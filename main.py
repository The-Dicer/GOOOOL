import os
import asyncio
import logging
from playwright.async_api import async_playwright
from scrapers.footballista import get_all_weekend_matches
from scrapers.graphics import prepare_graphics
from publishers.rutube import publish_stream
from publishers.footballista import add_video_link_to_match

logger = logging.getLogger(__name__)

async def fetch_matches_for_ui(debug_30_matches=False):
    logger.info("=== Запуск сбора матчей (Этап 1) ===")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        matches = await get_all_weekend_matches(context, debug_30_matches)
        if matches:
            matches.reverse()
        return matches


async def process_selected_matches(selected_matches, pattern_mode="Автовыбор", test_mode=True, league="AFL Moscow 8x8",
                                   default_color=3, desc_text="", stadium_colors=None):
    if stadium_colors is None:
        stadium_colors = {}
    #пасдлвы
    state_msg = "ВКЛЮЧЕН" if test_mode else "ВЫКЛЮЧЕН"
    logger.info(
        f"=== Запуск публикации | Тест: {state_msg} | Лига: {league} | Дефолтный цвет: {default_color} ===")

    # Задаем базовое имя
    base_name = "stream_keys"
    extension = ".txt"
    keys_file = f"{base_name}{extension}"

    # Перебираем цифры, пока не найдем свободное имя файла
    counter = 1
    while os.path.exists(keys_file):
        keys_file = f"{base_name}_{counter}{extension}"
        counter += 1

    # Открываем новый уникальный файл в режиме записи ("w")
    with open(keys_file, "w", encoding="utf-8") as f:
        f.write("=== КЛЮЧИ ТРАНСЛЯЦИЙ НА ЭТИ ВЫХОДНЫЕ ===\n\n")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        success_count = 0
        success_count = 0
        for i, match in enumerate(selected_matches, 1):
            logger.info(f"--- Обработка [{i}/{len(selected_matches)}]: {match.stream_title} ---")

            # --- ВНЕДРЕНИЕ СИСТЕМЫ АВТОПОВТОРА ---
            max_retries = 3  # Количество попыток на один матч
            for attempt in range(1, max_retries + 1):
                try:
                    # ПЕРЕДАЕМ СЛОВАРЬ ЦВЕТОВ В ГРАФИКУ
                    cover_path = await prepare_graphics(context, match, pattern_mode, league, default_color,
                                                        stadium_colors)

                    video_url = await publish_stream(context, match, cover_path, desc_text, keys_file)

                    if test_mode:
                        logger.info(
                            f"ТЕСТОВЫЙ РЕЖИМ: Ссылка {video_url} сохранена в txt. На Footballista не идем.")
                    else:
                        if video_url and match.match_url:
                            logger.info(f"БОЕВОЙ РЕЖИМ: Вставляем видео {video_url} на сайт Footballista...")
                            await add_video_link_to_match(context, match.match_url, video_url)
                        else:
                            logger.warning("Пропуск вставки: Rutube не вернул ссылку или у матча нет URL.")

                    success_count += 1
                    break  # УСПЕХ! Прерываем цикл попыток и идем к следующему матчу

                except Exception as e:
                    logger.error(f"Сбой при обработке (Попытка {attempt}/{max_retries}): {e}")
                    if attempt < max_retries:
                        logger.info("Rutube завис или выдал ошибку. Ждем 5 секунд и пробуем снова...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"Матч {match.stream_title} полностью пропущен из-за сбоев сайта.")
            # -------------------------------------

        logger.info(f"Пайплайн завершен. Успешно: {success_count} из {len(selected_matches)}.")
