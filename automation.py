import os
import sys

# Гарантируем, что рабочий каталог всегда указывает на корень проекта GOAL
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import json
import time
import base64
import logging
import asyncio
import datetime
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import requests

# Исключаем локальные адреса из системного прокси, чтобы Playwright не слал локальный CDP трафик в прокси
for _proxy_key in ["NO_PROXY", "no_proxy"]:
    _curr = os.environ.get(_proxy_key, "")
    _proxies = [p.strip() for p in _curr.split(",") if p.strip()]
    for _h in ["localhost", "127.0.0.1", "::1"]:
        if _h not in _proxies:
            _proxies.append(_h)
    os.environ[_proxy_key] = ",".join(_proxies)

from playwright.async_api import async_playwright

from models import MatchMetadata
from scrapers.footballista import get_all_weekend_matches
from scrapers.graphics import prepare_graphics
from publishers.rutube import publish_stream
from publishers.footballista import add_video_link_to_match
from main import cleanup_old_files, process_selected_matches, fetch_matches_for_ui

logger = logging.getLogger("automation")

PROCESSED_FILE = "processed_matches.json"
CONFIG_FILE = "config.json"


def ensure_config_exists():
    """Создает config.json из config.example.json если он отсутствует."""
    if not os.path.exists(CONFIG_FILE):
        example = "config.example.json"
        if os.path.exists(example):
            try:
                import shutil
                shutil.copy(example, CONFIG_FILE)
                logger.info(f"Создан {CONFIG_FILE} на основе шаблона {example}")
            except Exception as e:
                logger.error(f"Не удалось скопировать {example}: {e}")


# ==========================================
# 1. ТЕЛЕГРАМ УВЕДОМЛЕНИЯ
# ==========================================

class TelegramNotifier:
    def __init__(self, token: str, default_chat_id: str = ""):
        self.token = (token or "").strip()
        self.default_chat_id = (str(default_chat_id) or "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def is_configured(self) -> bool:
        return bool(self.token)

    def get_last_chat_id(self) -> Optional[str]:
        """
        Автоматически находит ID последнего написавшего в бота чата/пользователя.
        Пользователю достаточно написать /start или любое сообщение в бота.
        """
        if not self.is_configured():
            return None
        try:
            url = f"{self.base_url}/getUpdates"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return None

            results = data.get("result", [])
            if not results:
                return None

            # Ищем последнее сообщение от конца
            for update in reversed(results):
                msg = update.get("message") or update.get("channel_post") or update.get("my_chat_member")
                if msg and "chat" in msg and "id" in msg["chat"]:
                    return str(msg["chat"]["id"])
            return None
        except Exception as e:
            logger.warning(f"Не удалось получить chat_id из Telegram: {e}")
            return None

    def get_recent_chats(self) -> List[Dict[str, Any]]:
        """
        Возвращает список всех уникальных пользователей/чатов, писавших боту последнее время.
        Формат элементов: {"chat_id": "...", "name": "...", "username": "...", "date": ...}
        """
        if not self.is_configured():
            return []
        try:
            url = f"{self.base_url}/getUpdates"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return []

            results = data.get("result", [])
            seen_chats = {}
            for update in results:
                msg = update.get("message") or update.get("channel_post") or update.get("my_chat_member")
                if msg and "chat" in msg and "id" in msg["chat"]:
                    cid = str(msg["chat"]["id"])
                    c_title = msg["chat"].get("title")
                    first_name = msg["chat"].get("first_name", "")
                    last_name = msg["chat"].get("last_name", "")
                    full_name = (f"{first_name} {last_name}").strip() or c_title or "Без имени"
                    username = msg["chat"].get("username", "")
                    seen_chats[cid] = {
                        "chat_id": cid,
                        "name": full_name,
                        "username": f"@{username}" if username else "",
                        "date": msg.get("date", 0)
                    }
            return list(seen_chats.values())
        except Exception as e:
            logger.warning(f"Не удалось получить список чатов Telegram: {e}")
            return []

    def send_message(self, text: str, chat_id: Optional[str] = None, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
        """Отправка текстового сообщения в чат (с опциональными inline-кнопками/WebApp)."""
        target_chat = chat_id or self.default_chat_id
        if not self.is_configured() or not target_chat:
            logger.warning("Telegram не настроен (отсутствует токен или chat_id).")
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": target_chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            res = requests.post(url, json=payload, timeout=10)
            if res.ok:
                logger.info(f"Сообщение успешно отправлено в Telegram чат {target_chat}")
                return True
            else:
                logger.error(f"Ошибка отправки в Telegram: {res.status_code} - {res.text}")
                return False
        except Exception as e:
            logger.error(f"Сбой при отправке в Telegram: {e}")
            return False

    def send_document(self, file_path: str, caption: str = "", chat_id: Optional[str] = None) -> bool:
        """Отправка файла со стрим-ключами прямо в Telegram чат."""
        target_chat = chat_id or self.default_chat_id
        if not self.is_configured() or not target_chat:
            return False

        if not os.path.exists(file_path):
            logger.error(f"Файл для отправки в Telegram не найден: {file_path}")
            return False

        try:
            url = f"{self.base_url}/sendDocument"
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f, "text/plain")}
                data = {
                    "chat_id": target_chat,
                    "caption": caption[:1024] if caption else "",
                    "parse_mode": "HTML"
                }
                res = requests.post(url, data=data, files=files, timeout=30)
                if res.ok:
                    logger.info(f"Файл {os.path.basename(file_path)} отправлен в Telegram чат {target_chat}")
                    return True
                else:
                    logger.error(f"Ошибка отправки файла в Telegram: {res.status_code} - {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Сбой отправки документа в Telegram: {e}")
            return False


