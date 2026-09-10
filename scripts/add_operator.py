"""
Мастер добавления и управления операторами GOAL 3.2.

Позволяет:
1. Авторизовать второго оператора на Footballista через изолированное окно
   (основная сессия и профиль первого оператора не затрагиваются!).
2. Перехватить и проверить Bearer-токен авторизации Footballista через REST API.
3. Привязать Telegram второго оператора через бота @AFL_StreamCtreation_bot.
4. Отправить приветственное уведомление и сохранить оператора в config.json.
5. Управлять списком операторов (просмотр, обновление токена, тест отправки).
"""

import os
import sys
import json
import time
import asyncio
import urllib.request
import urllib.parse
import subprocess
from typing import Optional, Dict, Any, List, Tuple

# Установка безопасного вывода UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIG_FILE = "config.json"


def load_config() -> Dict[str, Any]:
    """Загрузка config.json с гарантией наличия структуры."""
    if not os.path.exists(CONFIG_FILE):
        print(f"[ОШИБКА] Файл {CONFIG_FILE} не найден в текущей директории.")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "operators" not in cfg:
        cfg["operators"] = []

    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Сохранение config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    print(f"[ИНФО] Настройки успешно сохранены в {CONFIG_FILE}")


def find_chrome_executable() -> str:
    """Поиск Chrome в стандартных путях Windows."""
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return "chrome.exe"


def is_chrome_running() -> bool:
    """Проверка доступности CDP порта 9222."""
    try:
        req = urllib.request.Request("http://localhost:9222/json/version")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_chrome_ready() -> bool:
    """Проверка или запуск Chrome с портом 9222."""
    if is_chrome_running():
        return True

    print("[ИНФО] Chrome на порту 9222 не обнаружен. Запускаю браузер...")
    chrome_path = find_chrome_executable()
    profile_path = os.path.abspath("chrome_debug_profile")
    os.makedirs(profile_path, exist_ok=True)

    cmd = [
        chrome_path,
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_path}",
        "https://footballista.ru/admin/games"
    ]
    subprocess.Popen(cmd)

    for i in range(15):
        time.sleep(1)
        if is_chrome_running():
            print("[ИНФО] Chrome успешно запущен и готов к работе.")
            time.sleep(1)
            return True
        print(f"  Ожидание подключения Chrome... ({i + 1}/15)")

    print("[ОШИБКА] Не удалось подключиться к Chrome на порту 9222.")
    return False


def validate_footballista_token(token: str) -> Tuple[bool, int, str]:
    """
    Проверяет валидность токена, делая запрос к Footballista API:
    GET https://footballista.ru/api/leagues/394/my_games
    Возвращает: (успех, количество_матчей, описание)
    """
    clean_token = token.strip()
    auth_header = clean_token if clean_token.startswith("Bearer ") else f"Bearer {clean_token}"

    url = "https://footballista.ru/api/leagues/394/my_games"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Authorization": auth_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        method="GET"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                items = data if isinstance(data, list) else (data.get("data") or [])
                return True, len(items), f"Токен валиден. Доступно матчей: {len(items)}"
            return False, 0, f"HTTP статус {resp.status}"
    except urllib.error.HTTPError as e:
        return False, 0, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, 0, str(e)


async def extract_token_from_page(page) -> Optional[str]:
    """Извлечение JWT токена из _ionicstorage или localStorage страницы."""
    js_extract = """async () => {
        async function getIonicData() {
            return new Promise((resolve) => {
                if (!window.indexedDB) return resolve(null);
                const req = indexedDB.open('_ionicstorage');
                req.onsuccess = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('_ionickv')) return resolve(null);
                    const tx = db.transaction('_ionickv', 'readonly');
                    const store = tx.objectStore('_ionickv');
                    const getReq = store.get('token');
                    getReq.onsuccess = () => resolve(getReq.result || null);
                    getReq.onerror = () => resolve(null);
                };
                req.onerror = () => resolve(null);
            });
        }

        let token = await getIonicData();
        if (token) return String(token);

        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            const v = localStorage.getItem(k);
            if (v && v.startsWith('eyJ')) {
                return v;
            }
        }
        return null;
    }"""
    try:
        raw = await page.evaluate(js_extract)
        if raw and isinstance(raw, str) and len(raw) > 20:
            token = raw.replace('"', '').replace("'", '').strip()
            return token if token.startswith("Bearer ") else f"Bearer {token}"
    except Exception:
        pass
    return None


