import asyncio
import logging
from playwright.async_api import async_playwright
from scrapers.footballista import get_all_weekend_matches
from scrapers.graphics import prepare_graphics
from publishers.rutube import publish_stream
from publishers.footballista import add_video_link_to_match

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(module)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("pipeline.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("=== Запуск полного пайплайна (Сбор -> Графика -> Rutube -> Footballista) ===")

    # --- ДОБАВИТЬ ЭТОТ БЛОК: Очистка файла с ключами ---
    keys_file = "stream_keys.txt"
    with open(keys_file, "w", encoding="utf-8") as f:
        # Режим "w" (write) перезапишет файл с нуля, удалив старые данные
        f.write("=== КЛЮЧИ ТРАНСЛЯЦИЙ НА ЭТИ ВЫХОДНЫЕ ===\n\n")
    logger.info(f"🧹 Файл {keys_file} успешно очищен перед новым запуском.")
    # ----------------------------------------------------

    async with async_playwright() as p:
        try:
            logger.info("Подключаемся к открытому браузеру (порт 9222)...")
            # ... дальше идет твой старый код ...
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]

            # ШАГ 1: Сбор ВСЕХ матчей за выходные
            matches = await get_all_weekend_matches(context)

            if not matches:
                logger.warning("Матчи не найдены, пайплайн завершен.")
                return

            # ШАГ 2: Хронологическая сортировка
            # Footballista отдает матчи сверху вниз (от вечера ВСК к утру ПТ).
            # Разворачиваем список, чтобы публиковать их в порядке времени (ПТ -> СБ -> ВС).
            matches.reverse()
            logger.info("Список матчей развернут: публикуем в хронологическом порядке.")

            # ШАГ 3: Генерация графики и публикация в цикле
            logger.info(f"Начинаем обработку {len(matches)} матчей...")

            success_count = 0
            for i, match in enumerate(matches, 1):
                logger.info(f"--- Обработка матча {i}/{len(matches)}: {match.match_date} | {match.stream_title} ---")
                try:
                    # 1. Генерируем обложку
                    cover_path = await prepare_graphics(context, match)

                    # 2. Публикуем на Rutube (ТЕПЕРЬ ПОЛУЧАЕМ ССЫЛКУ)
                    video_url = await publish_stream(context, match, cover_path)

                    # 3. Вставляем ссылку на Footballista (если она успешно скопировалась)
                    if video_url and match.match_url:
                        await add_video_link_to_match(context, match.match_url, video_url)
                    else:
                        logger.warning("Пропущено добавление ссылки: URL видео пустой или нет ссылки на матч.")

                    success_count += 1
                    logger.info(f"✅ Матч {match.stream_title} полностью отработан!")
                except Exception as e:
                    logger.error(f"❌ Ошибка при обработке '{match.stream_title}': {e}")

            logger.info(f"🎉 Пайплайн завершен! Успешно создано трансляций: {success_count} из {len(matches)}.")

        except Exception as e:
            logger.error(f"❌ Критическая ошибка оркестратора: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
