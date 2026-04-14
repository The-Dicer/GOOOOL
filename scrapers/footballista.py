import re
import logging
from typing import List
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def get_all_weekend_matches(context) -> List[MatchMetadata]:
    logger.info("Ищем вкладку Footballista...")
    footballista_page = None

    for page in context.pages:
        if "footballista.ru" in page.url:
            footballista_page = page
            break

    if not footballista_page:
        raise Exception("Открой вкладку Footballista в браузере!")

    logger.info("Вкладка Footballista найдена. Собираем расписание на выходные...")
    await footballista_page.bring_to_front()

    matches = []
    try:
        # Ждем загрузки всех ссылок на матчи
        await footballista_page.wait_for_selector('a[href^="/admin/games/"]', state="visible", timeout=10000)

        # Получаем все карточки матчей на странице
        match_cards = await footballista_page.locator('a[href^="/admin/games/"]').all()

        # Переменные для отслеживания смены выходных
        seen_friday = False
        seen_saturday = False

        logger.info(f"Найдено {len(match_cards)} матчей на странице. Начинаем фильтрацию...")

        for card in match_cards:
            # Парсим сырую дату (например: "12 АПР. (ВС) 20:50")
            date_raw = await card.locator('div.date').inner_text()
            date_raw = date_raw.strip().upper()

            # Логика определения "прошлых выходных"
            # Если мы уже прошли Пятницу или Субботу, и снова видим Воскресенье - значит начались прошлые выходные
            if "(ПТ)" in date_raw:
                seen_friday = True
            elif "(СБ)" in date_raw:
                seen_saturday = True

            if "(ВС)" in date_raw and (seen_friday or seen_saturday):
                logger.info(f"Дошли до прошлых выходных ({date_raw}). Остановка сбора.")
                break  # Прерываем цикл, дальше идут старые матчи

            # Собираем данные матча (как раньше, но внутри цикла)
            champ = await card.locator('div.champ').inner_text()

            try:
                stadium_text = await card.locator("xpath=..").locator('.stadium').first.inner_text(timeout=1000)
                stadium = stadium_text.strip()
            except Exception:
                stadium = "Неизвестно"

            round_raw = await card.locator('div.round').inner_text()
            tour_number = int(re.search(r'\d+', round_raw).group())

            img_count = await card.locator('img').count()
            if img_count >= 2:
                team_home = await card.locator('img').nth(0).get_attribute('title')
                team_away = await card.locator('img').nth(1).get_attribute('title')
            else:
                full_text = await card.locator('div.name').inner_text()
                parts = re.split(r'\s+(?:\d+\s*-\s*\d+(?:\s*тп)?|-)\s+', full_text)
                if len(parts) >= 2:
                    team_home = parts[0]
                    team_away = parts[1]
                else:
                    logger.warning(f"Пропуск матча: не удалось разобрать команды ({full_text})")
                    continue

            # ДОБАВИТЬ ЭТОТ БЛОК: Получаем ссылку на матч
            href = await card.get_attribute("href")
            match_url = f"https://footballista.ru{href}"

            match_data = MatchMetadata(
                team_home=team_home.strip(),
                team_away=team_away.strip(),
                tournament_name=champ.strip(),
                tour_number=tour_number,
                match_date=date_raw,
                stadium=stadium,
                match_url=match_url  # ПЕРЕДАЕМ СЮДА
            )

            matches.append(match_data)
            logger.info(f"Добавлен в очередь: {date_raw} | {match_data.stream_title} | {stadium}")

        logger.info(f"Успешно собрано матчей на эти выходные: {len(matches)}")
        return matches

    except Exception as e:
        logger.error(f"Ошибка парсинга Footballista: {e}", exc_info=True)
        raise e
