import os
import sys
import time
import json
import logging
import asyncio
import argparse
import datetime

# Гарантируем, что рабочий каталог всегда указывает на корень проекта GOAL
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Добавляем пути и кодировку для корректной работы в консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Настройка логирования в консоль и в файл логов
os.makedirs("logs", exist_ok=True)
log_format = "%(asctime)s | %(levelname)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/automation.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("auto_runner")

from automation import (
    TelegramNotifier,
    run_autopilot_check,
    CONFIG_FILE,
    ensure_config_exists,
    load_processed_matches,
    is_match_already_processed,
    get_current_weekend_window,
    is_weekend_completed,
    reset_weekend_status,
    send_custom_period_report,
    parse_match_date
)
from main import fetch_matches_for_ui


def test_telegram():
    """Тестирование отправки сообщения в Telegram."""
    ensure_config_exists()
    if not os.path.exists(CONFIG_FILE):
        logger.error("Файл config.json не найден.")
        return

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    tg_cfg = cfg.get("telegram", {})
    token = tg_cfg.get("bot_token", "")
    chat_id = tg_cfg.get("chat_id", "")

    if not token:
        logger.error("В config.json не указан bot_token для Telegram.")
        return

    notifier = TelegramNotifier(token, chat_id)

    if not chat_id:
        logger.info("Chat ID не указан. Пытаюсь автоматически определить из последних сообщений боту...")
        detected_id = notifier.get_last_chat_id()
        if detected_id:
            logger.info(f"Найден Chat ID: {detected_id}. Сохраняю в config.json...")
            tg_cfg["chat_id"] = detected_id
            cfg["telegram"] = tg_cfg
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            chat_id = detected_id
            notifier.default_chat_id = detected_id
        else:
            logger.warning("Не удалось найти Chat ID. Напишите в Telegram боту любое сообщение (или /start) и повторите команду.")
            return

    logger.info(f"Отправка тестового сообщения в чат {chat_id}...")
    success = notifier.send_message(
        "<b>Привет! Это бот GOAL.</b>\n\n"
        "Интеграция с Telegram успешно настроена.\n"
        "Сюда будут приходить отчеты и файлы со стрим-ключами при публикации трансляций."
    )
    if success:
        logger.info("Тестовое сообщение доставлено успешно.")
    else:
        logger.error("Не удалось доставить сообщение. Проверьте правильность токена и chat_id.")


def auto_detect_chat_id():
    """Поиск и сохранение chat_id."""
    ensure_config_exists()
    if not os.path.exists(CONFIG_FILE):
        logger.error("Файл config.json не найден.")
        return

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    tg_cfg = cfg.get("telegram", {})
    token = tg_cfg.get("bot_token", "")
    if not token:
        logger.error("Токен бота не задан в config.json.")
        return

    notifier = TelegramNotifier(token)
    detected_id = notifier.get_last_chat_id()
    if detected_id:
        logger.info(f"Успешно обнаружен Chat ID: {detected_id}")
        tg_cfg["chat_id"] = detected_id
        cfg["telegram"] = tg_cfg
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        logger.info("Chat ID сохранен в config.json!")
    else:
        logger.warning("Сообщений боту пока не поступало. Откройте диалог с ботом в Telegram, отправьте /start и попробуйте снова.")


async def run_check_only():
    """Проверка расписания без создания трансляций."""
    logger.info("Режим проверки расписания (без публикации)...")
    _, _, weekend_key = get_current_weekend_window()
    db = load_processed_matches()
    is_done = is_weekend_completed(weekend_key, db)
    if is_done:
        logger.info(f"Текущие выходные '{weekend_key}' уже помечены как ЗАВЕРШЕННЫЕ в базе.")
    else:
        logger.info(f"Текущие выходные '{weekend_key}' в процессе сбора матчей.")

    from automation import ensure_chrome_running
    ready, started = await ensure_chrome_running(max_wait=15)
    if not ready:
        logger.error("Не удалось подключиться к Chrome.")
        return

    matches = await fetch_matches_for_ui(debug_30_matches=False)
    
    new_matches = [m for m in matches if not is_match_already_processed(m, db)]
    logger.info(f"Всего актуальных матчей в Footballista: {len(matches)}")
    logger.info(f"Новых матчей без трансляций: {len(new_matches)}")
    for i, m in enumerate(new_matches, 1):
        logger.info(f"  {i}. {m.stream_title} ({m.match_date}, {m.stadium})")


