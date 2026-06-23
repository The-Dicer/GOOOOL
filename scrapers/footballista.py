import re
import logging
import datetime
from typing import List, Dict
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def enrich_matches_from_compact_view(context, matches: List[MatchMetadata]) -> List[MatchMetadata]:
    logger.info("Открываем дополнительную вкладку Footballista в компактном режиме...")
    compact_page = await context.new_page()
    try:
        await compact_page.set_viewport_size({"width": 400, "height": 900})
        await compact_page.goto("https://footballista.ru/admin/games")

        # ИСПРАВЛЕНИЕ 1: Ждем просто загрузки основного HTML, игнорируя фоновый мусор
        await compact_page.wait_for_load_state("domcontentloaded")

        # ИСПРАВЛЕНИЕ 2: Ждем, пока появится хотя бы одна карточка (таймаут 15 сек)
        await compact_page.wait_for_selector('a[href^="/admin/games/"]', state="visible", timeout=15000)

        # Защита от гонки. Ждем, пока в DOM появится больше одной карточки
        try:
            await compact_page.wait_for_function(
                'document.querySelectorAll("a[href^=\'/admin/games/\']").length > 1',
                timeout=5000
            )
        except Exception:
            pass  # Если матч реально один, просто идем дальше

        await compact_page.wait_for_timeout(1000)  # Даем секунду на финальную перерисовку

        compact_cards = await compact_page.locator('a[href^="/admin/games/"]').all()
        logger.info(f"Найдено карточек в мобильной версии: {len(compact_cards)}")

        compact_map: Dict[str, dict] = {}

        for card in compact_cards:
            href = await card.get_attribute("href")
            if not href: continue

            full_match_url = f"https://footballista.ru{href}"
            imgs = card.locator("img")
            img_count = await imgs.count()

            logo_home, logo_away, abbr_home, abbr_away = "Нет логотипа", "Нет логотипа", "", ""

            if img_count >= 2:
                raw_logo_home = await imgs.nth(0).get_attribute("src")
                raw_logo_away = await imgs.nth(1).get_attribute("src")

                if raw_logo_home:
                    logo_home = raw_logo_home.replace("-min", "-max") if not raw_logo_home.startswith(
                        "/") else f"https://footballista.ru{raw_logo_home}".replace("-min", "-max")
                if raw_logo_away:
                    logo_away = raw_logo_away.replace("-min", "-max") if not raw_logo_away.startswith(
                        "/") else f"https://footballista.ru{raw_logo_away}".replace("-min", "-max")

                name_text = await card.locator("div.name").inner_text()
                name_text = name_text.replace("\n", " ").replace("\r", " ").strip().upper()

                # ИСПРАВЛЕНИЕ 3: Вырезаем счет, чтобы аббревиатуры были чистыми ("VEL" вместо "VEL1")
                parts = re.split(r'\s*\d+\s*-\s*\d+\s*', name_text)
                if len(parts) == 2 and parts[0] and parts[1]:
                    abbr_home, abbr_away = parts[0].strip(), parts[1].strip()
                else:
                    # План Б: если счет еще не сыгран, делим просто по тире с пробелами
                    fallback_parts = re.split(r'\s+-\s+', name_text)
                    if len(fallback_parts) == 2:
                        abbr_home, abbr_away = fallback_parts[0].strip(), fallback_parts[1].strip()
                    else:
                        # Запасной вариант (старая логика)
                        clean_name = re.sub(r"\s+", "", name_text)
                        short_match = re.search(r"([A-ZА-Я0-9]{2,8})-([A-ZА-Я0-9]{2,8})", clean_name)
                        if short_match:
                            abbr_home, abbr_away = short_match.group(1), short_match.group(2)

            compact_map[full_match_url] = {
                "logo_home": logo_home, "logo_away": logo_away,
                "abbr_home": abbr_home, "abbr_away": abbr_away,
            }

        for match in matches:
            extra = compact_map.get(match.match_url)
            if extra:
                match.logo_home = extra["logo_home"]
                match.logo_away = extra["logo_away"]
                match.abbr_home = extra["abbr_home"]
                match.abbr_away = extra["abbr_away"]

                if match.abbr_home and match.logo_home != "Нет логотипа":
                    logger.info(f"Данные подтянуты: {match.abbr_home} vs {match.abbr_away}")
                else:
                    logger.warning(f"Частично нет лого/сокращений: {match.team_home} vs {match.team_away}")
            else:
                logger.warning(f"Матч не найден в мобильной версии: {match.team_home} vs {match.team_away}")

        logger.info("Сбор дополнительных данных завершен.")
        return matches
    finally:
        await compact_page.close()


