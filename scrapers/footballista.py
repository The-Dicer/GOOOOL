import re
import logging
from typing import List, Dict
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def enrich_matches_from_compact_view(context, matches: List[MatchMetadata]) -> List[MatchMetadata]:
    logger.info("Открываем дополнительную вкладку Footballista в компактном режиме для логотипов и сокращений...")

    compact_page = await context.new_page()
    try:
        await compact_page.set_viewport_size({"width": 900, "height": 900})
        await compact_page.goto("https://footballista.ru/admin/games")
        await compact_page.wait_for_load_state("domcontentloaded")
        await compact_page.evaluate("document.body.style.zoom = '150%'")
        await compact_page.wait_for_timeout(2500)

        logos_count = await compact_page.locator('a[href^="/admin/games/"] img[src*="logos"]').count()
        logger.info(f"Компактный режим: найдено логотипов на странице = {logos_count}")

        compact_cards = await compact_page.locator('a[href^="/admin/games/"]').all()
        compact_map: Dict[str, dict] = {}

        for card in compact_cards:
            href = await card.get_attribute("href")
            if not href:
                continue

            full_match_url = f"https://footballista.ru{href}"
            imgs = card.locator("img")
            img_count = await imgs.count()

            logo_home = "Нет логотипа"
            logo_away = "Нет логотипа"
            abbr_home = ""
            abbr_away = ""

            if img_count >= 2:
                raw_logo_home = await imgs.nth(0).get_attribute("src")
                raw_logo_away = await imgs.nth(1).get_attribute("src")

                if raw_logo_home:
                    if raw_logo_home.startswith("/"):
                        raw_logo_home = f"https://footballista.ru{raw_logo_home}"
                    logo_home = raw_logo_home.replace("-min", "-max")

                if raw_logo_away:
                    if raw_logo_away.startswith("/"):
                        raw_logo_away = f"https://footballista.ru{raw_logo_away}"
                    logo_away = raw_logo_away.replace("-min", "-max")

                name_text = await card.locator("div.name").inner_text()
                name_text = name_text.replace("\n", " ").replace("\r", " ")
                name_text = re.sub(r"\s+", "", name_text).strip().upper()

                logger.info(f"[compact] name_text={name_text}")

                short_match = re.search(r"([A-ZА-Я0-9]{2,8})-([A-ZА-Я0-9]{2,8})", name_text)
                if short_match:
                    abbr_home = short_match.group(1)
                    abbr_away = short_match.group(2)

            compact_map[full_match_url] = {
                "logo_home": logo_home,
                "logo_away": logo_away,
                "abbr_home": abbr_home,
                "abbr_away": abbr_away,
            }

        enriched = []
        for match in matches:
            extra = compact_map.get(match.match_url)
            if extra:
                match.logo_home = extra["logo_home"]
                match.logo_away = extra["logo_away"]
                match.abbr_home = extra["abbr_home"]
                match.abbr_away = extra["abbr_away"]
                logger.info(
                    f"Обогащено: {match.stream_title} | {match.abbr_home}-{match.abbr_away} | "
                    f"logo_home={match.logo_home != 'Нет логотипа'} | logo_away={match.logo_away != 'Нет логотипа'}"
                )
            enriched.append(match)

        return enriched
    finally:
        await compact_page.close()


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
        await footballista_page.wait_for_selector('a[href^="/admin/games/"]', state="visible", timeout=10000)
        match_cards = await footballista_page.locator('a[href^="/admin/games/"]').all()
        weekend_days_map = {}

        logger.info(f"Найдено {len(match_cards)} матчей на странице. Начинаем фильтрацию...")

        for card in match_cards:
            date_raw = await card.locator('div.date').inner_text()
            date_raw = date_raw.strip().upper()
            date_str = date_raw.split('(')[0].strip()

            day_of_week = None
            if "(ПТ)" in date_raw:
                day_of_week = "ПТ"
            elif "(СБ)" in date_raw:
                day_of_week = "СБ"
            elif "(ВС)" in date_raw:
                day_of_week = "ВС"

            if day_of_week:
                if day_of_week == "ВС" and ("СБ" in weekend_days_map or "ПТ" in weekend_days_map):
                    logger.info(f"Дошли до прошлых выходных ({date_raw}). Остановка сбора.")
                    break
                if day_of_week == "СБ" and "ПТ" in weekend_days_map:
                    logger.info(f"Дошли до прошлых выходных ({date_raw}). Остановка сбора.")
                    break
                if day_of_week in weekend_days_map and weekend_days_map[day_of_week] != date_str:
                    logger.info(f"Обнаружен скачок в неделю ({date_raw}). Остановка сбора.")
                    break
                weekend_days_map[day_of_week] = date_str
            else:
                logger.info(f"Встретили будний день ({date_raw}). Остановка сбора.")
                break

            champ = await card.locator('div.champ').inner_text()

            try:
                stadium_text = await card.locator("xpath=..").locator('.stadium').first.inner_text(timeout=1000)
                stadium = stadium_text.strip()
            except Exception:
                stadium = "Неизвестно"

            round_raw = await card.locator('div.round').inner_text()
            tour_number = int(re.search(r'\d+', round_raw).group())

            img_count = await card.locator('img').count()
            logo_home_url = "Нет логотипа"
            logo_away_url = "Нет логотипа"

            if img_count >= 2:
                team_home = await card.locator('img').nth(0).get_attribute('title')
                team_away = await card.locator('img').nth(1).get_attribute('title')
                raw_logo_home = await card.locator('img').nth(0).get_attribute('src')
                raw_logo_away = await card.locator('img').nth(1).get_attribute('src')

                if raw_logo_home and not raw_logo_home.startswith('http'):
                    raw_logo_home = f"https://footballista.ru{raw_logo_home}"
                if raw_logo_home and '-min' in raw_logo_home:
                    logo_home_url = raw_logo_home.replace('-min', '-max')
                elif raw_logo_home:
                    logo_home_url = raw_logo_home

                if raw_logo_away and not raw_logo_away.startswith('http'):
                    raw_logo_away = f"https://footballista.ru{raw_logo_away}"
                if raw_logo_away and '-min' in raw_logo_away:
                    logo_away_url = raw_logo_away.replace('-min', '-max')
                elif raw_logo_away:
                    logo_away_url = raw_logo_away

            else:
                full_text = await card.locator('div.name').inner_text()
                parts = re.split(r'\s+(?:\d+\s*-\s*\d+(?:\s*тп)?|-)?\s+', full_text)
                if len(parts) >= 2:
                    team_home = parts[0]
                    team_away = parts[1]
                else:
                    logger.warning(f"Пропуск матча: не удалось разобрать команды ({full_text})")
                    continue

            href = await card.get_attribute("href")
            match_url = f"https://footballista.ru{href}"

            match_data = MatchMetadata(
                team_home=team_home.strip(),
                team_away=team_away.strip(),
                tournament_name=champ.strip(),
                tour_number=tour_number,
                match_date=date_raw,
                stadium=stadium,
                match_url=match_url,
                logo_home=logo_home_url,
                logo_away=logo_away_url,
            )

            matches.append(match_data)
            logger.info(f"Добавлен в очередь: {date_raw} | {match_data.stream_title} | {stadium}")

        logger.info(f"Успешно собрано матчей на эти выходные: {len(matches)}")
        matches = await enrich_matches_from_compact_view(context, matches)
        return matches

    except Exception as e:
        logger.error(f"Ошибка парсинга Footballista: {e}", exc_info=True)
        raise e