def run_daemon_loop():
    """Фоновый режим непрерывного мониторинга."""
    logger.info("Запуск фонового мониторинга расписания (режим демона)...")
    while True:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            ap_cfg = cfg.get("autopilot", {})
            interval_min = ap_cfg.get("check_interval_minutes", 30)
            active_days = ap_cfg.get("active_days", ["Friday", "Saturday"])

            # Проверяем день недели, если задано ограничение
            today_name = datetime.datetime.now().strftime("%A")
            is_active_day = not active_days or (today_name in active_days)

            if is_active_day:
                logger.info(f"Сегодня {today_name} (день проверки). Запускаю анализ расписания...")
                asyncio.run(run_autopilot_check(test_mode=False))
            else:
                logger.info(f"Сегодня {today_name}. Активные дни проверки: {active_days}. Ожидание...")

            logger.info(f"Следующая проверка через {interval_min} минут.")
            time.sleep(interval_min * 60)

        except KeyboardInterrupt:
            logger.info("Мониторинг остановлен пользователем.")
            break
        except Exception as e:
            logger.error(f"Ошибка в цикле мониторинга: {e}")
            time.sleep(60)


def handle_report_command(report_args: Optional[List[str]]):
    """Формирование и отправка отчета за произвольный период (Failsafe)."""
    today = datetime.datetime.now().date()
    start_date = None
    end_date = None

    if not report_args or len(report_args) == 0:
        # Без аргументов: последние 14 дней
        end_date = today
        start_date = today - datetime.timedelta(days=14)
    elif len(report_args) == 1:
        arg = report_args[0].strip()
        if arg.isdigit():
            days = int(arg)
            end_date = today
            start_date = today - datetime.timedelta(days=days)
        elif "-" in arg and not arg.startswith("20"):
            parts = arg.split("-")
            if len(parts) == 2:
                start_date = parse_match_date(parts[0])
                end_date = parse_match_date(parts[1])
        elif ":" in arg:
            parts = arg.split(":")
            if len(parts) == 2:
                start_date = parse_match_date(parts[0])
                end_date = parse_match_date(parts[1])
        else:
            dt = parse_match_date(arg)
            if dt:
                start_date = dt
                end_date = dt
    elif len(report_args) >= 2:
        start_date = parse_match_date(report_args[0])
        end_date = parse_match_date(report_args[1])

    if not start_date or not end_date:
        logger.error("Не удалось распознать даты. Примеры: --report 01.09.2026 15.09.2026 или --report 14")
        return

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    logger.info(f"Формирование failsafe-отчета за период: {start_date.strftime('%d.%m.%Y')} – {end_date.strftime('%d.%m.%Y')}...")
    ok, msg = send_custom_period_report(start_date, end_date)
    if ok:
        logger.info(f"[OK] {msg}")
    else:
        logger.error(f"[ОШИБКА] {msg}")


def main():
    parser = argparse.ArgumentParser(description="GOAL 3.2: Автономный автопилот расписания и трансляций")
    parser.add_argument("--test-tg", action="store_true", help="Проверить отправку сообщения в Telegram")
    parser.add_argument("--get-chat-id", action="store_true", help="Найти Chat ID из последних сообщений боту")
    parser.add_argument("--check-only", action="store_true", help="Только проверить расписание без создания стримов")
    parser.add_argument("--force", action="store_true", help="Принудительно обработать матчи (игнорируя статус закрытых выходных)")
    parser.add_argument("--reset-weekend", action="store_true", help="Сбросить статус завершенности текущих выходных")
    parser.add_argument("--test-mode", action="store_true", help="Тестовый режим (без Footballista)")
    parser.add_argument("--watch", action="store_true", help="Запустить в бесконечном цикле мониторинга")
    parser.add_argument("--report", nargs="*", help="Сформировать отчет за произвольный период (например: --report 01.09.2026 15.09.2026 или --report 14)")

    args = parser.parse_args()

    if args.test_tg:
        test_telegram()
    elif args.get_chat_id:
        auto_detect_chat_id()
    elif args.reset_weekend:
        _, _, weekend_key = get_current_weekend_window()
        reset_weekend_status(weekend_key)
        logger.info(f"Статус выходных '{weekend_key}' сброшен. Теперь проверки снова активны.")
    elif args.report is not None:
        handle_report_command(args.report)
    elif args.check_only:
        asyncio.run(run_check_only())
    elif args.watch:
        run_daemon_loop()
    else:
        # Стандартный однократный запуск для Планировщика Windows
        res = asyncio.run(run_autopilot_check(test_mode=args.test_mode, force_all=args.force))
        logger.info(f"Результат выполнения: {res}")


if __name__ == "__main__":
    main()
