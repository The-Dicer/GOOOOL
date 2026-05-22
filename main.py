import asyncio
import logging
from playwright.async_api import async_playwright
from scrapers.footballista import get_all_weekend_matches
from scrapers.graphics import prepare_graphics
from publishers.rutube import publish_stream
from publishers.footballista import add_video_link_to_match

logger = logging.getLogger(__name__)


async def fetch_matches_for_ui():
    logger.info("=== Запуск сбора матчей (Этап 1) ===")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        matches = await get_all_weekend_matches(context)
        if matches:
            matches.reverse()
        return matches


async def process_selected_matches(selected_matches, pattern_mode="Автовыбор", test_mode=True, league="AFL Moscow 8x8"):
    state_msg = "ВКЛЮЧЕН" if test_mode else "ВЫКЛЮЧЕН"
    logger.info(f"=== Запуск публикации | Тестовый режим: {state_msg} | Лига: {league} ===")

    keys_file = "stream_keys.txt"
    with open(keys_file, "w", encoding="utf-8") as f:
        f.write("=== КЛЮЧИ ТРАНСЛЯЦИЙ НА ЭТИ ВЫХОДНЫЕ ===\n\n")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        success_count = 0
        for i, match in enumerate(selected_matches, 1):
            logger.info(f"--- Обработка [{i}/{len(selected_matches)}]: {match.stream_title} ---")
            try:
                cover_path = await prepare_graphics(context, match, pattern_mode, league)

                video_url = await publish_stream(context, match, cover_path)

                if test_mode:
                    logger.info(
                        f"ТЕСТОВЫЙ РЕЖИМ: Ссылка {video_url} сохранена в txt. На Footballista не идем, пропускаем шаг.")
                else:
                    if video_url and match.match_url:
                        logger.info(f"БОЕВОЙ РЕЖИМ: Вставляем видео {video_url} на сайт Footballista...")
                        await add_video_link_to_match(context, match.match_url, video_url)
                    else:
                        logger.warning("Пропуск вставки: Rutube не вернул ссылку или у матча нет URL.")

                success_count += 1
            except Exception as e:
                logger.error(f"Ошибка при обработке '{match.stream_title}': {e}")

        logger.info(f"Пайплайн завершен. Успешно: {success_count} из {len(selected_matches)}.")