async def auto_register_operator_1_if_needed(cfg: Dict[str, Any]) -> None:
    """
    Если в config['operators'] еще нет записей, автоматически переносит
    существующую сессию Оператора 1 из браузера и chat_id из config['telegram'].
    """
    operators = cfg.get("operators", [])
    if operators:
        return

    primary_chat_id = str(cfg.get("telegram", {}).get("chat_id", "")).strip()
    if not primary_chat_id:
        return

    print("--------------------------------------------------")
    print("[ИНФО] В конфигурации еще нет списка операторов.")
    print(f"[ИНФО] Обнаружен основной Chat ID: {primary_chat_id}")
    print("[ИНФО] Попытка извлечь токен Оператора 1 из текущей сессии Chrome...")

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = None
            for p_item in context.pages:
                if "footballista.ru" in p_item.url:
                    page = p_item
                    break
            if not page:
                page = await context.new_page()
                await page.goto("https://footballista.ru/admin/games")
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)

            token = await extract_token_from_page(page)
            if token:
                valid, cnt, desc = validate_footballista_token(token)
                if valid:
                    op1 = {
                        "name": "Оператор 1 (Основной)",
                        "chat_id": primary_chat_id,
                        "footballista_token": token
                    }
                    cfg["operators"] = [op1]
                    save_config(cfg)
                    print(f"[УСПЕХ] Оператор 1 (Основной) автоматически зарегистрирован! ({desc})")
                    return
    except Exception as e:
        print(f"[ПРЕДУПРЕЖДЕНИЕ] Не удалось автоматически зафиксировать токен Оператора 1: {e}")


def get_known_chat_ids(cfg: Dict[str, Any]) -> List[str]:
    """Сбор всех уже зарегистрированных Chat ID."""
    ids = []
    primary = str(cfg.get("telegram", {}).get("chat_id", "")).strip()
    if primary:
        ids.append(primary)
    for op in cfg.get("operators", []):
        cid = str(op.get("chat_id", "")).strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def poll_new_telegram_chat(bot_token: str, known_ids: List[str], timeout_sec: int = 120) -> Optional[Dict[str, Any]]:
    """
    Ожидание входящего сообщения от нового пользователя в Telegram боте.
    """
    base_url = f"https://api.telegram.org/bot{bot_token}"
    print(f"  Ожидание сообщения в Telegram боте (таймаут {timeout_sec} сек)...")

    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        try:
            url = f"{base_url}/getUpdates"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                for upd in reversed(data.get("result", [])):
                    msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member")
                    if msg and "chat" in msg:
                        cid = str(msg["chat"].get("id", "")).strip()
                        if cid and cid not in known_ids:
                            first_name = msg["chat"].get("first_name", "")
                            last_name = msg["chat"].get("last_name", "")
                            full_name = f"{first_name} {last_name}".strip() or msg["chat"].get("title", "") or "Пользователь"
                            username = msg["chat"].get("username", "")
                            return {
                                "chat_id": cid,
                                "name": full_name,
                                "username": f"@{username}" if username else ""
                            }
        except Exception:
            pass

        time.sleep(2)

    return None


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Отправка сообщения в Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[ОШИБКА] Не удалось отправить сообщение в Telegram: {e}")
        return False


