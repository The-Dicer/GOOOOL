import datetime
import os
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def prepare_graphics(context, match_data) -> str:
    logger.info("Ищем вкладку AFL Graphics...")
    graphics_page = None

    for page in context.pages:
        if "afl-graphics.vercel.app" in page.url:
            graphics_page = page
            break

    if not graphics_page:
        raise Exception("Открой вкладку AFL Graphics в браузере!")

    logger.info("Вкладка AFL Graphics найдена. Переключаемся...")
    await graphics_page.bring_to_front()

    try:
        # --- 1. ВЫБОР СЕЗОНА И ТУРНИРА ---
        target_tournament = match_data.tournament_name
        current_year = str(datetime.datetime.now().year)

        logger.info("Настраиваем лигу (Moscow 8x8)...")
        await graphics_page.get_by_role("searchbox", name="Select league").click()
        await graphics_page.get_by_role("option", name="AFL Moscow 8x8").click()
        await graphics_page.wait_for_timeout(1000)

        logger.info(f"Ищем сезон: {target_tournament} [{current_year}]")

        # Кликаем по иконке шеврона для выбора сезона
        logger.info("Кликаем по иконке шеврона для выбора сезона...")
        season_dropdown = graphics_page.locator(".mantine-Input-rightSection").nth(1)
        await season_dropdown.locator("svg").click()

        # Ждем появления модального окна глобально на всей странице
        modal_inner = graphics_page.locator(".mantine-Modal-inner")
        await modal_inner.wait_for(state="visible", timeout=8000)
        logger.debug("Модальное окно успешно открылось!")

        # Очищаем имя от спецсимволов для надежного поиска
        clean_name = target_tournament.replace('"', '').replace("'", "").strip()

        # Ищем все блоки турниров внутри открытого окна
        league_blocks = await modal_inner.locator(".IgrSeasonSelect_champ__r06TO").all()

        if not league_blocks:
            raise Exception("Модальное окно открылось, но список лиг пуст! Возможно, сайт не успел их загрузить.")

        found_league = None
        for block in league_blocks:
            block_text = await block.locator("div.IgrSeasonSelect_champName__BVzC6").inner_text()
            if clean_name.lower() in block_text.lower() or target_tournament.lower() in block_text.lower():
                found_league = block
                break

        if not found_league:
            available_leagues = [await b.locator("div.IgrSeasonSelect_champName__BVzC6").inner_text() for b in
                                 league_blocks]
            logger.error(f"Доступные лиги: {available_leagues}")
            raise Exception(f"Турнир '{target_tournament}' не найден!")

        # --- Выбираем последний (самый свежий) сезон ---
        logger.info("Выбираем самый актуальный (последний) сезон из списка...")
        year_buttons = found_league.locator("div.IgrSeasonSelect_season__AUXMG")

        # Дожидаемся, чтобы они появились (на всякий случай)
        await year_buttons.first.wait_for(state="visible", timeout=5000)

        # Получаем количество кнопок
        years_count = await year_buttons.count()
        if years_count == 0:
            raise Exception("Кнопки выбора сезона не найдены в блоке турнира!")

        # Кликаем по самой последней кнопке (index = count - 1)
        last_year_button = year_buttons.nth(years_count - 1)

        # Для логов можно получить текст кнопки (например, "2025/2026")
        season_text = await last_year_button.inner_text()
        logger.info(f"Актуальный сезон: {season_text}")

        await last_year_button.scroll_into_view_if_needed()
        await last_year_button.click()

        # Ждем, пока модалка полностью закроется
        await modal_inner.wait_for(state="hidden", timeout=5000)
        logger.info("Турнир успешно выбран.")
        await graphics_page.wait_for_timeout(1000)

        # --- 2. ВЫБОР Cover2 ---
        logger.info("Проверяем и выбираем тип графики (Cover2)...")

        # Находим все элементы, похожие на выпадающие списки (дропдауны)
        # Мы знаем, что выбор типа графики - это 3-й по счету дропдаун на странице
        # (1-й - Лига, 2-й - Сезон, 3-й - Тип графики, 4-й - Выбор игры)
        cover_wrapper = graphics_page.locator(".mantine-Input-wrapper").nth(2)
        cover_input = cover_wrapper.locator("input")

        # Даем странице немного времени после анимаций
        await graphics_page.wait_for_timeout(500)

        # Получаем текущее значение инпута
        current_cover = await cover_input.input_value()

        if current_cover == "Cover2":
            logger.info("Тип графики 'Cover2' уже выбран, пропускаем шаг.")
        else:
            logger.info(f"Текущий тип '{current_cover}', меняем на 'Cover2'...")

            # Прокручиваем инпут в зону видимости
            await cover_input.scroll_into_view_if_needed()

            # Находим иконку шеврона (стрелочку вниз) внутри этого же 3-го враппера
            cover_chevron = cover_wrapper.locator(".mantine-Input-rightSection")

            # Кликаем по шеврону, чтобы открыть меню
            await cover_chevron.click(force=True)
            await graphics_page.wait_for_timeout(500)

            # Выбираем опцию "Cover2"
            await graphics_page.get_by_role("option", name="Cover2", exact=True).click(force=True)
            await graphics_page.wait_for_timeout(1000)

        # --- 3. ВЫБОР ЦВЕТОВ ПО СТАДИОНУ ---
        logger.info(f"Выбираем цвета для стадиона: {match_data.stadium}...")
        await graphics_page.locator(".IgrSchemaSelect_container__lLhtL").click()
        await graphics_page.wait_for_timeout(500)

        stadium_lower = match_data.stadium.lower()
        color_position = 3  # Дефолтная карточка, если стадион не найден. Да, да я с труда

        # Название стадиона -> номер позиции (nth-child)
        if "труд" in stadium_lower:
            color_position = 3
        elif "ясенево" in stadium_lower:
            color_position = 9
        elif "терехово" in stadium_lower:
            color_position = 8
        elif "конструктор" in stadium_lower or "дело спорта" in stadium_lower:
            color_position = 19
        elif "тушино" in stadium_lower or "октябрь" in stadium_lower:
            color_position = 4

        logger.info(f"Выбрана позиция цвета: {color_position}")

        # Выбор карточки цвета по ее индексу
        await graphics_page.locator(f".mantine-Group-root > div:nth-child({color_position})").first.click()
        await graphics_page.wait_for_timeout(500)

        # Закрываем меню с помощью клавиши Escape
        logger.info("Закрываем меню выбора цветов...")
        await graphics_page.keyboard.press("Escape")
        await graphics_page.wait_for_timeout(1000)

        # --- 4. ПОИСК И ВЫБОР ИГРЫ ---
        logger.info(f"Ищем матч: {match_data.team_home} - {match_data.team_away}, {match_data.tour_number} round")

        game_input = graphics_page.get_by_role("searchbox", name="Select game")
        await game_input.click()
        # Вводим номер тура, чтобы отфильтровать список
        await game_input.fill(str(match_data.tour_number))
        await graphics_page.wait_for_timeout(2000)

        # Подготавливаем названия команд (убираем лишние пробелы, спецсимволы и аббревиатуры ТП)
        import re
        safe_home = re.sub(r'\s+', ' ', match_data.team_home.replace('ТП', '').strip())
        safe_away = re.sub(r'\s+', ' ', match_data.team_away.replace('ТП', '').strip())

        # Создаем регулярное выражение, которое ищет обе команды, игнорируя лишние пробелы и переносы между ними
        search_pattern = re.compile(f"{re.escape(safe_home)}.*{re.escape(safe_away)}", re.IGNORECASE)

        # Ищем опцию по регулярному выражению, а не по точной строке
        game_option = graphics_page.get_by_role("option", name=search_pattern)

        # Если найдено несколько вариантов (например, из-за похожего названия), берем первый
        game_option = game_option.first

        try:
            await game_option.wait_for(state="visible", timeout=5000)
            await game_option.click()
            logger.info("Матч успешно найден и выбран из списка!")
        except Exception:
            logger.warning(
                f"Умный поиск не нашел '{safe_home} - {safe_away}'. Пробуем искать только по первой команде...")
            # План Б: ищем только по домашней команде (обычно этого достаточно, так как мы уже отфильтровали по туру)
            fallback_pattern = re.compile(re.escape(safe_home), re.IGNORECASE)
            fallback_option = graphics_page.get_by_role("option", name=fallback_pattern).first
            await fallback_option.wait_for(state="visible", timeout=5000)
            await fallback_option.click()
            logger.info("Матч выбран по запасному варианту (только домашняя команда)!")

        await graphics_page.wait_for_timeout(1000)

        # --- 5. СКАЧИВАНИЕ КАРТИНКИ ---
        logger.info("Нажимаем 'DOWNLOAD IMAGE' и ждем файл...")
        download_dir = Path(os.getcwd()) / "covers"
        download_dir.mkdir(exist_ok=True)

        safe_home = match_data.team_home.replace(" ", "_").replace('"', "")
        safe_away = match_data.team_away.replace(" ", "_").replace('"', "")
        file_name = f"{safe_home}_{safe_away}_tour{match_data.tour_number}.png"
        download_path = download_dir / file_name

        async with graphics_page.expect_download(timeout=15000) as download_info:
            await graphics_page.get_by_role("button", name="DOWNLOAD IMAGE").click()

        download = await download_info.value
        await download.save_as(download_path)

        logger.info(f"Обложка успешно скачана: {download_path}")
        return str(download_path)


    except Exception as e:

        logger.error(f"Ошибка при генерации графики: {e}", exc_info=True)

        await graphics_page.screenshot(path="test_cover.png")

        logger.info("📸 Сохранен скриншот ошибки: test_cover.png")

        # --- ДОБАВЛЕНО: Очистка интерфейса при ошибке ---

        try:

            logger.info("Пытаемся закрыть всплывающие окна через Escape...")

            await graphics_page.keyboard.press("Escape")

            await graphics_page.wait_for_timeout(500)

            await graphics_page.keyboard.press("Escape")  # Дважды, на случай если открыто два меню

        except:

            pass

        raise e
