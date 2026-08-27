import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


async def cleanup_rutube():
    """
    Безопасная очистка Rutube: удаляет ТОЛЬКО запланированные / ожидающие трансляции (stream_status == 'wait'),
    не затрагивая завершённые эфиры, записи и обычные видеоролики.
    """
    logger.info("Подключение к браузеру для безопасной очистки ожидающих трансляций...")
    p = await async_playwright().start()
    try:
        b = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = b.contexts[0]

        rutube_page = next((p for p in context.pages if "studio.rutube.ru" in p.url), None)
        if not rutube_page:
            rutube_page = await context.new_page()
            await rutube_page.goto("https://studio.rutube.ru/streams")
            await rutube_page.wait_for_load_state("domcontentloaded")
            await rutube_page.wait_for_timeout(1000)

        script = """async () => {
            function getCsrfToken() {
                const match = document.cookie.match(/csrftoken=([^;]+)/);
                return match ? match[1] : '';
            }
            const csrfToken = getCsrfToken();

            const headers = {
                'Accept': 'application/json, text/plain, */*',
                'X-CSRFToken': csrfToken
            };

            // Запрашиваем список трансляций и видео
            const res = await fetch('https://studio.rutube.ru/api/video/person/?sort_by=created_ts&order=desc&limit=100', {
                headers: headers,
                credentials: 'include'
            });

            if (!res.ok) {
                return { error: `HTTP ${res.status}: ${await res.text()}` };
            }

            const data = await res.json();
            const results = data.results || [];
            const deleted = [];
            const skipped = [];

            for (const item of results) {
                // ПРОВЕРКА БЕЗОПАСНОСТИ:
                // 1. Это должна быть прямая трансляция (is_livestream === true или stream_status есть)
                // 2. Статус должен быть ТОЛЬКО 'wait' (ожидает начала) или длительность 0 без флага завершения
                const isStream = Boolean(item.is_livestream || item.stream_status || item.is_live);
                const streamStatus = (item.stream_status || '').toLowerCase();
                const isWaiting = streamStatus === 'wait' || (isStream && (item.duration === 0 || !item.duration) && streamStatus !== 'complete' && streamStatus !== 'live');

                // Обычные видеоролики и завершенные трансляции НЕ трогаем
                if (!isStream || !isWaiting) {
                    skipped.push({
                        id: item.id,
                        title: item.title,
                        reason: !isStream ? 'Обычное видео (не стрим)' : `Статус стрима: ${streamStatus || 'завершен/активен'}`
                    });
                    continue;
                }

                // Удаляем ТОЛЬКО ожидающую трансляцию
                const delRes = await fetch(`https://studio.rutube.ru/api/video/${item.id}/`, {
                    method: 'DELETE',
                    headers: headers,
                    credentials: 'include'
                });

                deleted.push({
                    id: item.id,
                    title: item.title,
                    status: delRes.status
                });
            }

            return {
                total_found: results.length,
                deleted_count: deleted.length,
                skipped_count: skipped.length,
                deleted,
                skipped
            };
        }"""

        res = await rutube_page.evaluate(script)
        if not res or res.get("error"):
            logger.error(f"Ошибка при очистке: {res.get('error') if res else 'Нет ответа'}")
            return

        logger.info(f"Найдено объектов: {res.get('total_found', 0)}")
        logger.info(f"✅ Удалено ожидающих трансляций: {res.get('deleted_count', 0)}")
        for d in res.get("deleted", []):
            logger.info(f"  🗑️ Удален черновик: [{d.get('id')}] {d.get('title')}")

        logger.info(f"🛡️ Сохранено (пропущено) видео и завершенных эфиров: {res.get('skipped_count', 0)}")
        for s in res.get("skipped", []):
            logger.info(f"  🔒 Защищено: [{s.get('id')}] {s.get('title')} ({s.get('reason')})")

    except Exception as e:
        logger.error(f"Сбой при выполнении скрипта очистки: {e}")
    finally:
        await p.stop()


if __name__ == "__main__":
    asyncio.run(cleanup_rutube())