async def onboard_operator_flow(cfg: Dict[str, Any], custom_name: Optional[str] = None) -> bool:
    """
    Полный пошаговый процесс подключения нового оператора:
    1. Изолированный вход на Footballista (без разлогинивания первого оператора).
    2. Перехват токена.
    3. Привязка Telegram Chat ID.
    4. Сохранение в config.json.
    """
    print("\n==================================================")
    print("      ДОБАВЛЕНИЕ НОВОГО ОПЕРАТОРА В СИСТЕМУ")
    print("==================================================")

    if not ensure_chrome_ready():
        return False

    # Авто-регистрация первого оператора если список пуст
    await auto_register_operator_1_if_needed(cfg)

    # Имя оператора
    if not custom_name:
        default_num = len(cfg.get("operators", [])) + 1
        name_input = input(f"Введите имя нового оператора [Оператор {default_num}]: ").strip()
        op_name = name_input or f"Оператор {default_num}"
    else:
        op_name = custom_name

    print(f"\n[ЭТАП 1/2] Авторизация на Footballista для: '{op_name}'")
    print("--------------------------------------------------")
    print("ВНИМАНИЕ:")
    print("1. Сейчас откроется ОТДЕЛЬНОЕ изолированное окно входа Footballista.")
    print("2. Основной профиль первого оператора остается нетронутым.")
    print("3. В открывшемся окне введите номер телефона и SMS-код второго оператора.")
    print("4. Скрипт сам зафиксирует токен сразу после успешного входа.")
    print("--------------------------------------------------")

    token_captured = None

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")

        # СОЗДАЕМ ИЗОЛИРОВАННЫЙ INCOGNITO КОНТЕКСТ ДЛЯ ВТОРОГО ОПЕРАТОРА
        incognito_context = await browser.new_context()
        auth_page = await incognito_context.new_page()

        print("[ИНФО] Загрузка страницы входа https://footballista.ru/admin/games...")
        await auth_page.goto("https://footballista.ru/admin/games")

        print("[ИНФО] Окно открыто. Ожидание ввода номера и SMS-кода (до 300 сек)...")
        start_wait = time.time()
        while time.time() - start_wait < 300:
            token = await extract_token_from_page(auth_page)
            if token:
                valid, match_count, desc = validate_footballista_token(token)
                if valid:
                    print(f"\n[УСПЕХ] Токен Footballista успешно получен и проверен!")
                    print(f"[ИНФО] Назначено матчей на аккаунте: {match_count}")
                    token_captured = token
                    break
                else:
                    print(f"[ИНФО] Обнаружен токен, но проверка API еще не прошла: {desc}. Ждем...")
            await asyncio.sleep(2)

        # Закрываем изолированный контекст, профиль Оператора 1 чист
        await auth_page.close()
        await incognito_context.close()

    if not token_captured:
        print("\n[ОШИБКА] Время ожидания входа истекло или токен не был получен.")
        return False

    # ЭТАП 2: TELEGRAM
    print(f"\n[ЭТАП 2/2] Привязка Telegram для: '{op_name}'")
    print("--------------------------------------------------")
    bot_token = cfg.get("telegram", {}).get("bot_token", "").strip()
    if not bot_token:
        print("[ОШИБКА] В config.json отсутствует telegram.bot_token. Привязка Telegram невозможна.")
        return False

    known_ids = get_known_chat_ids(cfg)
    print("Попросите оператора открыть Telegram, найти бота:")
    print("   @AFL_StreamCtreation_bot")
    print("и нажать СТАРТ (/start) или отправить любое сообщение.")
    print("--------------------------------------------------")

    tg_user = poll_new_telegram_chat(bot_token, known_ids, timeout_sec=180)
    if not tg_user:
        print("\n[ПРЕДУПРЕЖДЕНИЕ] Не удалось зафиксировать новое сообщение в Telegram боте.")
        chat_id_input = input("Вы можете ввести Telegram Chat ID вручную (или Enter для пропуска): ").strip()
        if chat_id_input:
            tg_user = {"chat_id": chat_id_input, "name": op_name, "username": ""}
        else:
            print("[ИНФО] Оператор сохранен без привязки Telegram.")
            tg_user = {"chat_id": "", "name": op_name, "username": ""}

    new_chat_id = tg_user.get("chat_id", "")
    if new_chat_id:
        print(f"[УСПЕХ] Telegram пользователя обнаружен: {tg_user.get('name')} (ID: {new_chat_id})")
        # Отправляем приветственное сообщение
        welcome_text = (
            f"<b>Здравствуйте, {tg_user.get('name')}!</b>\n\n"
            f"Вы успешно зарегистрированы в системе GOAL 3.2 как оператор трансляций: <b>{op_name}</b>.\n"
            f"Сюда будут поступать стрим-ключи и отчеты по созданным матчам."
        )
        send_telegram_message(bot_token, new_chat_id, welcome_text)

    # СОХРАНЕНИЕ В CONFIG
    new_operator = {
        "name": op_name,
        "chat_id": new_chat_id,
        "footballista_token": token_captured
    }

    operators = cfg.get("operators", [])
    # Если оператор с таким именем уже был — обновляем, иначе добавляем
    updated = False
    for idx, existing_op in enumerate(operators):
        if existing_op.get("name") == op_name:
            operators[idx] = new_operator
            updated = True
            break
    if not updated:
        operators.append(new_operator)

    cfg["operators"] = operators
    save_config(cfg)

    print("\n==================================================")
    print(f"[ГОТОВО] Оператор '{op_name}' успешно зарегистрирован в GOAL 3.2!")
    print("==================================================")
    print_operators_table(cfg)
    return True