# ==========================================
# 2. УПРАВЛЕНИЕ БРАУЗЕРОМ CHROME
# ==========================================

def find_chrome_executable() -> str:
    """Поиск установленного Chrome в стандартных путях Windows."""
    possible_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "chrome.exe"


def is_chrome_running() -> bool:
    """Проверка доступности CDP порта 9222."""
    try:
        req = urllib.request.Request("http://localhost:9222/json/version")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_chrome():
    """Запуск Chrome с рабочим профилем GOAL и портом 9222."""
    chrome_path = find_chrome_executable()
    profile_path = os.path.join(os.getcwd(), "chrome_debug_profile")
    os.makedirs(profile_path, exist_ok=True)

    cmd = [
        chrome_path,
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_path}",
        "https://studio.rutube.ru/streams"
    ]
    logger.info(f"Запуск Chrome (порт 9222, профиль: '{profile_path}')...")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, **kwargs)


async def ensure_chrome_running(max_wait: int = 15) -> Tuple[bool, bool]:
    """
    Проверяет, запущен ли Chrome. Если нет — запускает и ожидает готовности.
    Возвращает: (готов_к_работе, был_ли_запущен_скриптом).
    """
    if is_chrome_running():
        return True, False

    logger.info("Chrome на порту 9222 не найден. Автоматический запуск...")
    proc = launch_chrome()

    for _ in range(max_wait):
        await asyncio.sleep(1)
        if is_chrome_running():
            logger.info("Chrome успешно запущен и готов к работе.")
            await asyncio.sleep(2)  # дать вкладкам стабилизироваться
            return True, True

    logger.error("Chrome не ответил по порту 9222 за отведенное время.")
    return False, True


# ==========================================
# 3. БАЗА ОБРАБОТАННЫХ МАТЧЕЙ (ДЕДУПЛИКАЦИЯ)
# ==========================================

def load_processed_matches() -> Dict[str, Any]:
    """Загрузка базы уже созданных трансляций."""
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Не удалось прочитать {PROCESSED_FILE}: {e}")
    return {"matches": {}}


def save_processed_matches(data: Dict[str, Any]):
    """Сохранение базы созданных трансляций."""
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {PROCESSED_FILE}: {e}")


def is_match_already_processed(match: MatchMetadata, db: Dict[str, Any]) -> bool:
    """
    Матч считается обработанным, если:
    1. У него в Footballista уже есть прикрепленное видео (has_video == True)
    2. Либо его match_id уже записан в локальной базе
    3. Либо его уникальное название stream_title уже зафиксировано в базе
    """
    if match.has_video:
        return True

    matches_db = db.get("matches", {})
    if match.match_id and str(match.match_id) in matches_db:
        return True

    # Дополнительная проверка по stream_title
    for m_info in matches_db.values():
        if m_info.get("stream_title") == match.stream_title:
            return True

    return False


def record_processed_matches(results: List[Dict[str, Any]], db: Dict[str, Any]):
    """Запись успешно созданных матчей в базу."""
    matches_db = db.setdefault("matches", {})
    now_iso = datetime.datetime.now().isoformat()

    for item in results:
        if item.get("success"):
            match: MatchMetadata = item["match"]
            m_key = match.match_id or match.stream_title
            matches_db[str(m_key)] = {
                "match_id": match.match_id,
                "stream_title": match.stream_title,
                "match_date": match.match_date,
                "stadium": match.stadium,
                "video_url": item.get("video_url", ""),
                "operator_name": match.operator_name,
                "operator_chat_id": match.operator_chat_id,
                "processed_at": now_iso
            }
    save_processed_matches(db)


MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]


def get_friday_week_number(friday_date: datetime.date) -> int:
    """
    Определяет порядковый номер пятницы в текущем месяце (Выходные #1, #2, #3, #4, #5).
    """
    count = 0
    curr = datetime.date(friday_date.year, friday_date.month, 1)
    while curr <= friday_date:
        if curr.weekday() == 4:  # Пятница
            count += 1
        curr += datetime.timedelta(days=1)
    return max(1, count)


def build_calculator_url(
    base_url: str,
    start_date: datetime.date,
    end_date: datetime.date,
    matches: List[Any],
    mode: str = "weekend",
    period_title: Optional[str] = None
) -> str:
    """
    Формирует безопасную ссылку на Telegram WebApp калькулятор.
    Все данные пакуются в URL hash (#data=...), поэтому они не передаются
    на внешние серверы и обрабатываются строго на телефоне оператора.
    Поддерживает как режим стандартных выходных (mode='weekend'),
    так и произвольный период failsafe (mode='custom').
    """
    week_num = get_friday_week_number(start_date)
    dates_str = period_title or f"{start_date.strftime('%d.%m')} – {end_date.strftime('%d.%m.%Y')}"
    w_key = f"{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"

    match_payloads = []
    for i, m in enumerate(matches, 1):
        if hasattr(m, "stream_title"):
            m_id = str(m.match_id or i)
            title = m.stream_title
            m_time = m.match_date
            stadium = m.stadium
        elif isinstance(m, dict):
            m_id = str(m.get("match_id") or i)
            title = m.get("stream_title") or m.get("title") or "Матч"
            m_time = m.get("match_date") or m.get("time") or ""
            stadium = m.get("stadium") or ""
        else:
            m_id = str(i)
            title = str(m)
            m_time = ""
            stadium = ""

        # Пытаемся извлечь дату в формате YYYY-MM-DD
        date_iso = ""
        parsed_dt = parse_match_date(str(m_time))
        if parsed_dt:
            date_iso = parsed_dt.isoformat()

        match_payloads.append({
            "id": m_id,
            "title": title,
            "time": m_time,
            "stadium": stadium,
            "date": date_iso
        })

    payload = {
        "mode": mode,
        "w_key": w_key,
        "dates": dates_str,
        "period_title": dates_str,
        "week": week_num,
        "month_idx": start_date.month - 1,
        "year": start_date.year,
        "matches": match_payloads
    }

    raw_json = json.dumps(payload, ensure_ascii=False)
    b64_data = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
    clean_base = (base_url or "").split("#")[0] if base_url else "https://the-dicer.github.io/GOOOOL/webapp/calculator.html"
    return f"{clean_base}#data={b64_data}"