async def get_all_weekend_matches(context, debug_30_matches=False) -> List[MatchMetadata]:
    logger.info("Ищем вкладку Footballista...")
    footballista_page = next((p for p in context.pages if "footballista.ru" in p.url), None)

    if not footballista_page:
        raise Exception("Открой вкладку Footballista в браузере!")

    await footballista_page.bring_to_front()
    matches = []

    try:
        await footballista_page.wait_for_selector('a[href^="/admin/games/"]', state="visible", timeout=10000)
        match_cards = await footballista_page.locator('a[href^="/admin/games/"]').all()
        today = datetime.datetime.now().date()
        current_year = today.year

        month_map = {
            "ЯНВ": 1, "ФЕВ": 2, "МАР": 3, "АПР": 4, "МАЯ": 5, "МАЙ": 5,
            "ИЮН": 6, "ИЮЛ": 7, "АВГ": 8, "СЕН": 9, "ОКТ": 10, "НОЯ": 11, "ДЕК": 12
        }

        for card in match_cards:
            date_raw = (await card.locator('div.date').inner_text()).strip().upper()
            date_str = date_raw.split('(')[0].replace('.', '').strip()

            # --- ЛОГИКА ДЕБАГА ИЛИ ПРИВЯЗКА К ДАТЕ ---
            if debug_30_matches:
                if len(matches) >= 30:
                    logger.info("Дебаг режим: собрано 30 матчей. Остановка.")
                    break
            else:
                try:
                    parts = date_str.split()
                    if len(parts) >= 2:
                        d_num = int(parts[0])
                        m_str = parts[1][:3]
                        m_num = month_map.get(m_str, today.month)

                        match_date_obj = datetime.date(current_year, m_num, d_num)

                        if match_date_obj < today - datetime.timedelta(days=180):
                            match_date_obj = match_date_obj.replace(year=current_year + 1)
                        elif match_date_obj > today + datetime.timedelta(days=180):
                            match_date_obj = match_date_obj.replace(year=current_year - 1)
                        # pashalka
                        if match_date_obj < today:
                            logger.info(f"Матч {date_raw} уже прошел. Останавливаем сбор.")
                            break

                except Exception as e:
                    logger.warning(f"Не удалось распознать дату матча: {date_raw}. Игнорируем.")
                    pass
                    # -----------------------------------------

            champ = await card.locator('div.champ').inner_text()
            # ... дальше идет старый код парсинга champ, stadium, tour_number и т.д.
            try:
                stadium = (await card.locator("xpath=..").locator('.stadium').first.inner_text(timeout=1000)).strip()
            except:
                stadium = "Неизвестно"

            tour_number = int(re.search(r'\d+', await card.locator('div.round').inner_text()).group())

            img_count = await card.locator('img').count()
            if img_count >= 2:
                team_home = await card.locator('img').nth(0).get_attribute('title')
                team_away = await card.locator('img').nth(1).get_attribute('title')
            else:
                parts = re.split(r'\s+(?:\d+\s*-\s*\d+(?:\s*тп)?|-)?\s+', await card.locator('div.name').inner_text())
                if len(parts) >= 2:
                    team_home, team_away = parts[0], parts[1]
                else:
                    continue

            href = await card.get_attribute("href")

            match_data = MatchMetadata(
                team_home=team_home.strip(),
                team_away=team_away.strip(),
                tournament_name=champ.strip(),
                tour_number=tour_number,
                match_date=date_raw,
                stadium=stadium,
                match_url=f"https://footballista.ru{href}"
            )
            matches.append(match_data)

        matches = await enrich_matches_from_compact_view(context, matches)
        return matches

    except Exception as e:
        logger.error(f"Ошибка парсинга Footballista: {e}")
        raise e