def print_operators_table(cfg: Dict[str, Any]) -> None:
    """Вывод красивой таблицы зарегистрированных операторов."""
    operators = cfg.get("operators", [])
    print("\n----------------------------------------------------------------------")
    print(f"{'#':<3} | {'Имя оператора':<25} | {'Telegram Chat ID':<16} | {'Токен Footballista':<15}")
    print("----------------------------------------------------------------------")
    if not operators:
        print("  Список операторов пуст.")
    for idx, op in enumerate(operators, 1):
        name = op.get("name", "Не указано")[:25]
        cid = str(op.get("chat_id", "Нет"))[:16]
        tok = op.get("footballista_token", "")
        tok_status = "Активен" if tok else "Отсутствует"
        print(f"{idx:<3} | {name:<25} | {cid:<16} | {tok_status:<15}")
    print("----------------------------------------------------------------------\n")


async def refresh_operator_token(cfg: Dict[str, Any]) -> None:
    """Обновление токена Footballista для существующего оператора."""
    operators = cfg.get("operators", [])
    if not operators:
        print("[ИНФО] Нет зарегистрированных операторов.")
        return

    print("\nВыберите оператора для обновления токена Footballista:")
    for idx, op in enumerate(operators, 1):
        print(f"  {idx}. {op.get('name')} (Chat ID: {op.get('chat_id')})")
    print("  0. Отмена")

    choice = input("Номер оператора: ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(operators):
        print("Отмена.")
        return

    target_op = operators[int(choice) - 1]
    op_name = target_op.get("name")

    if not ensure_chrome_ready():
        return

    print(f"\n[ИНФО] Открываю окно авторизации для '{op_name}'...")
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        incognito_context = await browser.new_context()
        auth_page = await incognito_context.new_page()
        await auth_page.goto("https://footballista.ru/admin/games")

        print(f"Введите номер и SMS-код в окне для '{op_name}'...")
        start_wait = time.time()
        new_token = None
        while time.time() - start_wait < 300:
            tok = await extract_token_from_page(auth_page)
            if tok:
                valid, count, desc = validate_footballista_token(tok)
                if valid:
                    new_token = tok
                    print(f"[УСПЕХ] Новый токен подтвержден! Матчей: {count}")
                    break
            await asyncio.sleep(2)

        await auth_page.close()
        await incognito_context.close()

    if new_token:
        target_op["footballista_token"] = new_token
        save_config(cfg)
        print(f"[ГОТОВО] Токен для '{op_name}' успешно обновлен.")
    else:
        print("[ОШИБКА] Токен не был получен.")


def remove_operator(cfg: Dict[str, Any]) -> None:
    """Удаление оператора из config.json."""
    operators = cfg.get("operators", [])
    if not operators:
        print("[ИНФО] Нет зарегистрированных операторов.")
        return

    print("\nВыберите оператора для удаления:")
    for idx, op in enumerate(operators, 1):
        print(f"  {idx}. {op.get('name')} (Chat ID: {op.get('chat_id')})")
    print("  0. Отмена")

    choice = input("Номер оператора: ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(operators):
        print("Отмена.")
        return

    removed = operators.pop(int(choice) - 1)
    cfg["operators"] = operators
    save_config(cfg)
    print(f"[УСПЕХ] Оператор '{removed.get('name')}' удален.")


def test_telegram_broadcast(cfg: Dict[str, Any]) -> None:
    """Отправка тестового сообщения всем операторам."""
    bot_token = cfg.get("telegram", {}).get("bot_token", "").strip()
    if not bot_token:
        print("[ОШИБКА] В config.json отсутствует bot_token.")
        return

    operators = cfg.get("operators", [])
    chat_ids = [str(op.get("chat_id")).strip() for op in operators if op.get("chat_id")]
    primary = str(cfg.get("telegram", {}).get("chat_id", "")).strip()
    if primary and primary not in chat_ids:
        chat_ids.append(primary)

    if not chat_ids:
        print("[ПРЕДУПРЕЖДЕНИЕ] Нет ни одного Chat ID для отправки.")
        return

    text = (
        "<b>Тест оповещений GOAL 3.2</b>\n\n"
        "Интеграция с Telegram для нескольких операторов активна.\n"
        "Сюда будут поступать ключи трансляций и отчеты."
    )
    for cid in chat_ids:
        ok = send_telegram_message(bot_token, cid, text)
        status = "УСПЕШНО" if ok else "ОШИБКА"
        print(f"  Чат {cid}: [{status}]")


async def async_main():
    cfg = load_config()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--add", "-a"):
            name = sys.argv[2] if len(sys.argv) > 2 else None
            await onboard_operator_flow(cfg, custom_name=name)
            return
        elif arg in ("--list", "-l"):
            print_operators_table(cfg)
            return
        elif arg in ("--test", "-t"):
            test_telegram_broadcast(cfg)
            return

    # Интерактивное меню
    while True:
        print("\n==================================================")
        print("     GOAL 3.2: УПРАВЛЕНИЕ ОПЕРАТОРАМИ")
        print("==================================================")
        print("1. Добавить второго/нового оператора (Footballista + Telegram)")
        print("2. Показать список зарегистрированных операторов")
        print("3. Обновить токен Footballista для оператора")
        print("4. Удалить оператора из системы")
        print("5. Отправить тестовое сообщение всем операторам в Telegram")
        print("0. Выход")
        print("--------------------------------------------------")

        choice = input("Выберите пункт меню [0-5]: ").strip()
        if choice == "1":
            await onboard_operator_flow(cfg)
        elif choice == "2":
            print_operators_table(cfg)
        elif choice == "3":
            await refresh_operator_token(cfg)
        elif choice == "4":
            remove_operator(cfg)
        elif choice == "5":
            test_telegram_broadcast(cfg)
        elif choice in ("0", "q", "exit"):
            print("Выход.")
            break
        else:
            print("Неверный выбор. Повторите.")


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nОперация прервана пользователем.")


if __name__ == "__main__":
    main()
