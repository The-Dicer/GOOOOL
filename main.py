import asyncio
import logging
from playwright.async_api import async_playwright
from scrapers.footballista import get_all_weekend_matches
from scrapers.graphics import prepare_graphics
from publishers.rutube import publish_stream
from publishers.footballista import add_video_link_to_match

logger = logging.getLogger(__name__)


async def fetch_matches_for_ui():
    """ЭТАП 1: Подключаемся, собираем расписание и возвращаем список в GUI"""
    logger.info("=== Запуск сбора матчей (Этап 1) ===")

    async with async_playwright() as p:
        logger.info("Подключаемся к открытому браузеру (порт 9222)...")
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        matches = await get_all_weekend_matches(context)

        if matches:
            matches.reverse()
            logger.info("Список матчей развернут: публикуем в хронологическом порядке.")
        else:
            logger.warning("Матчи не найдены.")

        return matches


async def process_selected_matches(selected_matches, pattern_mode="Автовыбор"):
    """ЭТАП 2: Принимаем выбранные матчи из GUI и публикуем их"""
    logger.info(f"=== Запуск публикации для {len(selected_matches)} выбранных матчей ===")

    keys_file = "stream_keys.txt"
    with open(keys_file, "w", encoding="utf-8") as f:
        f.write("=== КЛЮЧИ ТРАНСЛЯЦИЙ НА ЭТИ ВЫХОДНЫЕ ===\n\n")
    logger.info(f"Файл {keys_file} успешно очищен перед новым запуском.")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        success_count = 0
        for i, match in enumerate(selected_matches, 1):
            logger.info(
                f"--- Обработка матча {i}/{len(selected_matches)}: {match.match_date} | {match.stream_title} ---")
            try:
                cover_path = await prepare_graphics(context, match, pattern_mode)
                video_url = await publish_stream(context, match, cover_path)

                if video_url and match.match_url:
                    await add_video_link_to_match(context, match.match_url, video_url)
                else:
                    logger.warning("Пропущено добавление ссылки: URL видео пустой или нет ссылки на матч.")

                success_count += 1
                logger.info(f"Матч {match.stream_title} полностью отработан!")
            except Exception as e:
                logger.error(f"Ошибка при обработке '{match.stream_title}': {e}")

        logger.info(f"Пайплайн завершен! Успешно создано трансляций: {success_count} из {len(selected_matches)}.")