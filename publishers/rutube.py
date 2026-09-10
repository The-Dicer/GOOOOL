import os
import base64
import logging
import asyncio
from typing import Optional
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def ensure_rutube_channel(page, target_channel_id: str = "77095292") -> dict:
    """
    Проверяет активный канал в Rutube Studio и при необходимости переключает его
    на канал лиги (например, 77095292), если оператор является модератором,
    а открыт личный канал.
    """
    if not target_channel_id:
        return {"status": "skipped", "reason": "no_target_channel_id"}

    target_id_str = str(target_channel_id).strip()

    # Скрипт проверки и переключения канала
    switch_script = """async (targetId) => {
        function getCsrfToken() {
            const match = document.cookie.match(/csrftoken=([^;]+)/);
            return match ? match[1] : '';
        }

        function getCookieScope() {
            try {
                const match = document.cookie.match(/(?:^|;\\s*)scope=([^;]+)/);
                if (!match) return null;
                let val = decodeURIComponent(match[1]).replaceAll('\\\\"', '"');
                return JSON.parse(val);
            } catch (e) {
                return null;
            }
        }

        const csrfToken = getCsrfToken();
        const headers = {
            'Accept': 'application/json, text/plain, */*',
            'X-CSRFToken': csrfToken
        };

        // 1. Проверяем, не активен ли уже целевой канал
        const scope = getCookieScope();
        if (scope && String(scope.channel_id) === String(targetId)) {
            return {
                already_active: true,
                channel_id: targetId,
                role: (scope.channel_scopes && scope.channel_scopes[0]) || 'moderator'
            };
        }

        // 2. Запрашиваем список доступных каналов и ролей пользователя
        let channels = [];
        try {
            const resUser = await fetch('https://studio.rutube.ru/api/permissions/user/', {
                headers: headers,
                credentials: 'include'
            });
            if (resUser.ok) {
                const data = await resUser.json();
                channels = data.channels || [];
            }
        } catch (e) {}

        const targetChannel = channels.find(c => String(c.channel_id) === String(targetId));

        // 3. Вызываем API переключения канала permissions/channel/{targetId}/
        let apiSwitched = false;
        let switchError = null;
        try {
            let resSwitch = await fetch(`https://studio.rutube.ru/api/permissions/channel/${targetId}/`, {
                method: 'GET',
                headers: headers,
                credentials: 'include'
            });
            if (!resSwitch.ok && resSwitch.status !== 304) {
                resSwitch = await fetch(`https://studio.rutube.ru/api/permissions/channel/${targetId}/`, {
                    method: 'POST',
                    headers: headers,
                    credentials: 'include'
                });
            }
            if (resSwitch.ok || resSwitch.status === 304) {
                apiSwitched = true;
            } else {
                switchError = `HTTP ${resSwitch.status}`;
            }
        } catch (e) {
            switchError = String(e);
        }

        // Проверяем cookie после запроса
        const scopeAfter = getCookieScope();
        const verifiedActive = scopeAfter && String(scopeAfter.channel_id) === String(targetId);

        return {
            already_active: false,
            api_switched: apiSwitched,
            verified_active: verifiedActive,
            switch_error: switchError,
            target_found: !!targetChannel,
            channel_name: targetChannel ? (targetChannel.channel_name || targetChannel.name) : null,
            role: targetChannel ? targetChannel.permission : ((scopeAfter && scopeAfter.channel_scopes && scopeAfter.channel_scopes[0]) || null),
            available_channels: channels.map(c => ({ id: String(c.channel_id), name: c.channel_name || c.name, role: c.permission }))
        };
    }"""

    try:
        res = await page.evaluate(switch_script, target_id_str)
    except Exception as e:
        logger.warning(f"Ошибка при проверке/переключении канала через API: {e}")
        res = None

    if res and res.get("already_active"):
        logger.info(f"Целевой канал Rutube {target_id_str} уже активен (роль: {res.get('role', 'модератор')}).")
        return res

    if res and res.get("target_found"):
        ch_name = res.get("channel_name") or target_id_str
        ch_role = res.get("role") or "модератор"
        logger.info(f"Найдено соответствие в списке каналов: '{ch_name}' (ID: {target_id_str}, роль: {ch_role})")

    # Если переключили через API, обновляем страницу Rutube Studio для применения контекста канала
    if res and (res.get("api_switched") or res.get("verified_active")):
        logger.info(f"Канал Rutube переключен на ID {target_id_str}. Перезагрузка вкладки студии...")
        try:
            await page.goto("https://studio.rutube.ru/streams")
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning(f"Предупреждение при перезагрузке страницы студии: {e}")
        return res

    # UI-fallback: если API не сработал или cookie не подтвердился, пробуем переключить через меню интерфейса
    logger.info("Попытка переключения канала через графический интерфейс Rutube Studio...")
    try:
        avatar_btn = page.locator("button[class*='avatarButton__vl-header-user-menu'], div[class*='avatarButton__vl-header-user-menu'], button:has([class*='avatar'])").first
        if await avatar_btn.is_visible(timeout=3000):
            await avatar_btn.click()
            await page.wait_for_timeout(800)

            channel_card = page.locator(f"div[class*='card__vl-header-user-roles']:has-text('{target_id_str}')").first
            if not await channel_card.is_visible(timeout=1500):
                target_name = (res and res.get("channel_name")) or "AFL"
                channel_card = page.locator(f"div[class*='card__vl-header-user-roles']:has-text('{target_name}')").first

            if await channel_card.is_visible(timeout=2000):
                logger.info("Клик по карточке канала в выпадающем меню...")
                await channel_card.click()
                await page.wait_for_timeout(1500)
                await page.goto("https://studio.rutube.ru/streams")
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                logger.info("Переключение канала через интерфейс выполнено.")
                return {"status": "ui_switched", "channel_id": target_id_str}
    except Exception as e:
        logger.warning(f"UI-fallback переключения канала не удался: {e}")

    return res or {"status": "unknown"}


