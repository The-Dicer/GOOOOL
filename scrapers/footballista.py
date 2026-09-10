import os
import re
import json
import logging
import datetime
import urllib.parse
from typing import List, Optional
from models import MatchMetadata

logger = logging.getLogger(__name__)


def parse_match_item(item_wrap: dict, today, debug_30_matches: bool = False,
                     op_name: Optional[str] = None, op_token: Optional[str] = None,
                     op_chat_id: Optional[str] = None) -> Optional[MatchMetadata]:
    """Разбор отдельного элемента матча из ответа Footballista API."""
    item = item_wrap.get("raw_data") or item_wrap

    # 1. Проверка даты матча
    date_raw = item.get("date")
    match_dt = None
    if date_raw:
        try:
            dt = datetime.datetime.fromisoformat(str(date_raw).replace("Z", "+00:00"))
            match_dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=3)))
            match_date_str = match_dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            match_date_str = str(date_raw)
    else:
        match_date_str = str(today)

    # ФИЛЬТР: если дебаг выключен — берем строго матчи, которые еще не прошли (дата >= сегодня)
    if not debug_30_matches and match_dt:
        if match_dt.date() < today:
            return None

    # 2. Извлечение информации о командах и турнире
    m_id = item.get("_id") or item.get("id")
    team_home_obj = item.get("teamHome") or item.get("team1") or {}
    team_away_obj = item.get("teamAway") or item.get("team2") or {}
    champ_obj = item.get("champ") or item.get("tournament") or {}
    stadium_obj = item.get("stadium") or {}

    team_home = (team_home_obj.get("name") or "Команда 1").strip()
    team_away = (team_away_obj.get("name") or "Команда 2").strip()

    abbr_home = team_home_obj.get("shortName") or team_home_obj.get("short_name") or ""
    abbr_away = team_away_obj.get("shortName") or team_away_obj.get("short_name") or ""

    logo_home_name = (team_home_obj.get("logo") or "").strip()
    logo_away_name = (team_away_obj.get("logo") or "").strip()

    if logo_home_name and not logo_home_name.startswith("http"):
        logo_home = f"https://footballista.ru/api/img/logos/{urllib.parse.quote(logo_home_name)}-max.png?logoId=0"
    elif logo_home_name:
        logo_home = logo_home_name
    else:
        logo_home = "Нет логотипа"

    if logo_away_name and not logo_away_name.startswith("http"):
        logo_away = f"https://footballista.ru/api/img/logos/{urllib.parse.quote(logo_away_name)}-max.png?logoId=0"
    elif logo_away_name:
        logo_away = logo_away_name
    else:
        logo_away = "Нет логотипа"

    champ_name = (champ_obj.get("name") or "AFL").strip()
    stadium_name = (stadium_obj.get("name") or "Неизвестно").strip()
    tour_number = str(item.get("tourNumber") or item.get("tour") or item.get("round") or "1").strip()

    # Проверка наличия уже прикрепленных видео (трансляций)
    existing_videos = item.get("videos") or []
    has_video = False
    if isinstance(existing_videos, list):
        for v in existing_videos:
            if isinstance(v, dict) and v.get("link") and str(v.get("link")).strip():
                has_video = True
                break

    return MatchMetadata(
        match_id=str(m_id) if m_id else None,
        team_home=team_home,
        team_away=team_away,
        tournament_name=champ_name,
        tour_number=tour_number,
        match_date=match_date_str,
        stadium=stadium_name,
        match_url=f"https://footballista.ru/admin/games/{m_id}/{urllib.parse.quote(f'{team_home}-{team_away}')}" if m_id else None,
        logo_home=logo_home,
        logo_away=logo_away,
        abbr_home=abbr_home,
        abbr_away=abbr_away,
        has_video=has_video,
        operator_name=op_name,
        operator_token=op_token,
        operator_chat_id=op_chat_id
    )


