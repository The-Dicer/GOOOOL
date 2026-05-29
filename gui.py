import customtkinter as ctk
import tkinter as tk
import threading
import asyncio
import logging
import subprocess
import os
import urllib.request

from main import fetch_matches_for_ui, process_selected_matches

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

pipeline_task = None
checkbox_vars = []


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
        self.title("AFL Publisher - PRO")
        self.geometry("1100x800")
        try:
            self.iconbitmap("icon.ico")
        except:
            pass

        self.test_mode_var = ctk.BooleanVar(value=True)
        self.pattern_var = ctk.StringVar(value="Автовыбор")
        self.select_all_var = ctk.BooleanVar(value=True)

        self.league_var = ctk.StringVar(value="AFL Moscow 8x8")
        self.default_color_var = ctk.StringVar(value="3")  # По умолчанию ставим 3

        self.last_browser_state = None

        self.build_ui()
        self.check_browser_status()

    def build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Сайдбар
        self.sidebar = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(self.sidebar, text="Настройки", font=("Arial", 20, "bold")).pack(pady=(20, 10))

        self.btn_chrome = ctk.CTkButton(self.sidebar, text="1. Запустить Chrome", fg_color="#2E7D32",
                                        hover_color="#1B5E20", command=launch_chrome)
        self.btn_chrome.pack(pady=10, padx=20, fill="x")

        self.lbl_status = ctk.CTkLabel(self.sidebar, text="Браузер: Ожидание...", text_color="orange")
        self.lbl_status.pack(pady=(0, 20))

        self.btn_fetch = ctk.CTkButton(self.sidebar, text="2. Собрать расписание", font=("Arial", 14, "bold"),
                                       fg_color="#F57C00", hover_color="#E65100", height=40, state="disabled",
                                       command=self.start_fetch)
        self.btn_fetch.pack(pady=(0, 20), padx=20, fill="x")

        ctk.CTkLabel(self.sidebar, text="Паттерн графики:").pack(anchor="w", padx=20)
        ctk.CTkSegmentedButton(self.sidebar, variable=self.pattern_var, values=["Автовыбор", "1", "2"]).pack(pady=5,
                                                                                                             padx=20,
                                                                                                             fill="x")

        ctk.CTkLabel(self.sidebar, text="Лига:").pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkSegmentedButton(self.sidebar, variable=self.league_var,
                               values=["AFL Moscow 8x8", "AFL Balashikha 8x8", "Just League Moscow 11x11"]).pack(pady=5, padx=20, fill="x")

        # === НОВЫЙ БЛОК ВЫБОРА ЦВЕТА ===
        ctk.CTkLabel(self.sidebar, text="Цвет по умолчанию (номер):").pack(anchor="w", padx=20, pady=(10, 0))
        self.entry_color = ctk.CTkEntry(self.sidebar, textvariable=self.default_color_var)
        self.entry_color.pack(pady=5, padx=20, fill="x")
        # ===============================

        self.switch_test = ctk.CTkSwitch(self.sidebar, text="Тестовый режим\n(без footballista)",
                                         variable=self.test_mode_var, onvalue=True, offvalue=False)
        self.switch_test.pack(pady=20, padx=20, anchor="w")

        bottom_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom_frame.pack(side="bottom", fill="x", pady=20)

        self.btn_publish = ctk.CTkButton(bottom_frame, text="ОПУБЛИКОВАТЬ", font=("Arial", 16, "bold"), height=50,
                                         state="disabled", command=self.start_publish)
        self.btn_publish.pack(pady=10, padx=20, fill="x")

        self.btn_stop = ctk.CTkButton(bottom_frame, text="СТОП", fg_color="#D32F2F", hover_color="#C62828",
                                      state="disabled", command=self.stop_automation)
        self.btn_stop.pack(padx=20, fill="x")

        # Главная панель
        self.main_content = ctk.CTkFrame(self, fg_color="transparent")
        self.main_content.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)

        self.main_content.grid_columnconfigure(0, weight=1)

        self.main_content.grid_rowconfigure(1, weight=3)
        self.main_content.grid_rowconfigure(3, weight=1)

        header_frame = ctk.CTkFrame(self.main_content, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew")

        self.cb_select_all = ctk.CTkCheckBox(header_frame, text="Выбрать всё", variable=self.select_all_var,
                                             command=self.toggle_all_matches)
        self.cb_select_all.pack(side="left", padx=5)

        self.scroll_matches = ctk.CTkScrollableFrame(self.main_content, label_text="Матчи")
        self.scroll_matches.grid(row=1, column=0, sticky="nsew", pady=(10, 20))

        ctk.CTkLabel(self.main_content, text="Лог работы:", font=("Arial", 12, "bold")).grid(row=2, column=0,
                                                                                             sticky="w")
        self.log_console = ctk.CTkTextbox(self.main_content, font=("Consolas", 12), text_color="#A9B7C6",
                                          fg_color="#1E1E1E")
        self.log_console.grid(row=3, column=0, sticky="nsew")

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
            matches = loop.run_until_complete(fetch_matches_for_ui())
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

        for match in matches:
            # Создаем основную карточку
            card = ctk.CTkFrame(self.scroll_matches, fg_color="#2B2B2B", corner_radius=8)
            card.pack(fill="x", pady=4, padx=5)

            # ОПТИМИЗАЦИЯ: Настраиваем сетку прямо на карточке
            # Колонка 0 (для чекбокса) будет узкой, колонка 1 (для текста) заберет всё свободное место (weight=1)
            card.grid_columnconfigure(1, weight=1)

            # Чекбокс (размещаем в левой колонке, растягиваем на две строки вниз)
            var = ctk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(card, text="", variable=var, width=20)
            cb.grid(row=0, column=0, rowspan=2, padx=10, pady=10, sticky="w")

            # Заголовок матча (размещаем в правой колонке, верхняя строка)
            lbl_title = ctk.CTkLabel(card, text=match.stream_title, font=("Arial", 14, "bold"), anchor="w")
            lbl_title.grid(row=0, column=1, padx=(0, 10), pady=(5, 0), sticky="ew")

            # Подзаголовок матча (правая колонка, нижняя строка)
            lbl_sub = ctk.CTkLabel(card, text=f"{match.match_date} | Тур {match.tour_number} | {match.stadium}",
                                   text_color="gray", anchor="w")
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

        mode = self.pattern_var.get()
        test = self.test_mode_var.get()
        league = self.league_var.get()

        # Считываем цвет и защищаемся от дурака (если ввели текст)
        try:
            default_color = int(self.default_color_var.get())
        except ValueError:
            default_color = 3

        threading.Thread(target=self._run_async_publish, args=(selected, mode, test, league, default_color),
                         daemon=True).start()

    def _run_async_publish(self, selected_matches, pattern_mode, test_mode, league, default_color):
        global pipeline_task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        pipeline_task = loop.create_task(
            process_selected_matches(selected_matches, pattern_mode, test_mode, league, default_color))

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