async def publish_stream(context, match_data: MatchMetadata, cover_path: str, description_text: str, keys_file: str, rutube_channel_id: str = "77095292", test_mode: bool = False) -> str:
    """
    Создание трансляции на Rutube Studio через прямой REST API v2.
    Работает за 1-2 секунды, точно извлекая RTMP-сервер и ключ трансляции.
    """
    if test_mode:
        logger.info("[ТЕСТОВЫЙ РЕЖИМ] Переключение канала Rutube отключено (публикация в текущий тестовый канал).")
        rutube_channel_id = None

    logger.info("Подготовка страницы Rutube Studio для работы по API...")

    page = None
    for p in context.pages:
        if "studio.rutube.ru" in p.url:
            page = p
            break

    if not page:
        page = await context.new_page()
        await page.goto("https://studio.rutube.ru/streams")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)

    # Проверка и переключение на канал лиги (только в боевом режиме при наличии ID)
    if not test_mode and rutube_channel_id:
        await ensure_rutube_channel(page, rutube_channel_id)

    # Читаем обложку в base64 (если есть)
    cover_base64 = None
    cover_filename = None
    if cover_path and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            cover_base64 = base64.b64encode(f.read()).decode("utf-8")
        cover_filename = os.path.basename(cover_path)

    api_script = """async (params) => {
        function getCsrfToken() {
            const match = document.cookie.match(/csrftoken=([^;]+)/);
            return match ? match[1] : '';
        }

        const csrfToken = getCsrfToken();
        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'X-CSRFToken': csrfToken
        };

        // 1. Создание черновика стрима
        const createPayload = {
            stream_status: "wait",
            title: params.title,
            description: params.description || '',
            category: 16,
            is_adult: false,
            is_hidden: false
        };

        const createRes = await fetch('https://studio.rutube.ru/api/v2/video/create/stream/', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(createPayload)
        });

        if (!createRes.ok) {
            const err = await createRes.text();
            return { error: `Ошибка создания стрима: HTTP ${createRes.status}: ${err}` };
        }

        const createData = await createRes.json();
        const videoId = createData.video || createData.id;
        if (!videoId) {
            return { error: 'Не получен ID созданного стрима' };
        }

        // 2. Включение push_auto_start и сохранение параметров
        const updatePayload = {
            title: params.title,
            category: 16,
            description: params.description || '',
            hide_chat: false,
            push_auto_start: true,
            is_donate_allowed: false,
            is_adult: false,
            is_hidden: false,
            is_chat_saved: true
        };

        await fetch(`https://studio.rutube.ru/api/v2/video/stream/${videoId}/`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(updatePayload)
        });

        // 3. Загрузка обложки
        if (params.coverBase64) {
            try {
                const byteCharacters = atob(params.coverBase64);
                const byteNumbers = new Array(byteCharacters.length);
                for (let i = 0; i < byteCharacters.length; i++) {
                    byteNumbers[i] = byteCharacters.charCodeAt(i);
                }
                const byteArray = new Uint8Array(byteNumbers);
                const blob = new Blob([byteArray], { type: 'image/png' });

                const formData = new FormData();
                formData.append('file', blob, params.coverFilename || 'cover.png');

                await fetch(`https://studio.rutube.ru/api/video/${videoId}/thumbnail/?client=vulp`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrfToken },
                    body: formData
                });
            } catch (e) {}
        }

        // 4. Получение RTMP сервера и ключа трансляции
        let streamKey = '';
        let serverUrl = 'rtmp://live.rutube.ru/live_push';
        try {
            const streamInfoRes = await fetch(`https://studio.rutube.ru/api/v2/video/stream/${videoId}/`, {
                headers: { 'X-CSRFToken': csrfToken }
            });
            if (streamInfoRes.ok) {
                const streamInfo = await streamInfoRes.json();
                streamKey = streamInfo.input_key_gen || streamInfo.stream_key || '';
                if (streamInfo.input_servers) {
                    serverUrl = streamInfo.input_servers.primary || streamInfo.input_servers.secondary || serverUrl;
                } else if (streamInfo.server_url) {
                    serverUrl = streamInfo.server_url;
                }
            }
        } catch (e) {}

        const videoUrl = `https://rutube.ru/video/${videoId}/`;

        return {
            success: true,
            videoId: videoId,
            videoUrl: videoUrl,
            serverUrl: serverUrl,
            streamKey: streamKey
        };
    }"""

    result = await page.evaluate(api_script, {
        "title": match_data.stream_title,
        "description": description_text,
        "coverBase64": cover_base64,
        "coverFilename": cover_filename
    })

    if not result or result.get("error"):
        err = result.get("error") if result else "Неизвестная ошибка"
        raise RuntimeError(f"Сбой Rutube API: {err}")

    video_url = result.get("videoUrl", "")
    server_url = result.get("serverUrl", "")
    stream_key = result.get("streamKey", "")

    logger.info(f"Трансляция создана: {video_url}")
    logger.info(f"Ключ трансляции получен: {stream_key[:15]}... | Сервер: {server_url}")

    # Запись в файл stream_keys
    with open(keys_file, "a", encoding="utf-8") as f:
        f.write(f"Матч: {match_data.stream_title}\n")
        f.write(f"URL видео: {video_url}\n")
        f.write(f"Сервер: {server_url}\n")
        f.write(f"Ключ: {stream_key}\n")
        f.write(f"Лого хозяев: {match_data.logo_home}\n")
        f.write(f"Лого гостей: {match_data.logo_away}\n")
        f.write(f"Сокр. хозяев: {match_data.abbr_home}\n")
        f.write(f"Сокр. гостей: {match_data.abbr_away}\n")
        f.write("-" * 50 + "\n")

    logger.info(f"Данные успешно записаны в файл: {keys_file}")
    return video_url