async def get_all_weekend_matches(context, debug_30_matches: bool = False,
                                  operators: Optional[List[dict]] = None) -> List[MatchMetadata]:
    """
    Быстрый сбор матчей через прямой Footballista REST API с поддержкой нескольких операторов.
    
    - Если в config.json настроены операторы с токенами, матчи запрашиваются для каждого оператора.
    - Если операторы не указаны, используется текущая сессия браузера.
    - Если debug_30_matches == False: фильтруются только актуальные матчи (дата матча >= сегодня).
    - Если debug_30_matches == True: дебаг-режим, собирает до 30 матчей независимо от даты.
    """
    state_desc = "ВКЛЮЧЕН (30 матчей без фильтра по дате)" if debug_30_matches else "ВЫКЛЮЧЕН (только предстоящие матчи >= сегодня)"
    logger.info(f"Сбор матчей через Footballista REST API... [Расширенная выборка: {state_desc}]")

    # Чтение операторов из config.json если не переданы
    if operators is None:
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    operators = cfg.get("operators", [])
        except Exception as e:
            logger.warning(f"Не удалось загрузить operators из config.json: {e}")
            operators = []

    valid_operators = [op for op in (operators or []) if op.get("footballista_token")]

    page = None
    for p in context.pages:
        if "footballista.ru" in p.url:
            page = p
            break

    if not page:
        page = await context.new_page()
        await page.goto("https://footballista.ru/admin/games")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1500)
    else:
        await page.bring_to_front()

    fetch_by_token_script = """async (token) => {
        let cleanToken = typeof token === 'string' ? token.replace(/^["']+|["']+$/g, '').trim() : String(token);
        const authHeader = cleanToken.startsWith('Bearer ') ? cleanToken : `Bearer ${cleanToken}`;

        const headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Authorization': authHeader
        };

        const res = await fetch('https://footballista.ru/api/leagues/394/my_games', {
            method: 'GET',
            headers: headers
        });

        if (!res.ok) {
            const errText = await res.text();
            return { error: `HTTP ${res.status}: ${errText}` };
        }
        return await res.json();
    }"""

    # Скрипт извлечения токена из активной сессии браузера (fallback)
    fetch_browser_script = """async () => {
        async function getIonicData() {
            return new Promise((resolve) => {
                if (!window.indexedDB) return resolve({});
                const req = indexedDB.open('_ionicstorage');
                req.onsuccess = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('_ionickv')) return resolve({});
                    const tx = db.transaction('_ionickv', 'readonly');
                    const store = tx.objectStore('_ionickv');
                    const keysReq = store.getAllKeys();
                    const valsReq = store.getAll();
                    tx.oncomplete = () => {
                        const items = {};
                        const keys = keysReq.result || [];
                        const vals = valsReq.result || [];
                        for (let i = 0; i < keys.length; i++) {
                            items[keys[i]] = vals[i];
                        }
                        resolve(items);
                    };
                    tx.onerror = () => resolve({});
                };
                req.onerror = () => resolve({});
            });
        }

        const ionicStore = await getIonicData();
        let token = ionicStore['token'] || null;

        if (!token) {
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                const v = localStorage.getItem(k);
                if (v && v.startsWith('eyJ')) {
                    token = v;
                    break;
                }
            }
        }

        if (!token) {
            return { error: 'Токен авторизации не найден в браузере. Пожалуйста, откройте вкладку footballista.ru и залогиньтесь.' };
        }

        let cleanToken = typeof token === 'string' ? token.replace(/^["']+|["']+$/g, '').trim() : String(token);
        const authHeader = cleanToken.startsWith('Bearer ') ? cleanToken : `Bearer ${cleanToken}`;

        const headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Authorization': authHeader
        };

        const res = await fetch('https://footballista.ru/api/leagues/394/my_games', {
            method: 'GET',
            headers: headers
        });

        if (!res.ok) {
            const errText = await res.text();
            return { error: `HTTP ${res.status}: ${errText}` };
        }
        return await res.json();
    }"""

    today = datetime.datetime.now().date()
    all_parsed_matches: List[MatchMetadata] = []
    seen_ids = set()

    if valid_operators:
        logger.info(f"Найдено зарегистрированных операторов: {len(valid_operators)}. Загрузка матчей для каждого оператора...")
        for op in valid_operators:
            op_name = op.get("name", "Оператор")
            op_token = op.get("footballista_token")
            op_chat = op.get("chat_id")
            logger.info(f"Запрос матчей для '{op_name}'...")
            try:
                raw_data = await page.evaluate(fetch_by_token_script, op_token)
                if not raw_data or (isinstance(raw_data, dict) and "error" in raw_data):
                    err = raw_data.get("error") if isinstance(raw_data, dict) else "Пустой ответ"
                    logger.warning(f"Ошибка получения матчей для '{op_name}': {err}")
                    continue

                raw_list = raw_data if isinstance(raw_data, list) else (raw_data.get("data") or [])
                count_op = 0
                for item_wrap in raw_list:
                    m = parse_match_item(item_wrap, today, debug_30_matches, op_name, op_token, op_chat)
                    if m:
                        if m.match_id and m.match_id in seen_ids:
                            continue
                        if m.match_id:
                            seen_ids.add(m.match_id)
                        all_parsed_matches.append(m)
                        count_op += 1
                        if debug_30_matches and len(all_parsed_matches) >= 30:
                            break
                logger.info(f"Для '{op_name}' загружено матчей: {count_op}")
                if debug_30_matches and len(all_parsed_matches) >= 30:
                    break
            except Exception as e:
                logger.warning(f"Сбой при запросе матчей для '{op_name}': {e}")
    else:
        # Fallback: запрос через активную сессию браузера
        logger.info("Операторы с токенами не найдены. Запрос через текущую сессию браузера...")
        raw_data = await page.evaluate(fetch_browser_script)
        if not raw_data or (isinstance(raw_data, dict) and "error" in raw_data):
            err = raw_data.get("error") if isinstance(raw_data, dict) else "Пустой ответ от сервера"
            raise RuntimeError(f"Ошибка вызова Footballista API: {err}")

        raw_list = raw_data if isinstance(raw_data, list) else (raw_data.get("data") or [])
        for item_wrap in raw_list:
            m = parse_match_item(item_wrap, today, debug_30_matches)
            if m:
                if m.match_id and m.match_id in seen_ids:
                    continue
                if m.match_id:
                    seen_ids.add(m.match_id)
                all_parsed_matches.append(m)
                if debug_30_matches and len(all_parsed_matches) >= 30:
                    break

    logger.info(f"Итого отобрано для работы: {len(all_parsed_matches)} матчей.")
    return all_parsed_matches
