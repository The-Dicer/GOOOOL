import os
import base64
import logging
import asyncio
from typing import Optional
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def publish_stream(context, match_data: MatchMetadata, cover_path: str, description_text: str, keys_file: str) -> str:
    """
    Создание трансляции на Rutube Studio через прямой REST API v2.
    Работает за 1-2 секунды, точно извлекая RTMP-сервер и ключ трансляции.
    """
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

    logger.info(f"✅ Трансляция создана: {video_url}")
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
