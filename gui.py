import customtkinter as ctk
import tkinter as tk
import threading
import asyncio
import logging
import subprocess
import os
import urllib.request
import json

from main import fetch_matches_for_ui, process_selected_matches

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

pipeline_task = None
checkbox_vars = []
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
    except:
        return False


def launch_chrome():
    try:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_path):
            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        profile_path = os.path.join(os.getcwd(), "chrome_debug_profile")
        os.makedirs(profile_path, exist_ok=True)
        subprocess.Popen([chrome_path, "--remote-debugging-port=9222", f"--user-data-dir={profile_path}"])
        logging.info("Chrome запущен. Авторизуйтесь на нужных сайтах.")
    except Exception as e:
        logging.error(f"Не удалось запустить Chrome: {e}")


class AFLPublisherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("GOAL 2.0")
        self.geometry("1150x820")
        try:
            self.iconbitmap("icon.ico")
        except:
            pass

        self.test_mode_var = ctk.BooleanVar(value=False)
        self.debug_30_var = ctk.BooleanVar(value=False)
        self.pattern_var = ctk.StringVar(value="Автовыбор")
        self.select_all_var = ctk.BooleanVar(value=True)
        self.league_var = ctk.StringVar(value="AFL Moscow 8x8")
        self.default_color_var = ctk.StringVar(value="3")
        self.rutube_description_text = ""
        self.stadium_colors = {}
        self.last_browser_state = None

        self.load_config()
        self.build_ui()
        self.check_browser_status()
# pasha
    def load_config(self):
        default_desc = "Заявляйся в AFL!\n\n+7 (915) 296-80-45\nhttps://vk.com/s.lebedev24\n\nТелеграм AFL — https://t.me/aflrussiа\n\nAFL VK – https://vk.com/aflmoscow"

        default_stadiums = {
            "труд": "3", "ясенево": "24", "терехово": "19",
            "конструктор": "13", "дело спорта": "13", "тушино": "4",
            "октябрь": "4", "братиславский": "5", "торпедо": "22",
            "олимпийская": "9", "красносельская": "11", "балашиха": "15"
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
        except Exception as e:
            logging.error(f"Не удалось загрузить {CONFIG_FILE}: {e}")

    def save_config(self):
        if hasattr(self, "textbox_desc") and self.textbox_desc.winfo_exists():
            self.rutube_description_text = self.textbox_desc.get("1.0", tk.END).strip()

        config_data = {
            "rutube_description": self.rutube_description_text,
            "stadium_colors": self.stadium_colors
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

        # === ПРАВАЯ ЧАСТЬ (ВКЛАДКИ) ===
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)

        # Создаем ЧЕТЫРЕ вкладки
        self.tab_matches = self.tabview.add("Матчи")
        self.tab_settings = self.tabview.add("Настройки")
        self.tab_colors = self.tabview.add("Цвета стадионов")
        self.tab_test = self.tabview.add("Дебаг")

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

        ctk.CTkLabel(self.tab_matches, text="Лог работы:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w")
        self.log_console = ctk.CTkTextbox(self.tab_matches, font=("Consolas", 12), text_color="#A9B7C6",
                                          fg_color="#1E1E1E")
        self.log_console.grid(row=3, column=0, sticky="nsew", pady=(0, 5))

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

        # --- ВКЛАДКА 4: ТЕСТОВЫЙ РЕЖИМ ---
        test_frame = ctk.CTkFrame(self.tab_test, fg_color="transparent")
        test_frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(test_frame, text="Инструменты отладки и тестирования", font=("Arial", 18, "bold")).pack(
            pady=(10, 30), anchor="w")

        self.switch_test = ctk.CTkSwitch(test_frame,
                                         text="Тестовый режим (без публикации видео на сайте Footballista)",
                                         variable=self.test_mode_var, onvalue=True, offvalue=False,
                                         font=("Arial", 14))
        self.switch_test.pack(pady=15, anchor="w")

        self.switch_debug = ctk.CTkSwitch(test_frame,
                                          text="Дебаг-режим: собрать ровно 30 последних матчей (игнорировать проверку даты)",
                                          variable=self.debug_30_var, onvalue=True, offvalue=False,
                                          font=("Arial", 14))
        self.switch_debug.pack(pady=25, anchor="w")

        ctk.CTkLabel(test_frame,
                     text="* Включение дебаг-режима полезно для проверки парсера на старых матчах, \nкогда на текущей неделе нет актуального расписания.",
                     text_color="gray", font=("Arial", 12), justify="left").pack(pady=10, anchor="w")

        # Настройка логгера
        ui_handler = TextHandler(self.log_console)
        ui_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(ui_handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

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
        for var, _, _ in checkbox_vars:
            var.set(state)

    def start_fetch(self):
        self.btn_fetch.configure(state="disabled", text="Сбор...")
        threading.Thread(target=self._run_async_fetch, daemon=True).start()

    def _run_async_fetch(self):
        global checkbox_vars
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
        global checkbox_vars
        for widget in self.scroll_matches.winfo_children():
            widget.destroy()
        checkbox_vars.clear()

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

            checkbox_vars.append((var, match, card))

        self.btn_publish.configure(state="normal")
        logging.info("Матчи загружены. Проверьте список перед публикацией.")

    def start_publish(self):
        selected = [m for var, m, _ in checkbox_vars if var.get()]
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
        global pipeline_task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        pipeline_task = loop.create_task(
            process_selected_matches(selected_matches, pattern_mode, test_mode, league, default_color, desc_text,
                                     colors_dict))

        try:
            loop.run_until_complete(pipeline_task)
        except asyncio.CancelledError:
            logging.warning("Остановлено пользователем.")
        except Exception as e:
            logging.error(f"Сбой публикации: {e}")
        finally:
            loop.close()
            self.after(0, lambda: self.btn_publish.configure(state="normal"))
            self.after(0, lambda: self.btn_stop.configure(state="disabled"))

    def stop_automation(self):
        global pipeline_task
        if pipeline_task and not pipeline_task.done():
            logging.info("Посылаем сигнал остановки...")
            pipeline_task.get_loop().call_soon_threadsafe(pipeline_task.cancel)


if __name__ == "__main__":
    app = AFLPublisherApp()
    app.mainloop()
