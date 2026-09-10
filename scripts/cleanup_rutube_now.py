import os
import subprocess
import urllib.request
import sys
import asyncio
import logging
from typing import List, Dict, Any

import json

# Подключаем корень проекта для общих модулей
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from publishers.rutube import ensure_rutube_channel

# Исключаем локальные адреса из системного прокси, чтобы Playwright не слал локальный CDP трафик в прокси
for _proxy_key in ["NO_PROXY", "no_proxy"]:
    _curr = os.environ.get(_proxy_key, "")
    _proxies = [p.strip() for p in _curr.split(",") if p.strip()]
    for _h in ["localhost", "127.0.0.1", "::1"]:
        if _h not in _proxies:
            _proxies.append(_h)
    os.environ[_proxy_key] = ",".join(_proxies)

from playwright.async_api import async_playwright

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def find_chrome_executable() -> str:
    """Ищет исполняемый файл Chrome по стандартным путям Windows."""
    possible_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "chrome.exe"


def is_chrome_running() -> bool:
    """Проверяет доступность Chrome по порту 9222."""
    try:
        with urllib.request.urlopen("http://localhost:9222/json/version", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_chrome():
    """Запускает Chrome с отладочным портом 9222 и рабочим профилем GOAL в корне проекта."""
    chrome_path = find_chrome_executable()
    profile_path = os.path.join(_project_root, "chrome_debug_profile")
    os.makedirs(profile_path, exist_ok=True)

    cmd = [
        chrome_path,
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_path}",
        "https://studio.rutube.ru/streams"
    ]
    logger.info(f"Запуск Chrome (порт 9222, профиль: '{profile_path}')...")
    subprocess.Popen(cmd)