def send_operator_dispatch(
    results: List[Dict[str, Any]],
    master_keys_file: str,
    config: Optional[Dict[str, Any]] = None,
    dates_display: Optional[str] = None,
    week_num: Optional[int] = None,
    month_name: Optional[str] = None,
    friday_date: Optional[datetime.date] = None,
    sunday_date: Optional[datetime.date] = None,
    notifier: Optional[TelegramNotifier] = None
) -> int:
    """
    Персональная рассылка отчетов и файлов ключей каждому оператору в Telegram:
    - Каждый оператор получает ТОЛЬКО свои матчи и количество своих матчей.
    - Каждый оператор получает ТОЛЬКО свой файл ключей (stream_keys_..._Имя.txt).
    - Кнопка калькулятора WebApp рассчитывает оплату ТОЛЬКО для матчей данного оператора.
    - Операторы без созданных матчей в этой пачке не получают чужие отчеты.
    Возвращает количество операторов, которым успешно отправлены отчеты.
    """
    if config is None:
        ensure_config_exists()
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}

    tg_cfg = config.get("telegram", {})
    token = tg_cfg.get("bot_token", "")
    primary_chat_id = str(tg_cfg.get("chat_id", "")).strip()
    tg_send_file = tg_cfg.get("send_file", True)
    webapp_url = tg_cfg.get("webapp_url", "https://the-dicer.github.io/GOOOOL/webapp/calculator.html")

    if notifier is None:
        notifier = TelegramNotifier(token, primary_chat_id)

    if not notifier.is_configured():
        logger.warning("Telegram не настроен (отсутствует bot_token). Отправка отчетов пропущена.")
        return 0

    if friday_date is None or sunday_date is None:
        friday_date, sunday_date, _ = get_current_weekend_window()
    if week_num is None:
        week_num = get_friday_week_number(friday_date)
    if month_name is None:
        month_name = MONTH_NAMES_RU[friday_date.month] if 1 <= friday_date.month <= 12 else ""
    if dates_display is None:
        dates_display = f"{friday_date.strftime('%d.%m')} – {sunday_date.strftime('%d.%m.%Y')}"

    registered_ops = config.get("operators", [])

    # Группировка результатов по операторам (ключ: chat_id)
    groups: Dict[str, Dict[str, Any]] = {}

    if registered_ops:
        for op in registered_ops:
            c_id = str(op.get("chat_id", "")).strip()
            o_name = op.get("name", "Оператор")
            if c_id:
                groups[c_id] = {
                    "name": o_name,
                    "chat_id": c_id,
                    "items": []
                }

    # Если зарегистрированных операторов нет, используем primary_chat_id
    if not groups and primary_chat_id:
        groups[primary_chat_id] = {
            "name": "Оператор",
            "chat_id": primary_chat_id,
            "items": []
        }

    # Распределяем элементы results по группам операторов
    for item in results:
        m: MatchMetadata = item["match"]
        assigned = False

        # 1. Поиск по chat_id
        if m.operator_chat_id and str(m.operator_chat_id).strip() in groups:
            groups[str(m.operator_chat_id).strip()]["items"].append(item)
            assigned = True
        # 2. Поиск по имени оператора
        elif m.operator_name:
            for g in groups.values():
                if g["name"] == m.operator_name:
                    g["items"].append(item)
                    assigned = True
                    break

        # 3. Fallback: если не совпало ни с кем — отправляем в fallback chat
        if not assigned:
            fallback_cid = primary_chat_id or (list(groups.keys())[0] if groups else None)
            if fallback_cid:
                if fallback_cid not in groups:
                    groups[fallback_cid] = {
                        "name": "Оператор",
                        "chat_id": fallback_cid,
                        "items": []
                    }
                groups[fallback_cid]["items"].append(item)

    sent_count = 0
    for cid, g_data in groups.items():
        op_items = g_data["items"]
        if not op_items:
            # У этого оператора нет матчей в текущем запуске — не шлем пустые/чужие отчеты!
            logger.info(f"Для оператора '{g_data['name']}' нет матчей в текущей партии. Уведомление пропущено.")
            continue

        op_name = g_data["name"]
        op_total = len(op_items)
        op_success = sum(1 for it in op_items if it.get("success"))

        # Определяем персональный файл ключей
        op_keys_file = None
        for it in op_items:
            cand = it.get("operator_keys_file")
            if cand and os.path.exists(cand):
                op_keys_file = cand
                break
        if not op_keys_file and master_keys_file and os.path.exists(master_keys_file):
            op_keys_file = master_keys_file

        # Формируем текст отчета
        report_lines = [
            f"<b>GOAL: Ваши трансляции успешно созданы</b>",
            f"Оператор: <b>{op_name}</b>",
            f"Период: <b>{dates_display}</b> ({month_name}, Выходные #{week_num})",
            f"Создано: <b>{op_success}</b> из <b>{op_total}</b> ваших матчей\n",
            "<b>Ваши матчи:</b>"
        ]
        for it in op_items:
            m = it["match"]
            v_url = it.get("video_url")
            icon = "[OK]" if it.get("success") else "[ERR]"
            if v_url:
                report_lines.append(f"{icon} <a href='{v_url}'>{m.stream_title}</a>")
            else:
                report_lines.append(f"{icon} {m.stream_title}")

        if op_keys_file:
            report_lines.append(f"\nФайл ключей:\n<code>{os.path.basename(op_keys_file)}</code>")

        report_text = "\n".join(report_lines)

        # Формируем персональную кнопку WebApp калькулятора ТОЛЬКО для матчей этого оператора
        calc_matches = [it["match"] for it in op_items if it.get("success")] or [it["match"] for it in op_items]
        calc_url = build_calculator_url(webapp_url, friday_date, sunday_date, calc_matches)
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": f"Мой расчет оплаты ({len(calc_matches)} игр)",
                        "web_app": {"url": calc_url}
                    }
                ]
            ]
        }

        # Отправляем сообщение
        msg_ok = notifier.send_message(report_text, chat_id=cid, reply_markup=reply_markup)
        if msg_ok:
            sent_count += 1
            logger.info(f"Персональный отчет отправлен оператору '{op_name}' (чат {cid}): {op_success}/{op_total} матчей.")

        # Отправляем персональный файл ключей
        if tg_send_file and op_keys_file and os.path.exists(op_keys_file):
            caption = f"Ключи трансляций: {op_name} ({datetime.datetime.now().strftime('%d.%m.%Y')})"
            notifier.send_document(op_keys_file, caption=caption, chat_id=cid)

    return sent_count


