import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import asyncio
import logging
import subprocess
import os
import urllib.request
import json
import datetime

# Исключаем локальные адреса из системного прокси, чтобы Playwright не слал локальный CDP трафик в прокси
for _proxy_key in ["NO_PROXY", "no_proxy"]:
    _curr = os.environ.get(_proxy_key, "")
    _proxies = [p.strip() for p in _curr.split(",") if p.strip()]
    for _h in ["localhost", "127.0.0.1", "::1"]:
        if _h not in _proxies:
            _proxies.append(_h)
    os.environ[_proxy_key] = ",".join(_proxies)

from main import fetch_matches_for_ui, process_selected_matches
from automation import (
    TelegramNotifier,
    run_autopilot_check,
    load_processed_matches,
    record_processed_matches,
    is_match_already_processed,
    get_current_weekend_window,
    is_weekend_completed,
    reset_weekend_status,
    send_custom_period_report,
    parse_match_date,
    send_operator_dispatch,
    CONFIG_FILE
)
try:
    from scripts.install_scheduler_task import install_task, remove_task
except ImportError:
    try:
        from install_scheduler_task import install_task, remove_task
    except ImportError:
        install_task, remove_task = None, None

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "config.json"


class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)

        def append():
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg + '\n')
            self.text_widget.configure(state='disabled')
            self.text_widget.yview(tk.END)

        self.text_widget.after(0, append)


def is_chrome_running():
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=1)
        return True
    except Exception:
        return False


def launch_chrome():
    try:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_path):
            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        profile_path = os.path.join(os.getcwd(), "chrome_debug_profile")
        os.makedirs(profile_path, exist_ok=True)
        subprocess.Popen([chrome_path, "--remote-debugging-port=9222", "--remote-allow-origins=*", f"--user-data-dir={profile_path}"])
        logging.info("Chrome запущен. Авторизуйтесь на нужных сайтах.")
    except Exception as e:
        logging.error(f"Не удалось запустить Chrome: {e}")


class AFLPublisherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("GOAL 3.2")
        self.geometry("1180x840")
        for icon_path in ["assets/icon.ico", "icon.ico"]:
            if os.path.exists(icon_path):
                try:
                    self.iconbitmap(icon_path)
                    break
                except Exception:
                    pass

        self.pipeline_task = None
        self.checkbox_vars = []

        self.test_mode_var = ctk.BooleanVar(value=False)
        self.debug_30_var = ctk.BooleanVar(value=False)
        self.pattern_var = ctk.StringVar(value="Автовыбор")
        self.select_all_var = ctk.BooleanVar(value=True)
        self.league_var = ctk.StringVar(value="AFL Moscow 8x8")
        self.default_color_var = ctk.StringVar(value="3")
        self.rutube_description_text = ""
        self.stadium_colors = {}
        self.last_browser_state = None

        # Настройки папки ключей и Telegram
        self.stream_keys_dir_var = ctk.StringVar(value="stream_keys")
        self.tg_bot_token_var = ctk.StringVar(value="8780587668:AAEm_d4JqgggAySYl9g7GsNCdItsWpe7wPA")
        self.tg_chat_id_var = ctk.StringVar(value="")
        self.tg_send_file_var = ctk.BooleanVar(value=True)
        self.rutube_channel_id_var = ctk.StringVar(value="77095292")

        # Автопилот внутри GUI
        self.autopilot_enabled_var = ctk.BooleanVar(value=False)
        self.autopilot_interval_var = ctk.StringVar(value="120")
        self.autopilot_friday_only_var = ctk.BooleanVar(value=True)
        self.autopilot_next_check = None
        self.autopilot_running = False

        self.load_config()
        self.build_ui()
        self.check_browser_status()
        self.check_autopilot_tick()

    def load_config(self):
        default_desc =  "Заявляйся в AFL!\n\n+7 (916) 739-96-23\nhttps://vk.com/lkuka\n\nТелеграм AFL — https://t.me/aflrussiа\n\nAFL VK – https://vk.com/aflmoscow\n\nInstagram* AFL – платформа запрещена на территории РФ https://instagram.com/afl_russia\n\nПриложение AFL:\n\nIphone — https://apps.apple.com/ru/app/afl/id1555695558\n\nAndroid — https://play.google.com/store/apps/details?id=com.foo"

        default_stadiums = {
            "труд": "3", "ясенево": "24", "терехово": "19",
            "конструктор": "13", "дело спорта": "13", "тушино": "4",
            "октябрь": "4", "братиславский": "5", "торпедо": "22",
            "олимпийская": "9", "красносельская": "11", "балашиха": "15", "mfl": "24"
        }

        if not os.path.exists(CONFIG_FILE):
            default_config = {
                "rutube_description": default_desc,
                "stadium_colors": default_stadiums
            }
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=4)
                logging.info(f"Создан дефолтный файл конфигурации {CONFIG_FILE}")
            except Exception as e:
                print(f"Ошибка создания конфига: {e}")

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            self.rutube_description_text = config.get("rutube_description", default_desc)
            self.stadium_colors = config.get("stadium_colors", default_stadiums)

            # Папка ключей
            self.stream_keys_dir_var.set(config.get("stream_keys_dir", "stream_keys"))

            # Telegram
            tg = config.get("telegram", {})
            self.tg_bot_token_var.set(tg.get("bot_token", "8780587668:AAEm_d4JqgggAySYl9g7GsNCdItsWpe7wPA"))
            self.tg_chat_id_var.set(tg.get("chat_id", ""))
            self.tg_send_file_var.set(tg.get("send_file", True))

            # Rutube канал лиги (ID)
            self.rutube_channel_id_var.set(config.get("rutube_channel_id", "77095292"))

            # Автопилот
            ap = config.get("autopilot", {})
            self.autopilot_enabled_var.set(ap.get("enabled", False))
            self.autopilot_interval_var.set(str(ap.get("check_interval_minutes", 30)))
            self.autopilot_friday_only_var.set("Friday" in ap.get("active_days", ["Friday", "Saturday"]))
            # Операторы
            self.operators = config.get("operators", [])

        except Exception as e:
            logging.error(f"Не удалось загрузить {CONFIG_FILE}: {e}")

    def save_config(self):
        if hasattr(self, "textbox_desc") and self.textbox_desc.winfo_exists():
            self.rutube_description_text = self.textbox_desc.get("1.0", tk.END).strip()

        current_operators = getattr(self, "operators", [])
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    current_operators = file_cfg.get("operators", current_operators)
            except Exception:
                pass

        config_data = {
            "rutube_description": self.rutube_description_text,
            "rutube_channel_id": self.rutube_channel_id_var.get().strip() or "77095292",
            "stadium_colors": self.stadium_colors,
            "stream_keys_dir": self.stream_keys_dir_var.get().strip() or "stream_keys",
            "telegram": {
                "enabled": bool(self.tg_bot_token_var.get().strip()),
                "bot_token": self.tg_bot_token_var.get().strip(),
                "chat_id": self.tg_chat_id_var.get().strip(),
                "send_file": self.tg_send_file_var.get()
            },
            "operators": current_operators,
            "autopilot": {
                "enabled": self.autopilot_enabled_var.get(),
                "check_interval_minutes": int(self.autopilot_interval_var.get() or 30),
                "active_days": ["Friday", "Saturday"] if self.autopilot_friday_only_var.get() else [],
                "close_chrome_after": False
            }
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"Ошибка сохранения файла конфигурации: {e}")

    def add_stadium_color(self):
        stadium = self.entry_new_stadium.get().strip()
        color = self.entry_new_color.get().strip()

        if stadium and color.isdigit():
            self.stadium_colors[stadium.lower()] = color
            self.entry_new_stadium.delete(0, 'end')
            self.entry_new_color.delete(0, 'end')
            self.save_config()
            self.refresh_colors_list()
            logging.info(f"Добавлен стадион '{stadium}' с цветом {color}")
        else:
            logging.warning("Ошибка: Введите название стадиона и ЧИСЛОВОЙ номер цвета.")

    def delete_stadium_color(self, stadium):
        if stadium in self.stadium_colors:
            del self.stadium_colors[stadium]
            self.save_config()
            self.refresh_colors_list()
            logging.info(f"Стадион '{stadium}' удален из базы.")

    def refresh_colors_list(self):
        for widget in self.colors_scroll.winfo_children():
            widget.destroy()

        for idx, (stadium, color) in enumerate(self.stadium_colors.items()):
            row = ctk.CTkFrame(self.colors_scroll, fg_color="#333333", height=40)
            row.pack(fill="x", pady=2, padx=5)

            lbl_stadium = ctk.CTkLabel(row, text=stadium.capitalize(), font=("Arial", 14, "bold"), anchor="w",
                                       width=250)
            lbl_stadium.pack(side="left", padx=15, pady=5)

            lbl_color = ctk.CTkLabel(row, text=f"Цвет №: {color}", font=("Arial", 13), width=100)
            lbl_color.pack(side="left", padx=15, pady=5)

            btn_del = ctk.CTkButton(row, text="Удалить", fg_color="#D32F2F", hover_color="#C62828", width=80,
                                    command=lambda s=stadium: self.delete_stadium_color(s))
            btn_del.pack(side="right", padx=15, pady=5)

    def build_ui(self):
        # Жестко фиксируем нулевую колонку сайдбара (ширина 260px, не растягивается)
        self.grid_columnconfigure(0, weight=0, minsize=260)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # === ЛЕВАЯ ПАНЕЛЬ (САЙДБАР) ===
        self.sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(self.sidebar, text="Управление", font=("Arial", 20, "bold")).pack(pady=(20, 10))

        self.btn_chrome = ctk.CTkButton(self.sidebar, text="1. Запустить Chrome", fg_color="#2E7D32",
                                        hover_color="#1B5E20", height=40, command=launch_chrome)
        self.btn_chrome.pack(pady=10, padx=20, fill="x")

        self.lbl_status = ctk.CTkLabel(self.sidebar, text="Браузер: Ожидание...", text_color="orange")
        self.lbl_status.pack(pady=(0, 15))

        self.btn_fetch = ctk.CTkButton(self.sidebar, text="2. Собрать расписание", font=("Arial", 14, "bold"),
                                       fg_color="#F57C00", hover_color="#E65100", height=40, state="disabled",
                                       command=self.start_fetch)
        self.btn_fetch.pack(pady=(0, 10), padx=20, fill="x")

        # Контейнер для главных кнопок (теперь без привязки ко дну)
        action_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        action_frame.pack(fill="x", pady=(10, 20))

        self.btn_publish = ctk.CTkButton(action_frame, text="ОПУБЛИКОВАТЬ", font=("Arial", 16, "bold"), height=50,
                                         state="disabled", command=self.start_publish)
        self.btn_publish.pack(pady=(0, 10), padx=20, fill="x")

        self.btn_stop = ctk.CTkButton(action_frame, text="СТОП", fg_color="#D32F2F", hover_color="#C62828", height=40,
                                      state="disabled", command=self.stop_automation)
        self.btn_stop.pack(padx=20, fill="x")

        self.btn_custom_report = ctk.CTkButton(
            action_frame,
            text="Отчет за период (Failsafe)",
            fg_color="#1E3A5F",
            hover_color="#152A45",
            height=36,
            command=self.open_custom_report_dialog
        )
        self.btn_custom_report.pack(pady=(12, 0), padx=20, fill="x")

        # === ПРАВАЯ ЧАСТЬ (ВКЛАДКИ) ===
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)

        # Создаем ПЯТЬ вкладок
        self.tab_matches = self.tabview.add("Матчи")
        self.tab_autopilot = self.tabview.add("Автопилот & TG")
        self.tab_settings = self.tabview.add("Настройки")
        self.tab_colors = self.tabview.add("Цвета стадионов")
        self.tab_test = self.tabview.add("Параметры запуска")

        # --- ВКЛАДКА 1: МАТЧИ И ЛОГИ ---
        self.tab_matches.grid_columnconfigure(0, weight=1)
        self.tab_matches.grid_rowconfigure(1, weight=3)
        self.tab_matches.grid_rowconfigure(3, weight=1)

        header_frame = ctk.CTkFrame(self.tab_matches, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", pady=(5, 5))

        self.cb_select_all = ctk.CTkCheckBox(header_frame, text="Выбрать всё", variable=self.select_all_var,
                                             command=self.toggle_all_matches)
        self.cb_select_all.pack(side="left", padx=5)

        # Используем встроенный оптимизированный скролл-фрейм
        self.scroll_matches = ctk.CTkScrollableFrame(self.tab_matches, fg_color="#2B2B2B")
        self.scroll_matches.grid(row=1, column=0, sticky="nsew", pady=(5, 10))

        log_header_frame = ctk.CTkFrame(self.tab_matches, fg_color="transparent")
        log_header_frame.grid(row=2, column=0, sticky="ew", pady=(2, 2))

        ctk.CTkLabel(log_header_frame, text="Лог работы:", font=("Arial", 12, "bold")).pack(side="left")

        self.btn_clear_logs = ctk.CTkButton(log_header_frame, text="Очистить", width=80, height=24,
                                            font=("Arial", 11), fg_color="#444444", hover_color="#555555",
                                            command=self.clear_logs)
        self.btn_clear_logs.pack(side="right", padx=(5, 0))

        self.btn_copy_logs = ctk.CTkButton(log_header_frame, text="Скопировать логи", width=140, height=24,
                                           font=("Arial", 11), fg_color="#1F6AA5", hover_color="#144870",
                                           command=self.copy_logs_to_clipboard)
        self.btn_copy_logs.pack(side="right", padx=(5, 0))

        self.log_console = ctk.CTkTextbox(self.tab_matches, font=("Consolas", 12), text_color="#A9B7C6",
                                          fg_color="#1E1E1E")
        self.log_console.grid(row=3, column=0, sticky="nsew", pady=(0, 5))

        # Контекстное меню (ПКМ) для логов
        self.log_menu = tk.Menu(self, tearoff=0, bg="#2B2B2B", fg="white", activebackground="#404040", activeforeground="white")
        self.log_menu.add_command(label="Копировать выделенное", command=self.copy_selected_log)
        self.log_menu.add_command(label="Скопировать все логи", command=self.copy_logs_to_clipboard)
        self.log_menu.add_command(label="Выделить всё", command=self.select_all_logs)
        self.log_menu.add_separator()
        self.log_menu.add_command(label="Очистить логи", command=self.clear_logs)

        def show_log_menu(event):
            try:
                self.log_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.log_menu.grab_release()

        self.log_console._textbox.bind("<Button-3>", show_log_menu)

        # --- ВКЛАДКА 2: НАСТРОЙКИ СИСТЕМЫ ---
        settings_frame = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        settings_frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(settings_frame, text="Паттерн графики:", font=("Arial", 14, "bold")).pack(pady=(5, 2), anchor="w")
        ctk.CTkSegmentedButton(settings_frame, variable=self.pattern_var, values=["Автовыбор", "1", "2"]).pack(fill="x",
                                                                                                               pady=2)

        ctk.CTkLabel(settings_frame, text="Лига:", font=("Arial", 14, "bold")).pack(pady=(10, 2), anchor="w")
        ctk.CTkSegmentedButton(settings_frame, variable=self.league_var,
                               values=["AFL Moscow 8x8", "AFL Balashikha 8x8", "Just League Moscow 11x11"]).pack(
            fill="x", pady=2)

        ctk.CTkLabel(settings_frame, text="Цвет по умолчанию (номер позиции):", font=("Arial", 14, "bold")).pack(
            pady=(10, 2), anchor="w")
        ctk.CTkEntry(settings_frame, textvariable=self.default_color_var, font=("Arial", 13)).pack(fill="x", pady=2)

        ctk.CTkLabel(settings_frame, text="Шаблон описания трансляции в Rutube:", font=("Arial", 14, "bold")).pack(
            pady=(15, 2), anchor="w")
        self.textbox_desc = ctk.CTkTextbox(settings_frame, font=("Arial", 13))
        self.textbox_desc.pack(fill="both", expand=True, pady=5)
        self.textbox_desc.insert("1.0", self.rutube_description_text)

        self.btn_save_settings = ctk.CTkButton(settings_frame, text="СОХРАНИТЬ КОНФИГУРАЦИЮ",
                                               font=("Arial", 14, "bold"),
                                               fg_color="#2E7D32", hover_color="#1B5E20", height=45,
                                               command=self.save_config)
        self.btn_save_settings.pack(pady=15, fill="x")

        # --- ВКЛАДКА 3: ЦВЕТА СТАДИОНОВ (НОВАЯ) ---
        add_frame = ctk.CTkFrame(self.tab_colors, fg_color="transparent")
        add_frame.pack(fill="x", padx=10, pady=(10, 20))

        ctk.CTkLabel(add_frame, text="Добавить / Обновить привязку цвета", font=("Arial", 16, "bold")).pack(anchor="w",
                                                                                                            pady=(0,
                                                                                                                  10))

        input_row = ctk.CTkFrame(add_frame, fg_color="transparent")
        input_row.pack(fill="x")

        self.entry_new_stadium = ctk.CTkEntry(input_row, placeholder_text="Слово в названии (например 'труд')",
                                              font=("Arial", 13), width=250)
        self.entry_new_stadium.pack(side="left", padx=(0, 10))

        self.entry_new_color = ctk.CTkEntry(input_row, placeholder_text="Номер цвета", font=("Arial", 13), width=100)
        self.entry_new_color.pack(side="left", padx=(0, 10))

        btn_add_stadium = ctk.CTkButton(input_row, text="Добавить", fg_color="#1976D2", hover_color="#1565C0",
                                        command=self.add_stadium_color)
        btn_add_stadium.pack(side="left")

        ctk.CTkLabel(self.tab_colors, text="Текущая база стадионов:", font=("Arial", 14, "bold")).pack(anchor="w",
                                                                                                       padx=10,
                                                                                                       pady=(0, 5))

        self.colors_scroll = ctk.CTkScrollableFrame(self.tab_colors)
        self.colors_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.refresh_colors_list()

        # --- ВКЛАДКА 4: ПАРАМЕТРЫ ЗАПУСКА ---
        test_frame = ctk.CTkFrame(self.tab_test, fg_color="transparent")
        test_frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(test_frame, text="Параметры публикации и выборки матчей", font=("Arial", 18, "bold")).pack(
            pady=(10, 30), anchor="w")

        self.switch_test = ctk.CTkSwitch(test_frame,
                                         text="Автономный запуск (генерация ключей без привязки к Footballista)",
                                         variable=self.test_mode_var, onvalue=True, offvalue=False,
                                         font=("Arial", 14))
        self.switch_test.pack(pady=15, anchor="w")

        self.switch_debug = ctk.CTkSwitch(test_frame,
                                          text="Расширенная выборка (загрузка всех туров без фильтра по дате)",
                                          variable=self.debug_30_var, onvalue=True, offvalue=False,
                                          font=("Arial", 14))
        self.switch_debug.pack(pady=25, anchor="w")

        ctk.CTkLabel(test_frame,
                     text="* Расширенная выборка позволяет загрузить матчи из всех доступных туров лиги,\nдаже если на ближайшие выходные расписание еще не опубликовано.",
                     text_color="gray", font=("Arial", 12), justify="left").pack(pady=10, anchor="w")

        # --- ВКЛАДКА 5: АВТОПИЛОТ И TELEGRAM ---
        self.build_autopilot_tab()

        # Настройка логгера
        ui_handler = TextHandler(self.log_console)
        ui_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(ui_handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

    def build_autopilot_tab(self):
        scroll = ctk.CTkScrollableFrame(self.tab_autopilot, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        # 1. ПАПКА СО СТРИМ-КЛЮЧАМИ
        sec1 = ctk.CTkFrame(scroll, fg_color="#2B2B2B", corner_radius=8)
        sec1.pack(fill="x", pady=(0, 15), padx=5)
        ctk.CTkLabel(sec1, text="Папка для сохранения стрим-ключей", font=("Arial", 15, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        ctk.CTkLabel(sec1, text="Сюда сохраняются файлы stream_keys с датой и туром", font=("Arial", 12), text_color="gray").pack(anchor="w", padx=15, pady=(0, 8))

        row_dir = ctk.CTkFrame(sec1, fg_color="transparent")
        row_dir.pack(fill="x", padx=15, pady=(0, 15))
        self.entry_keys_dir = ctk.CTkEntry(row_dir, textvariable=self.stream_keys_dir_var, font=("Arial", 13))
        self.entry_keys_dir.pack(side="left", fill="x", expand=True, padx=(0, 10))
        btn_browse = ctk.CTkButton(row_dir, text="Выбрать...", width=90, command=self.browse_keys_dir)
        btn_browse.pack(side="left", padx=(0, 5))
        btn_open = ctk.CTkButton(row_dir, text="Открыть", width=90, fg_color="#37474F", hover_color="#455A64", command=self.open_keys_dir)
        btn_open.pack(side="left")

        # 2. НАСТРОЙКИ TELEGRAM
        sec2 = ctk.CTkFrame(scroll, fg_color="#2B2B2B", corner_radius=8)
        sec2.pack(fill="x", pady=(0, 15), padx=5)
        ctk.CTkLabel(sec2, text="Оповещения в Telegram", font=("Arial", 15, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        ctk.CTkLabel(sec2, text="Бот присылает отчет со ссылками на Rutube и готовый файл ключей прямо в чат", font=("Arial", 12), text_color="gray").pack(anchor="w", padx=15, pady=(0, 8))

        ctk.CTkLabel(sec2, text="Токен бота (@BotFather):", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(4, 2))
        self.entry_tg_token = ctk.CTkEntry(sec2, textvariable=self.tg_bot_token_var, font=("Arial", 13))
        self.entry_tg_token.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(sec2, text="Chat ID (ваш личный ID или ID группы):", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(4, 2))
        row_tg_chat = ctk.CTkFrame(sec2, fg_color="transparent")
        row_tg_chat.pack(fill="x", padx=15, pady=(0, 10))
        self.entry_tg_chat = ctk.CTkEntry(row_tg_chat, textvariable=self.tg_chat_id_var, font=("Arial", 13), placeholder_text="Например: 123456789 или -100123456789")
        self.entry_tg_chat.pack(side="left", fill="x", expand=True, padx=(0, 10))
        btn_detect_id = ctk.CTkButton(row_tg_chat, text="Найти Chat ID", width=120, command=self.auto_detect_chat_id)
        btn_detect_id.pack(side="left", padx=(0, 5))
        btn_test_tg = ctk.CTkButton(row_tg_chat, text="Тест", width=80, fg_color="#1976D2", hover_color="#1565C0", command=self.test_telegram)
        btn_test_tg.pack(side="left")

        cb_send_file = ctk.CTkCheckBox(sec2, text="Прикреплять файл stream_keys.txt к отчету в Telegram", variable=self.tg_send_file_var, font=("Arial", 13))
        cb_send_file.pack(anchor="w", padx=15, pady=(0, 12))

        # Блок управления операторами (Footballista + Telegram)
        row_ops = ctk.CTkFrame(sec2, fg_color="#1E1E1E", corner_radius=6)
        row_ops.pack(fill="x", padx=15, pady=(0, 15))
        self.lbl_operators_count = ctk.CTkLabel(row_ops, text="Операторы: Загрузка...", font=("Arial", 13))
        self.lbl_operators_count.pack(side="left", padx=15, pady=8)
        btn_open_wizard = ctk.CTkButton(row_ops, text="Управление операторами...", width=200, fg_color="#1976D2", hover_color="#1565C0", command=self.open_operator_wizard)
        btn_open_wizard.pack(side="right", padx=15, pady=6)
        self.update_operators_label()

        # 3. АВТОПИЛОТ В ПРИЛОЖЕНИИ
        sec3 = ctk.CTkFrame(scroll, fg_color="#2B2B2B", corner_radius=8)
        sec3.pack(fill="x", pady=(0, 15), padx=5)
        ctk.CTkLabel(sec3, text="Автопилот (мониторинг расписания в фоне)", font=("Arial", 15, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        ctk.CTkLabel(sec3, text="Проверяет появление новых матчей на Footballista и сам делает трансляции", font=("Arial", 12), text_color="gray").pack(anchor="w", padx=15, pady=(0, 8))

        row_ap_switch = ctk.CTkFrame(sec3, fg_color="transparent")
        row_ap_switch.pack(fill="x", padx=15, pady=(0, 10))
        self.switch_autopilot = ctk.CTkSwitch(row_ap_switch, text="Включить фоновый автопилот", variable=self.autopilot_enabled_var,
                                              font=("Arial", 14, "bold"), command=self.toggle_autopilot)
        self.switch_autopilot.pack(side="left")

        self.cb_ap_friday = ctk.CTkCheckBox(sec3, text="Проверять только по пятницам и субботам", variable=self.autopilot_friday_only_var, font=("Arial", 13))
        self.cb_ap_friday.pack(anchor="w", padx=15, pady=(0, 10))

        row_interval = ctk.CTkFrame(sec3, fg_color="transparent")
        row_interval.pack(fill="x", padx=15, pady=(0, 12))
        ctk.CTkLabel(row_interval, text="Интервал проверки (минут):", font=("Arial", 13)).pack(side="left", padx=(0, 10))
        ctk.CTkSegmentedButton(row_interval, variable=self.autopilot_interval_var, values=["15", "30", "60", "120"]).pack(side="left")

        row_ap_status = ctk.CTkFrame(sec3, fg_color="#222222", corner_radius=6)
        row_ap_status.pack(fill="x", padx=15, pady=(0, 10))
        self.lbl_ap_status = ctk.CTkLabel(row_ap_status, text="Статус: Выключен", font=("Arial", 13, "bold"), text_color="gray")
        self.lbl_ap_status.pack(side="left", padx=15, pady=10)

        btn_run_now = ctk.CTkButton(row_ap_status, text="Проверить сейчас", fg_color="#F57C00", hover_color="#E65100", command=self.trigger_autopilot_now)
        btn_run_now.pack(side="right", padx=15, pady=8)

        # Статус текущих выходных (Пт, Сб, Вс)
        row_weekend_status = ctk.CTkFrame(sec3, fg_color="#1E1E1E", corner_radius=6)
        row_weekend_status.pack(fill="x", padx=15, pady=(0, 15))
        self.lbl_weekend_status = ctk.CTkLabel(row_weekend_status, text="Выходные: Загрузка...", font=("Arial", 12))
        self.lbl_weekend_status.pack(side="left", padx=15, pady=8)
        btn_reset_wk = ctk.CTkButton(row_weekend_status, text="Сбросить статус выходных", width=190, height=28,
                                     fg_color="#37474F", hover_color="#455A64", command=self.reset_weekend_clicked)
        btn_reset_wk.pack(side="right", padx=15, pady=6)
        self.refresh_weekend_status()

        # 4. ПЛАНИРОВЩИК WINDOWS (БЕЗ ОТКРЫТИЯ ПРОГРАММЫ)
        sec4 = ctk.CTkFrame(scroll, fg_color="#2B2B2B", corner_radius=8)
        sec4.pack(fill="x", pady=(0, 15), padx=5)
        ctk.CTkLabel(sec4, text="Полная автономность: Планировщик Windows (Task Scheduler)", font=("Arial", 15, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        ctk.CTkLabel(sec4, text="Windows сама по пятницам и субботам (с 12:00 каждые 2 ч) проверит расписание, создаст стримы и пришлет в TG.\nПрограмму можно вообще не открывать!", font=("Arial", 12), text_color="gray", justify="left").pack(anchor="w", padx=15, pady=(0, 10))

        row_win_task = ctk.CTkFrame(sec4, fg_color="transparent")
        row_win_task.pack(fill="x", padx=15, pady=(0, 15))
        btn_install_task = ctk.CTkButton(row_win_task, text="Установить автозапуск (Пт и Сб с 12:00)", fg_color="#2E7D32", hover_color="#1B5E20", height=38, command=self.install_windows_task)
        btn_install_task.pack(side="left", padx=(0, 10))
        btn_remove_task = ctk.CTkButton(row_win_task, text="Удалить из Windows", fg_color="#D32F2F", hover_color="#C62828", height=38, command=self.remove_windows_task)
        btn_remove_task.pack(side="left")

        # 5. ОТЧЕТ ЗА ПРОИЗВОЛЬНЫЙ ПЕРИОД (FAILSAFE)
        sec5 = ctk.CTkFrame(scroll, fg_color="#2B2B2B", corner_radius=8)
        sec5.pack(fill="x", pady=(0, 15), padx=5)
        ctk.CTkLabel(sec5, text="Экстренный отчет за произвольный период (Failsafe)", font=("Arial", 15, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        ctk.CTkLabel(sec5, text="Если нужно рассчитать оплату за случайные даты, турнир или смену вне пятниц-воскресений", font=("Arial", 12), text_color="gray").pack(anchor="w", padx=15, pady=(0, 10))
        btn_failsafe = ctk.CTkButton(sec5, text="Сформировать отчет за произвольный период...", fg_color="#1E3A5F", hover_color="#152A45", height=38, command=self.open_custom_report_dialog)
        btn_failsafe.pack(fill="x", padx=15, pady=(0, 15))

        # Кнопка сохранения
        ctk.CTkButton(scroll, text="СОХРАНИТЬ ВСЕ НАСТРОЙКИ", font=("Arial", 14, "bold"), fg_color="#2E7D32", hover_color="#1B5E20", height=45, command=self.save_config).pack(fill="x", pady=(10, 20), padx=5)

    def browse_keys_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.stream_keys_dir_var.get() or os.getcwd(), title="Выберите папку для сохранения ключей трансляций")
        if chosen:
            self.stream_keys_dir_var.set(chosen)
            self.save_config()
            logging.info(f"Выбрана папка для ключей: {chosen}")

    def open_keys_dir(self):
        path = os.path.abspath(self.stream_keys_dir_var.get() or "stream_keys")
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception as e:
            logging.error(f"Не удалось открыть папку: {e}")

    def auto_detect_chat_id(self):
        token = self.tg_bot_token_var.get().strip()
        if not token:
            messagebox.showwarning("Telegram", "Сначала укажите токен бота!")
            return
        notifier = TelegramNotifier(token)
        cid = notifier.get_last_chat_id()
        if cid:
            self.tg_chat_id_var.set(cid)
            self.save_config()
            messagebox.showinfo("Telegram", f"Chat ID найден: {cid}\nНастройки сохранены!")
            logging.info(f"Chat ID автоматически обнаружен и сохранен: {cid}")
        else:
            messagebox.showinfo("Telegram", "Бот пока не видит входящих сообщений.\n\nОткройте бота в Telegram, отправьте ему /start или любое слово, и нажмите эту кнопку снова.")

    def test_telegram(self):
        token = self.tg_bot_token_var.get().strip()
        cid = self.tg_chat_id_var.get().strip()
        if not token or not cid:
            messagebox.showwarning("Telegram", "Укажите токен бота и Chat ID!")
            return
        notifier = TelegramNotifier(token, cid)
        ok = notifier.send_message("<b>Тест из GOAL 3.2</b>\n\nИнтеграция с Telegram успешно работает.")
        if ok:
            messagebox.showinfo("Telegram", "Тестовое сообщение успешно доставлено в Telegram.")
        else:
            messagebox.showerror("Telegram", "Не удалось отправить сообщение. Проверьте правильность токена и Chat ID.")

    def update_operators_label(self):
        try:
            ops = []
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    ops = cfg.get("operators", [])
            self.operators = ops
            if hasattr(self, "lbl_operators_count"):
                self.lbl_operators_count.configure(text=f"Операторы: {len(ops)} подключено")
        except Exception:
            pass

    def open_operator_wizard(self):
        try:
            script_path = os.path.abspath(os.path.join(os.getcwd(), "scripts", "add_operator.py"))
            if sys.platform == "win32":
                cmd = f'start cmd /k "{sys.executable}" "{script_path}"'
                subprocess.Popen(cmd, shell=True)
            else:
                subprocess.Popen([sys.executable, script_path])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить мастер операторов: {e}")

    def install_windows_task(self):
        self.save_config()
        ok = install_task()
        if ok:
            messagebox.showinfo("Планировщик Windows", "Задача успешно зарегистрирована в Планировщике Windows!\n\nКаждую пятницу система будет автоматически проверять появление нового расписания и отправлять ключи в Telegram.")
        else:
            messagebox.showerror("Планировщик Windows", "Не удалось зарегистрировать задачу. Попробуйте запустить приложение от имени Администратора.")

    def remove_windows_task(self):
        ok = remove_task()
        if ok:
            messagebox.showinfo("Планировщик Windows", "Задача автопилота удалена из Планировщика Windows.")
        else:
            messagebox.showwarning("Планировщик Windows", "Задача не найдена или уже была удалена.")

    def open_custom_report_dialog(self):
        """Диалоговое окно формирования отчета за произвольный период (Failsafe)."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Отчет за произвольный период (Failsafe)")
        dlg.geometry("480x420")
        dlg.resizable(False, False)
        dlg.grab_set()

        today = datetime.datetime.now().date()
        date_from_default = (today - datetime.timedelta(days=14)).strftime("%d.%m.%Y")
        date_to_default = today.strftime("%d.%m.%Y")

        ctk.CTkLabel(dlg, text="Отчет за произвольный период", font=("Arial", 16, "bold")).pack(pady=(18, 4))
        ctk.CTkLabel(
            dlg,
            text="Сформирует Telegram-отчет и ссылку на калькулятор WebApp\nдля любого диапазона дат (включая нетипичные смены и турниры).",
            font=("Arial", 11),
            text_color="gray",
            justify="center"
        ).pack(pady=(0, 15))

        f_dates = ctk.CTkFrame(dlg, fg_color="transparent")
        f_dates.pack(fill="x", padx=25, pady=5)

        ctk.CTkLabel(f_dates, text="С даты (ДД.ММ.ГГГГ):", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", pady=4)
        entry_from = ctk.CTkEntry(f_dates, width=170)
        entry_from.insert(0, date_from_default)
        entry_from.grid(row=0, column=1, padx=(10, 0), pady=4)

        ctk.CTkLabel(f_dates, text="По дату (ДД.ММ.ГГГГ):", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", pady=4)
        entry_to = ctk.CTkEntry(f_dates, width=170)
        entry_to.insert(0, date_to_default)
        entry_to.grid(row=1, column=1, padx=(10, 0), pady=4)

        use_selected_var = ctk.BooleanVar(value=False)
        cb_use_selected = ctk.CTkCheckBox(
            dlg,
            text="Использовать только выбранные галочками матчи из таблицы",
            variable=use_selected_var,
            font=("Arial", 11)
        )
        cb_use_selected.pack(pady=(12, 10), padx=25, anchor="w")

        lbl_status_msg = ctk.CTkLabel(dlg, text="", font=("Arial", 11))
        lbl_status_msg.pack(pady=(0, 5))

        def send_report():
            df_str = entry_from.get().strip()
            dt_str = entry_to.get().strip()
            d_from = parse_match_date(df_str)
            d_to = parse_match_date(dt_str)

            if not d_from or not d_to:
                messagebox.showwarning("Даты", "Укажите даты в корректном формате (например, 01.09.2026)!", parent=dlg)
                return

            if d_from > d_to:
                d_from, d_to = d_to, d_from

            custom_matches = None
            if use_selected_var.get() and hasattr(self, "current_matches") and self.current_matches:
                custom_matches = [
                    self.current_matches[i]
                    for i, var in enumerate(self.checkbox_vars)
                    if var.get() and i < len(self.current_matches)
                ]

            lbl_status_msg.configure(text="Отправка отчета в Telegram...", text_color="orange")
            dlg.update_idletasks()

            ok, msg = send_custom_period_report(d_from, d_to, custom_matches)
            if ok:
                logging.info(f"{msg}")
                messagebox.showinfo("Telegram", f"{msg}", parent=dlg)
                dlg.destroy()
            else:
                lbl_status_msg.configure(text=f"{msg}", text_color="#E53935")
                messagebox.showerror("Ошибка", msg, parent=dlg)

        btn_send = ctk.CTkButton(
            dlg,
            text="Отправить отчет и калькулятор в Telegram",
            font=("Arial", 13, "bold"),
            fg_color="#1E88E5",
            hover_color="#1565C0",
            height=42,
            command=send_report
        )
        btn_send.pack(fill="x", padx=25, pady=(5, 10))

    def toggle_autopilot(self):
        self.save_config()
        if self.autopilot_enabled_var.get():
            interval = int(self.autopilot_interval_var.get() or 30)
            self.autopilot_next_check = datetime.datetime.now() + datetime.timedelta(minutes=interval)
            self.lbl_ap_status.configure(
                text=f"Активен (след. проверка: {self.autopilot_next_check.strftime('%H:%M:%S')})",
                text_color="#00FF00"
            )
            logging.info(f"Фоновый автопилот включен. Интервал: {interval} мин.")
        else:
            self.autopilot_next_check = None
            self.lbl_ap_status.configure(text="Статус: Выключен", text_color="gray")
            logging.info("Фоновый автопилот выключен.")

    def check_autopilot_tick(self):
        if self.autopilot_enabled_var.get() and not self.autopilot_running:
            now = datetime.datetime.now()
            if self.autopilot_next_check is None:
                interval = int(self.autopilot_interval_var.get() or 30)
                self.autopilot_next_check = now + datetime.timedelta(minutes=interval)

            if now >= self.autopilot_next_check:
                today_name = now.strftime("%A")
                if not self.autopilot_friday_only_var.get() or today_name in ["Friday", "Saturday"]:
                    self.trigger_autopilot_now()
                else:
                    interval = int(self.autopilot_interval_var.get() or 30)
                    self.autopilot_next_check = now + datetime.timedelta(minutes=interval)
                    self.lbl_ap_status.configure(
                        text=f"Пропуск ({today_name}, не пт/сб). След: {self.autopilot_next_check.strftime('%H:%M')}",
                        text_color="orange"
                    )

            if self.autopilot_next_check and not self.autopilot_running:
                diff = int((self.autopilot_next_check - now).total_seconds())
                mins, secs = divmod(max(0, diff), 60)
                self.lbl_ap_status.configure(
                    text=f"Активен (до проверки {mins:02d}:{secs:02d})",
                    text_color="#00FF00"
                )

        self.after(1000, self.check_autopilot_tick)

    def trigger_autopilot_now(self):
        if self.autopilot_running:
            return
        self.autopilot_running = True
        self.lbl_ap_status.configure(text="Проверка расписания...", text_color="#FFD600")
        logging.info("[Автопилот] Запуск проверки расписания...")

        threading.Thread(target=self._run_async_autopilot, daemon=True).start()

    def _run_async_autopilot(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(run_autopilot_check(test_mode=self.test_mode_var.get()))
            status = res.get("status")
            if status == "success":
                txt = f"Создано {res.get('success_count')} трансляций."
            elif status == "already_up_to_date":
                txt = "Все матчи актуальны"
            elif status == "no_matches":
                txt = "Матчей в расписании пока нет"
            else:
                txt = f"{res.get('message', status)}"
            logging.info(f"Результат автопилота: {txt}")
        except Exception as e:
            logging.error(f"Сбой в работе автопилота: {e}")
            txt = f"Ошибка: {e}"
        finally:
            loop.close()
            interval = int(self.autopilot_interval_var.get() or 30)
            self.autopilot_next_check = datetime.datetime.now() + datetime.timedelta(minutes=interval)
            self.autopilot_running = False
            self.after(0, lambda: self.lbl_ap_status.configure(text=txt, text_color="#00E676" if "Создано" in txt else "orange"))
            self.after(0, self.refresh_weekend_status)

    def refresh_weekend_status(self):
        try:
            _, _, w_key = get_current_weekend_window()
            db = load_processed_matches()
            is_done = is_weekend_completed(w_key, db)
            if is_done:
                w_info = db.get("completed_weekends", {}).get(w_key, {})
                days_txt = ""
                if isinstance(w_info, dict):
                    def_info = w_info.get("default", w_info)
                    days_txt = f" ({', '.join(def_info.get('days', []))})"
                self.lbl_weekend_status.configure(
                    text=f"Выходные {w_key}: Завершены{days_txt} (проверки отключены)",
                    text_color="#4CAF50"
                )
            else:
                self.lbl_weekend_status.configure(
                    text=f"Выходные {w_key}: В процессе (проверки активны)",
                    text_color="#FFA726"
                )
        except Exception as e:
            self.lbl_weekend_status.configure(text=f"Выходные: {e}", text_color="gray")

    def reset_weekend_clicked(self):
        _, _, w_key = get_current_weekend_window()
        reset_weekend_status(w_key)
        self.refresh_weekend_status()
        messagebox.showinfo("Автопилот", f"Статус выходных '{w_key}' сброшен.\nАвтопилот снова будет проверять появление матчей.")


    def check_browser_status(self):
        current_state = is_chrome_running()
        if current_state != self.last_browser_state:
            self.last_browser_state = current_state
            if current_state:
                self.lbl_status.configure(text="Браузер: Подключен", text_color="#00FF00")
                self.btn_fetch.configure(state="normal")
            else:
                self.lbl_status.configure(text="Браузер: Не найден", text_color="#FF5252")
                self.btn_fetch.configure(state="disabled")
        self.after(2000, self.check_browser_status)

    def toggle_all_matches(self):
        state = self.select_all_var.get()
        for var, _, _ in self.checkbox_vars:
            var.set(state)

    def start_fetch(self):
        self.btn_fetch.configure(state="disabled", text="Сбор...")
        threading.Thread(target=self._run_async_fetch, daemon=True).start()

    def _run_async_fetch(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            debug_flag = self.debug_30_var.get()
            matches = loop.run_until_complete(fetch_matches_for_ui(debug_flag))
            self.after(0, lambda: self._render_match_cards(matches))
        except Exception as e:
            logging.error(f"Ошибка сбора: {e}")
        finally:
            loop.close()
            self.after(0, lambda: self.btn_fetch.configure(state="normal", text="2. Обновить расписание"))

    def _render_match_cards(self, matches):
        for widget in self.scroll_matches.winfo_children():
            widget.destroy()
        self.checkbox_vars.clear()

        if not matches: return

        # 1. ОПТИМИЗАЦИЯ ШРИФТОВ: Создаем объекты один раз до начала цикла
        font_title = ctk.CTkFont(family="Arial", size=14, weight="bold")
        font_sub = ctk.CTkFont(family="Arial", size=12)

        for match in matches:
            # 2. ПЛОСКАЯ КАРТОЧКА: Уже сделано отлично (без скруглений и прозрачности)
            card = ctk.CTkFrame(self.scroll_matches, corner_radius=0, border_width=0, fg_color="#333333")
            card.pack(pady=3, padx=10, fill="x")
            card.grid_columnconfigure(1, weight=1)

            var = ctk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(card, text="", variable=var, width=20)
            cb.grid(row=0, column=0, rowspan=2, padx=10, pady=10, sticky="w")

            # Применяем заранее созданный шрифт
            lbl_title = ctk.CTkLabel(card, text=match.stream_title, font=font_title, anchor="w")
            lbl_title.grid(row=0, column=1, padx=(0, 10), pady=(5, 0), sticky="ew")

            lbl_sub = ctk.CTkLabel(card, text=f"{match.match_date} | Тур {match.tour_number} | {match.stadium}",
                                   text_color="gray", font=font_sub, anchor="w")
            lbl_sub.grid(row=1, column=1, padx=(0, 10), pady=(0, 5), sticky="ew")

            self.checkbox_vars.append((var, match, card))

        self.btn_publish.configure(state="normal")
        logging.info("Матчи загружены. Проверьте список перед публикацией.")

    def start_publish(self):
        selected = [m for var, m, _ in self.checkbox_vars if var.get()]
        if not selected:
            logging.warning("Нет выбранных матчей.")
            return

        self.btn_publish.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self.save_config()

        mode = self.pattern_var.get()
        test = self.test_mode_var.get()
        league = self.league_var.get()

        try:
            default_color = int(self.default_color_var.get())
        except ValueError:
            default_color = 3

        desc_text = self.rutube_description_text
        # Берем актуальный словарь цветов
        colors_dict = self.stadium_colors

        threading.Thread(target=self._run_async_publish,
                         args=(selected, mode, test, league, default_color, desc_text, colors_dict),
                         daemon=True).start()

    def _run_async_publish(self, selected_matches, pattern_mode, test_mode, league, default_color, desc_text,
                           colors_dict):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        keys_dir = self.stream_keys_dir_var.get().strip() or "stream_keys"
        target_ch_id = self.rutube_channel_id_var.get().strip() or "77095292"
        self.pipeline_task = loop.create_task(
            process_selected_matches(selected_matches, pattern_mode, test_mode, league, default_color, desc_text,
                                     colors_dict, stream_keys_dir=keys_dir, rutube_channel_id=target_ch_id))

        try:
            success_count, keys_file, results = loop.run_until_complete(self.pipeline_task)

            # Сохранение в базу обработанных матчей
            db = load_processed_matches()
            record_processed_matches(results, db)

            # Персональная отправка отчетов и файлов ключей каждому оператору в Telegram
            cfg = None
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    pass

            token = self.tg_bot_token_var.get().strip()
            cid = self.tg_chat_id_var.get().strip()
            notifier = TelegramNotifier(token, cid) if token else None

            send_operator_dispatch(
                results=results,
                master_keys_file=keys_file,
                config=cfg,
                notifier=notifier
            )

        except asyncio.CancelledError:
            logging.warning("Остановлено пользователем.")
        except Exception as e:
            logging.error(f"Сбой публикации: {e}")
        finally:
            loop.close()
            self.after(0, lambda: self.btn_publish.configure(state="normal"))
            self.after(0, lambda: self.btn_stop.configure(state="disabled"))

    def stop_automation(self):
        if self.pipeline_task and not self.pipeline_task.done():
            logging.info("Посылаем сигнал остановки...")
            self.pipeline_task.get_loop().call_soon_threadsafe(self.pipeline_task.cancel)

    def copy_logs_to_clipboard(self):
        try:
            text = self.log_console.get("1.0", tk.END).strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                logging.info("Все логи скопированы в буфер обмена.")
        except Exception as e:
            logging.error(f"Не удалось скопировать логи: {e}")

    def copy_selected_log(self):
        try:
            sel = self.log_console._textbox.selection_get()
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
                logging.info("Выделенный фрагмент логов скопирован.")
                return
        except Exception:
            pass
        self.copy_logs_to_clipboard()

    def select_all_logs(self):
        try:
            self.log_console._textbox.tag_add("sel", "1.0", "end")
        except Exception:
            pass

    def clear_logs(self):
        self.log_console.configure(state="normal")
        self.log_console.delete("1.0", tk.END)
        self.log_console.configure(state="disabled")
        logging.info("Логи очищены.")


if __name__ == "__main__":
    app = AFLPublisherApp()
    app.mainloop()