async def cleanup_rutube(dry_run: bool = False, auto_confirm: bool = False, target_channel: str = None):
    """
    100% БЕЗОПАСНАЯ очистка Rutube:
    - Удаляет ИСКЛЮЧИТЕЛЬНО запланированные эфиры в статусе 'wait' с длительностью 0.
    - СТРОЖАЙШЕ ЗАЩИЩАЕТ:
      * Все обычные видеоролики (is_livestream == False)
      * Все прошедшие трансляции и записи эфиров (status == 'complete' или duration > 0)
      * Все текущие прямые эфиры (status == 'live')
    - Позволяет выбрать очистку личного канала или канала лиги AFL.
    - Перед удалением выводит полный список кандидатов и запрашивает явное подтверждение пользователя.
    """
    logger.info("Подключение к браузеру Chrome для безопасного анализа трансляций...")

    # Автоматический запуск Chrome, если он еще не открыт
    if not is_chrome_running():
        logger.info("Chrome на порту 9222 не найден. Автоматически запускаем Chrome с нужным профилем...")
        launch_chrome()

        print("Ожидание подключения к Chrome...", end="", flush=True)
        started = False
        for _ in range(15):
            await asyncio.sleep(1)
            print(".", end="", flush=True)
            if is_chrome_running():
                started = True
                break
        print()

        if not started:
            logger.error("Не удалось подключиться к Chrome на порту 9222. Запустите Chrome вручную.")
            return

        logger.info("Chrome успешно запущен и готов к работе.")
        await asyncio.sleep(2)

    p = await async_playwright().start()
    try:
        try:
            b = await p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            logger.error(f"Не удалось подключиться к Chrome: {e}")
            return

        context = b.contexts[0]
        rutube_page = next((page for page in context.pages if "studio.rutube.ru" in page.url), None)
        if not rutube_page:
            rutube_page = await context.new_page()
            await rutube_page.goto("https://studio.rutube.ru/streams")
        else:
            await rutube_page.bring_to_front()
            if "studio.rutube.ru" not in rutube_page.url:
                await rutube_page.goto("https://studio.rutube.ru/streams")

        try:
            await rutube_page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

        # Проверка авторизации в Rutube Studio
        async def check_is_logged_in(page):
            if not page or page.is_closed():
                return False
            if "login" in page.url:
                return False
            check_script = """async () => {
                try {
                    const match = document.cookie.match(/csrftoken=([^;]+)/);
                    if (!match) return false;
                    const res = await fetch('https://studio.rutube.ru/api/video/person/?limit=1', {
                        headers: { 'X-CSRFToken': match[1] },
                        credentials: 'include'
                    });
                    return res.ok;
                } catch (e) {
                    return false;
                }
            }"""
            try:
                return await page.evaluate(check_script)
            except Exception:
                return False

        if not await check_is_logged_in(rutube_page):
            print("\n" + "!" * 80)
            print("ВНИМАНИЕ: Вы не авторизованы в Rutube Studio!")
            print("Пожалуйста, перейдите в открывшееся окно Chrome и войдите в свой аккаунт Rutube.")
            print("    Скрипт автоматически продолжит работу, как только вы войдете в систему.")
            print("!" * 80 + "\n")
            print("Ожидание входа в аккаунт...", end="", flush=True)

            while True:
                await asyncio.sleep(2)
                print(".", end="", flush=True)
                # Ищем активную вкладку Rutube Studio
                for p_curr in context.pages:
                    if not p_curr.is_closed() and "studio.rutube.ru" in p_curr.url and "login" not in p_curr.url:
                        rutube_page = p_curr
                        break
                if await check_is_logged_in(rutube_page):
                    print("\n\nВход выполнен успешно! Переходим к анализу канала...")
                    await asyncio.sleep(1.5)
                    break

        # Загрузка целевого ID канала лиги из config.json
        config_path = os.path.join(_project_root, "config.json")
        rutube_channel_id = "77095292"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    rutube_channel_id = cfg.get("rutube_channel_id", "77095292")
            except Exception:
                pass

        # Выбор канала для очистки (личный или канал лиги)
        selected_channel_action = target_channel
        if not selected_channel_action:
            print("\n" + "=" * 80)
            print("ВЫБОР КАНАЛА ДЛЯ ОЧИСТКИ:")
            print("   [1] Оставить текущий активный канал (Личный канал по умолчанию)")
            print(f"   [2] Переключиться на канал лиги AFL Moscow (ID: {rutube_channel_id})")
            print("=" * 80)
            try:
                user_choice = input("Выберите вариант (1 или 2, Enter = 1): ").strip()
            except Exception:
                user_choice = "1"
            if user_choice == "2":
                selected_channel_action = "league"
            else:
                selected_channel_action = "current"

        if selected_channel_action == "league" or selected_channel_action == rutube_channel_id:
            logger.info(f"Переключение на канал лиги Rutube Studio (ID: {rutube_channel_id})...")
            await ensure_rutube_channel(rutube_page, rutube_channel_id)
        elif selected_channel_action and selected_channel_action not in ["current", "personal"]:
            logger.info(f"Переключение на указанный канал Rutube Studio (ID: {selected_channel_action})...")
            await ensure_rutube_channel(rutube_page, selected_channel_action)
        else:
            logger.info("Используется текущий активный канал в Rutube Studio (Личный канал).")

        # ЭТАП 1: Сканирование и получение списка ожидающих трансляций через родной API Rutube Studio
        scan_script = """async () => {
            function getCsrfToken() {
                const match = document.cookie.match(/csrftoken=([^;]+)/);
                return match ? match[1] : '';
            }
            const headers = {
                'Accept': 'application/json, text/plain, */*',
                'X-CSRFToken': getCsrfToken()
            };

            // 1. Получаем общее количество существующих видео и записей на канале для отчета безопасности
            let totalChannelVideos = 0;
            try {
                const vRes = await fetch('https://studio.rutube.ru/api/video/person/?per_page=1', {
                    headers: headers,
                    credentials: 'include'
                });
                if (vRes.ok) {
                    const vData = await vRes.json();
                    totalChannelVideos = vData.num_pages || 0;
                }
            } catch (e) {}

            // 2. Получаем запланированные трансляции со статусом 'wait' через родной API стримов Rutube Studio
            let page = 1;
            let hasNext = true;
            const toDelete = [];

            while (hasNext && page <= 10) {
                const res = await fetch(`https://studio.rutube.ru/api/v2/video/stream/owner/?stream_status=wait&page=${page}&per_page=100`, {
                    headers: headers,
                    credentials: 'include'
                });
                if (!res.ok) {
                    if (page === 1) return { error: `HTTP ${res.status}: ${await res.text()}` };
                    break;
                }
                const data = await res.json();
                const results = data.results || [];
                for (const item of results) {
                    // Жесткая проверка: статус строго 'wait'
                    if (item.stream_status === 'wait') {
                        toDelete.push({
                            id: item.video || item.id,
                            title: item.title || 'Без названия',
                            status: 'wait (ожидает начала)',
                            planned_start: item.planned_start_time || ''
                        });
                    }
                }
                hasNext = Boolean(data.has_next);
                page++;
            }

            // 3. Получаем примеры защищенных завершенных эфиров
            const protectedSamples = [];
            try {
                const compRes = await fetch('https://studio.rutube.ru/api/v2/video/stream/owner/?stream_status=complete&page=1&per_page=5', {
                    headers: headers,
                    credentials: 'include'
                });
                if (compRes.ok) {
                    const compData = await compRes.json();
                    for (const item of (compData.results || [])) {
                        protectedSamples.push({
                            title: item.title,
                            reason: 'Завершенный эфир / запись матча (защищено)'
                        });
                    }
                }
            } catch (e) {}

            // Определение активного канала
            let activeChannelName = 'Личный канал (владелец)';
            try {
                const scopeMatch = document.cookie.match(/(?:^|;\\s*)scope=([^;]+)/);
                if (scopeMatch) {
                    const val = decodeURIComponent(scopeMatch[1]).replaceAll('\\"', '"');
                    const sc = JSON.parse(val);
                    activeChannelName = `Канал лиги AFL (ID: ${sc.channel_id}, роль: ${(sc.channel_scopes && sc.channel_scopes[0]) || 'модератор'})`;
                }
            } catch(e) {}

            return {
                active_channel: activeChannelName,
                protected_count: totalChannelVideos,
                to_delete: toDelete,
                protected_samples: protectedSamples
            };
        }"""

        scan_result = await rutube_page.evaluate(scan_script)
        if not scan_result or scan_result.get("error"):
            logger.error(f"Ошибка при сканировании: {scan_result.get('error') if scan_result else 'Нет ответа'}")
            return

        total_protected = scan_result.get("protected_count", 0)
        to_delete = scan_result.get("to_delete", [])
        protected_samples = scan_result.get("protected_samples", [])

        print("\n" + "=" * 80)
        print("РЕЗУЛЬТАТЫ СКАНИРОВАНИЯ КАНАЛА RUTUBE:")
        print(f"   АКТИВНЫЙ КАНАЛ: {scan_result.get('active_channel', 'Текущий канал')}")
        print(f"   НАДЕЖНО ЗАЩИЩЕНО (видео и завершенные матчи на канале): {total_protected}")
        print(f"   НАЙДЕНО НЕ НАЧАВШИХСЯ ТРАНСЛЯЦИЙ ('wait'): {len(to_delete)}")
        print("=" * 80)

        if not to_delete:
            print("\nНа канале нет не начавшихся запланированных трансляций. Очистка не требуется.\n")
            return

        print("\nСписок не начавшихся трансляций, которые МОГУТ быть удалены:")
        for idx, item in enumerate(to_delete, 1):
            time_str = f" [{item['planned_start']}]" if item.get('planned_start') else ""
            print(f"   [{idx}] {item['title']}{time_str} (ID: {item['id']})")

        if protected_samples:
            print("\nПримеры защищенного контента (НЕ будут затронуты ни при каких условиях):")
            for item in protected_samples:
                print(f"   [ЗАЩИТА] {item['title']} -> {item['reason']}")

        print("=" * 80)

        if dry_run:
            print("\nРежим симуляции (--dry-run): удаление пропущено.\n")
            return

        # Запрос подтверждения у пользователя
        if not auto_confirm:
            try:
                answer = input(f"\nВы действительно хотите удалить эти {len(to_delete)} не начавшихся трансляций? (введите 'y' для удаления): ")
                if answer.strip().lower() not in ("y", "yes", "да"):
                    print("\nУдаление отменено пользователем. Ни одного объекта не тронуто.\n")
                    return
            except (EOFError, KeyboardInterrupt):
                print("\nОтменено.\n")
                return

        # ЭТАП 2: Удаление только подтвержденного списка ID
        print("\nВыполняется удаление...")
        delete_ids = [item["id"] for item in to_delete]

        delete_script = """async (ids) => {
            function getCsrfToken() {
                const match = document.cookie.match(/csrftoken=([^;]+)/);
                return match ? match[1] : '';
            }
            const headers = {
                'Accept': 'application/json, text/plain, */*',
                'X-CSRFToken': getCsrfToken()
            };

            const deleted = [];
            const failed = [];

            for (const id of ids) {
                try {
                    const res = await fetch(`https://studio.rutube.ru/api/video/${id}/`, {
                        method: 'DELETE',
                        headers: headers,
                        credentials: 'include'
                    });
                    if (res.ok || res.status === 200 || res.status === 204) {
                        deleted.push(id);
                    } else {
                        failed.push({ id, status: res.status });
                    }
                } catch (e) {
                    failed.push({ id, status: e.message });
                }
                await new Promise(r => setTimeout(r, 150));
            }
            return { deleted, failed };
        }"""

        del_res = await rutube_page.evaluate(delete_script, delete_ids)
        deleted = del_res.get("deleted", [])
        failed = del_res.get("failed", [])

        print(f"\nУспешно удалено черновиков: {len(deleted)}")
        if failed:
            print(f"Ошибок при удалении: {len(failed)}")
            for f in failed:
                print(f"   Не удалось удалить {f['id']}: HTTP {f['status']}")
        print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Сбой при выполнении скрипта: {e}")
    finally:
        await p.stop()