def send_custom_period_report(
    start_date: datetime.date,
    end_date: datetime.date,
    matches: Optional[List[Any]] = None
) -> Tuple[bool, str]:
    """
    Формирует и отправляет в Telegram отчет и персональный WebApp-калькулятор
    за произвольный (случайный) период дат (Failsafe-механизм).
    Возвращает: (success: bool, status_message: str)
    """
    ensure_config_exists()
    if not os.path.exists(CONFIG_FILE):
        return False, "Файл config.json не найден."

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return False, f"Ошибка чтения {CONFIG_FILE}: {e}"

    tg_cfg = cfg.get("telegram", {})
    token = tg_cfg.get("bot_token", "")
    primary_chat_id = tg_cfg.get("chat_id", "")
    operators = cfg.get("operators", [])
    target_chats = [str(op["chat_id"]).strip() for op in operators if op.get("chat_id")]
    if not target_chats and primary_chat_id:
        target_chats = [str(primary_chat_id).strip()]

    if not token or not target_chats:
        return False, "В config.json не указаны bot_token или chat_id для Telegram."

    # Если список матчей не передан, ищем подходящие в базе обработанных
    period_matches = []
    if matches is not None:
        period_matches = matches
    else:
        db = load_processed_matches()
        matches_db = db.get("matches", {})
        for m_id, m_info in matches_db.items():
            m_date_str = m_info.get("match_date", "")
            proc_at = m_info.get("processed_at", "")
            m_dt = parse_match_date(m_date_str)
            if not m_dt and proc_at:
                try:
                    m_dt = datetime.date.fromisoformat(proc_at.split("T")[0])
                except Exception:
                    pass

            if m_dt and (start_date <= m_dt <= end_date):
                period_matches.append(m_info)

    dates_str = f"{start_date.strftime('%d.%m.%Y')} – {end_date.strftime('%d.%m.%Y')}"
    calc_url = build_calculator_url(
        webapp_url,
        start_date,
        end_date,
        period_matches,
        mode="custom",
        period_title=dates_str
    )

    notifier = TelegramNotifier(token, chat_id)
    text_lines = [
        "<b>GOAL 3.2: Отчет за произвольный период (Failsafe)</b>\n",
        f"<b>Интервал:</b> {dates_str}",
        f"<b>Найдено матчей:</b> {len(period_matches)}"
    ]

    if period_matches:
        text_lines.append("\n<b>Список матчей:</b>")
        for i, m in enumerate(period_matches[:15], 1):
            title = m.get("stream_title") if isinstance(m, dict) else getattr(m, "stream_title", str(m))
            time_val = m.get("match_date") if isinstance(m, dict) else getattr(m, "match_date", "")
            if time_val:
                text_lines.append(f"  {i}. {title} ({time_val})")
            else:
                text_lines.append(f"  {i}. {title}")
        if len(period_matches) > 15:
            text_lines.append(f"  <i>...и еще {len(period_matches) - 15} матчей</i>")

    text_lines.append("\n<i>Нажмите кнопку ниже, чтобы открыть расчет, настроить ставку и исключить неоплаченные игры на телефоне:</i>")

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "Расчет за выбранный период",
                    "web_app": {"url": calc_url}
                }
            ]
        ]
    }

    any_ok = False
    for cid in target_chats:
        if notifier.send_message("\n".join(text_lines), chat_id=cid, reply_markup=reply_markup):
            any_ok = True

    if any_ok:
        return True, f"Отчет за период {dates_str} успешно отправлен в Telegram ({len(target_chats)} чат(ов))!"
    else:
        return False, "Сбой отправки сообщения в Telegram API."


def get_current_weekend_window(ref_date: Optional[datetime.date] = None) -> Tuple[datetime.date, datetime.date, str]:
    """
    Определяет диапазон текущих игровых выходных (Пятница - Воскресенье).
    Понедельник полностью исключен из расчета.
    Возвращает: (friday_date, sunday_date, weekend_key)
    """
    if ref_date is None:
        ref_date = datetime.datetime.now().date()

    # 0: Пн, 1: Вт, 2: Ср, 3: Чт, 4: Пт, 5: Сб, 6: Вс
    weekday = ref_date.weekday()

    if weekday in [4, 5, 6]:  # Пятница, Суббота, Воскресенье (текущие выходные)
        friday_date = ref_date - datetime.timedelta(days=(weekday - 4))
    else:  # Пн (0), Вт (1), Ср (2), Чт (3) -> готовимся к предстоящей пятнице
        friday_date = ref_date + datetime.timedelta(days=(4 - weekday))

    sunday_date = friday_date + datetime.timedelta(days=2)
    weekend_key = f"{friday_date.strftime('%Y-%m-%d')}_to_{sunday_date.strftime('%Y-%m-%d')}"
    return friday_date, sunday_date, weekend_key


def parse_match_date(date_str: str) -> Optional[datetime.date]:
    """Парсинг даты матча из строки вида '12.09.2026 14:00' или '2026-09-12'."""
    if not date_str:
        return None
    try:
        d_part = str(date_str).strip().split()[0]
        if "." in d_part:
            parts = d_part.split(".")
            if len(parts) == 3:
                return datetime.date(int(parts[2]), int(parts[1]), int(parts[0]))
        elif "-" in d_part:
            parts = d_part.split("-")
            if len(parts) == 3:
                return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        pass
    return None


def is_weekend_completed(weekend_key: str, db: Dict[str, Any], account_id: str = "default") -> bool:
    """Проверяет, помечены ли текущие выходные как полностью обработанные."""
    completed = db.get("completed_weekends", {})
    entry = completed.get(weekend_key)
    if not entry:
        return False
    if isinstance(entry, dict):
        if account_id in entry:
            return True
        if "completed_at" in entry:
            return True
    return False


def mark_weekend_completed(weekend_key: str, matches_count: int, days_list: List[str], db: Dict[str, Any], account_id: str = "default"):
    """Фиксирует текущие выходные как завершенные."""
    completed = db.setdefault("completed_weekends", {})
    entry = completed.setdefault(weekend_key, {})
    if not isinstance(entry, dict) or "completed_at" in entry:
        entry = {}
        completed[weekend_key] = entry
    entry[account_id] = {
        "completed_at": datetime.datetime.now().isoformat(),
        "matches_count": matches_count,
        "days": days_list
    }
    save_processed_matches(db)


def reset_weekend_status(weekend_key: Optional[str] = None, account_id: Optional[str] = None):
    """Сброс статуса завершенности выходных (для принудительной повторной проверки)."""
    db = load_processed_matches()
    completed = db.get("completed_weekends", {})
    if weekend_key:
        if weekend_key in completed:
            if account_id and isinstance(completed[weekend_key], dict) and account_id in completed[weekend_key]:
                del completed[weekend_key][account_id]
            else:
                del completed[weekend_key]
    else:
        completed.clear()
    save_processed_matches(db)


# ==========================================
# 4. СИСТЕМНЫЕ УВЕДОМЛЕНИЯ WINDOWS
# ==========================================

def send_desktop_notification(title: str, message: str):
    """Отображение всплывающего системного уведомления Windows."""
    try:
        # Быстрый и надежный PowerShell Toast без сторонних библиотек
        ps_script = f"""
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $strings = $template.GetElementsByTagName("text")
        $strings[0].AppendChild($template.CreateTextNode("{title}")) > $null
        $strings[1].AppendChild($template.CreateTextNode("{message}")) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("GOAL 3.2").Show($toast)
        """
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_script],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception as e:
        logger.debug(f"Уведомление Windows не отобразилось: {e}")


# ==========================================
# 5. ОСНОВНОЙ ПАЙПЛАЙН АВТОПИЛОТА
# ==========================================