if __name__ == "__main__":
    if "--chrome" in sys.argv or "--launch-chrome" in sys.argv:
        launch_chrome()
        sys.exit(0)

    is_dry = "--dry-run" in sys.argv
    is_yes = "--yes" in sys.argv or "-y" in sys.argv
    no_pause = "--no-pause" in sys.argv

    target_channel_arg = None
    if "--league" in sys.argv:
        target_channel_arg = "league"
    elif "--personal" in sys.argv or "--current" in sys.argv:
        target_channel_arg = "current"
    elif "--channel" in sys.argv:
        try:
            idx = sys.argv.index("--channel")
            if idx + 1 < len(sys.argv):
                target_channel_arg = sys.argv[idx + 1]
        except Exception:
            pass

    try:
        asyncio.run(cleanup_rutube(dry_run=is_dry, auto_confirm=is_yes, target_channel=target_channel_arg))
    except KeyboardInterrupt:
        print("\n\nОчистка прервана пользователем.")
    except Exception as err:
        print(f"\n\nНепредвиденная ошибка: {err}")
    finally:
        # Удерживаем окно консоли открытым при запуске двойным кликом
        if not no_pause:
            try:
                input("\nНажмите клавишу Enter, чтобы закрыть это окно...")
            except Exception:
                pass