async def run_autopilot_check(test_mode: bool = False, force_all: bool = False) -> Dict[str, Any]:
    """
    Выполняет один полный цикл проверки расписания:
    1. Проверяет, не закрыты ли уже текущие выходные (если закрыты — мгновенный выход без запуска Chrome).
    2. Запускает Chrome (если нужно).
    3. Опрашивает Footballista API.
    4. Отсекает уже обработанные матчи.
    5. Если есть новые — создает обложки, эфиры на Rutube, привязывает видео.
    6. Сохраняет ключи в указанную папку.
    7. Отправляет отчет и файл ключей в Telegram с кнопкой персонального расчета WebApp.
    8. Оценивает укомплектованность выходных (Пт, Сб, Вс) и при получении всех матчей закрывает проверки.
    """
    now = datetime.datetime.now()
    logger.info("==========================================")
    logger.info(f"[АВТОПИЛОТ] Запуск проверки расписания ({now.strftime('%d.%m.%Y %H:%M:%S')})")

    # 1. Загрузка конфигурации
    ensure_config_exists()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tg_cfg = config.get("telegram", {})
    bot_token = tg_cfg.get("bot_token", "")
    primary_chat_id = tg_cfg.get("chat_id", "")
    operators = config.get("operators", [])
    target_chat_ids = [str(op["chat_id"]).strip() for op in operators if op.get("chat_id")]
    if not target_chat_ids and primary_chat_id:
        target_chat_ids = [str(primary_chat_id).strip()]

    tg_send_file = tg_cfg.get("send_file", True)
    webapp_url = tg_cfg.get("webapp_url", "https://the-dicer.github.io/GOOOOL/webapp/calculator.html")
    notifier = TelegramNotifier(bot_token, primary_chat_id)

    stream_keys_dir = config.get("stream_keys_dir", "stream_keys")
    stadium_colors = config.get("stadium_colors", {})
    desc_text = config.get("rutube_description", "")
    rutube_channel_id = config.get("rutube_channel_id", "77095292")
    autopilot_cfg = config.get("autopilot", {})
    close_chrome_after = autopilot_cfg.get("close_chrome_after", False)

    # 2. Быстрая проверка завершенности выходных ДО запуска Chrome
    friday_date, sunday_date, weekend_key = get_current_weekend_window()
    week_num = get_friday_week_number(friday_date)
    month_name = MONTH_NAMES_RU[friday_date.month]
    dates_display = f"{friday_date.strftime('%d.%m')} – {sunday_date.strftime('%d.%m.%Y')}"
    db = load_processed_matches()

    if not force_all and is_weekend_completed(weekend_key, db):
        w_info = db.get("completed_weekends", {}).get(weekend_key, {})
        days_str = ""
        if isinstance(w_info, dict):
            def_entry = w_info.get("default", w_info)
            days_str = f" ({', '.join(def_entry.get('days', []))})"
        msg = f"Выходные #{week_num} ({month_name}, {dates_display}){days_str} уже полностью закрыты. Проверка расписания пропускается."
        logger.info(msg)
        return {
            "status": "weekend_completed",
            "weekend_key": weekend_key,
            "message": msg
        }

    # 3. Подготовка браузера
    ready, started_by_us = await ensure_chrome_running(max_wait=20)
    if not ready:
        msg = "Ошибка: не удалось запустить Chrome для работы автопилота."
        logger.error(msg)
        if notifier.is_configured() and target_chat_ids:
            for cid in target_chat_ids:
                notifier.send_message(f"<b>GOAL Автопилот:</b> {msg}", chat_id=cid)
        return {"status": "chrome_error", "message": msg}

    # 4. Сбор матчей
    try:
        all_matches = await fetch_matches_for_ui(debug_30_matches=force_all)
    except Exception as e:
        logger.error(f"Ошибка сбора матчей: {e}")
        return {"status": "fetch_error", "message": str(e)}

    if not all_matches:
        logger.info("Матчи на предстоящие дни не найдены. Лига еще не опубликовала расписание.")
        return {"status": "no_matches", "count": 0}

    # 5. Фильтрация новых матчей
    new_matches = []
    for m in all_matches:
        if force_all or not is_match_already_processed(m, db):
            new_matches.append(m)
        else:
            logger.info(f"Пропуск (уже обработан): {m.stream_title}")

    success_count = 0
    keys_file = ""

    # 6. Если есть новые матчи — создаем трансляции
    if new_matches:
        logger.info(f"Найдено {len(new_matches)} новых матчей. Начинаем создание трансляций...")

        if notifier.is_configured():
            if operators:
                for op in operators:
                    cid = str(op.get("chat_id", "")).strip()
                    if not cid:
                        continue
                    op_name = op.get("name", "Оператор")
                    op_m = [m for m in new_matches if m.operator_chat_id == cid or m.operator_name == op_name]
                    if op_m:
                        notifier.send_message(
                            f"<b>GOAL Автопилот:</b> Обнаружено ваших новых матчей: <b>{len(op_m)}</b> ({op_name}).\n"
                            f"Запускаю создание трансляций и генерацию обложек...",
                            chat_id=cid
                        )
            elif target_chat_ids:
                for cid in target_chat_ids:
                    notifier.send_message(
                        f"<b>GOAL Автопилот:</b> Обнаружено новых матчей: <b>{len(new_matches)}</b>.\n"
                        f"Запускаю создание трансляций и генерацию обложек...",
                        chat_id=cid
                    )

        success_count, keys_file, results = await process_selected_matches(
            selected_matches=new_matches,
            pattern_mode="Автовыбор",
            test_mode=test_mode,
            league="AFL Moscow 8x8",
            default_color=3,
            desc_text=desc_text,
            stadium_colors=stadium_colors,
            stream_keys_dir=stream_keys_dir,
            rutube_channel_id=rutube_channel_id
        )

        # Запись созданных матчей в локальную базу
        record_processed_matches(results, db)

        # Персональная рассылка отчетов и ключей каждому оператору в Telegram
        send_operator_dispatch(
            results=results,
            master_keys_file=keys_file,
            config=config,
            dates_display=dates_display,
            week_num=week_num,
            month_name=month_name,
            friday_date=friday_date,
            sunday_date=sunday_date,
            notifier=notifier
        )
    else:
        logger.info(f"Все матчи ({len(all_matches)} шт.) уже имеют готовые трансляции. Новых игр нет.")

    # 7. Анализ укомплектованности выходных (Пт, Сб, Вс)
    weekend_matches = []
    weekend_days_found = set()
    day_name_map = {4: "Пт", 5: "Сб", 6: "Вс"}

    for m in all_matches:
        m_date = parse_match_date(m.match_date)
        if m_date and friday_date <= m_date <= sunday_date:
            weekend_matches.append(m)
            w_day = m_date.weekday()
            if w_day in day_name_map:
                weekend_days_found.add(day_name_map[w_day])

    all_weekend_done = bool(weekend_matches) and all(is_match_already_processed(m, db) for m in weekend_matches)

    # Условия завершения выходных:
    # 1. Присутствуют оба основных игровых дня (Суббота + Воскресенье), либо Пт+Сб+Вс
    has_sat_and_sun = ("Сб" in weekend_days_found and "Вс" in weekend_days_found)
    has_full_weekend = ("Пт" in weekend_days_found and "Сб" in weekend_days_found and "Вс" in weekend_days_found)
    # 2. Либо уже вечер субботы (>= 18:00) или воскресенье, и все назначенные игры созданы
    is_late_weekend = (now.weekday() == 5 and now.hour >= 18) or (now.weekday() == 6)

    should_close_weekend = all_weekend_done and (has_sat_and_sun or has_full_weekend or is_late_weekend)

    if should_close_weekend and not is_weekend_completed(weekend_key, db):
        order = ["Пт", "Сб", "Вс"]
        sorted_days = sorted(list(weekend_days_found), key=lambda d: order.index(d) if d in order else 99)
        mark_weekend_completed(weekend_key, len(weekend_matches), sorted_days, db)
        days_str = ", ".join(sorted_days)
        logger.info(f"Выходные #{week_num} ({month_name}, {dates_display}) полностью укомплектованы ({days_str}, матчей: {len(weekend_matches)}). Все дальнейшие проверки на этих выходных отключены.")

        if notifier.is_configured():
            if operators:
                for op in operators:
                    cid = str(op.get("chat_id", "")).strip()
                    if not cid:
                        continue
                    op_name = op.get("name", "Оператор")
                    op_m = [m for m in weekend_matches if m.operator_chat_id == cid or m.operator_name == op_name]
                    if op_m:
                        close_calc_url = build_calculator_url(webapp_url, friday_date, sunday_date, op_m)
                        close_markup = {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": f"Мой расчет оплаты ({len(op_m)} игр)",
                                        "web_app": {"url": close_calc_url}
                                    }
                                ]
                            ]
                        }
                        notifier.send_message(
                            f"<b>GOAL Автопилот: Выходные закрыты.</b>\n"
                            f"Оператор: <b>{op_name}</b>\n"
                            f"Период: <b>{dates_display}</b> ({month_name}, Выходные #{week_num})\n"
                            f"Всего ваших матчей: <b>{len(op_m)}</b> (дни: {days_str})\n\n"
                            f"Все ваши трансляции готовы. Проверки на эти выходные завершены. Ожидание следующего цикла.",
                            chat_id=cid,
                            reply_markup=close_markup
                        )
            elif target_chat_ids:
                close_calc_url = build_calculator_url(webapp_url, friday_date, sunday_date, weekend_matches)
                close_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Мой расчет оплаты",
                                "web_app": {"url": close_calc_url}
                            }
                        ]
                    ]
                }
                for cid in target_chat_ids:
                    notifier.send_message(
                        f"<b>GOAL Автопилот: Выходные закрыты.</b>\n"
                        f"Период: <b>{dates_display}</b> ({month_name}, Выходные #{week_num})\n"
                        f"Всего матчей: <b>{len(weekend_matches)}</b> (дни: {days_str})\n\n"
                        f"Все трансляции готовы. Проверки на эти выходные завершены. Ожидание следующего цикла.",
                        chat_id=cid,
                        reply_markup=close_markup
                    )

    logger.info("Проверка расписания завершена.")
    return {
        "status": "success",
        "new_count": len(new_matches),
        "total_matches": len(all_matches),
        "weekend_completed": is_weekend_completed(weekend_key, db)
    }
