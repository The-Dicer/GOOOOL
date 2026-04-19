import customtkinter as ctk
import tkinter as tk
import threading
import asyncio
import logging
import subprocess
import os

from main import fetch_matches_for_ui, process_selected_matches

# Настройка внешнего вида (Темная тема, синие акценты)
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

stop_event = None
pipeline_task = None

fetched_matches = []
checkbox_vars = []
select_all_var = None
pattern_var = None


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


def launch_chrome():
    try:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_path):
            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_path):
            # В ctk нет messagebox из коробки, поэтому печатаем в лог
            logging.error("Ошибка: Chrome не найден!")
            return
        profile_path = os.path.join(os.getcwd(), "chrome_debug_profile")
        os.makedirs(profile_path, exist_ok=True)
        subprocess.Popen([chrome_path, "--remote-debugging-port=9222", f"--user-data-dir={profile_path}"])
        logging.info("Изолированный Chrome запущен! Авторизуйтесь на нужных сайтах.")
    except Exception as e:
        logging.error(f"Не удалось запустить Chrome: {e}")


# ================= АСИНХРОННЫЕ ОБЕРТКИ =================

def run_async_fetch(btn_fetch, btn_publish, scrollable_frame):
    global fetched_matches, checkbox_vars, select_all_var
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        fetched_matches = loop.run_until_complete(fetch_matches_for_ui())

        def update_ui():
            # Очищаем старые чекбоксы, если они были
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            checkbox_vars.clear()

            if not fetched_matches:
                return

            select_all_var.set(True)  # Включаем "Выбрать все" по умолчанию

            def update_master(*args):
                all_checked = all(var.get() for var, _ in checkbox_vars)
                select_all_var.set(all_checked)

            # Создаем новые чекбоксы
            for match in fetched_matches:
                var = ctk.BooleanVar(value=True)
                var.trace_add("write", update_master)
                checkbox_vars.append((var, match))

                cb_text = f"{match.match_date} | {match.team_home} - {match.team_away} (Тур {match.tour_number})"
                # Используем чекбоксы ctk
                cb = ctk.CTkCheckBox(scrollable_frame, text=cb_text, variable=var, font=("Arial", 14))
                cb.pack(anchor="w", padx=10, pady=5)

            btn_publish.configure(state=tk.NORMAL)
            logging.info("✅ Матчи загружены! Снимите галочки с лишних и нажмите '3. Опубликовать'.")

        scrollable_frame.after(0, update_ui)

    except Exception as e:
        logging.error(f"Ошибка при сборе матчей: {e}")
    finally:
        loop.close()
        btn_fetch.after(0, lambda: btn_fetch.configure(state=tk.NORMAL, text="2. Собрать расписание"))


async def run_cancellable_publish(selected_matches, pattern_mode):
    global pipeline_task
    pipeline_task = asyncio.create_task(process_selected_matches(selected_matches, pattern_mode))
    try:
        await pipeline_task
    except asyncio.CancelledError:
        logging.warning("Процесс был принудительно остановлен пользователем!")
    except Exception as e:
        error_msg = str(e).lower()
        if "target closed" in error_msg or "econnrefused" in error_msg:
            logging.error("СВЯЗЬ С БРАУЗЕРОМ ПОТЕРЯНА! Похоже, вы закрыли Chrome.")
        else:
            logging.error(f"Критическая ошибка: {e}")


def run_async_publish(selected_matches, btn_publish, btn_stop, pattern_mode):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_cancellable_publish(selected_matches, pattern_mode))
    finally:
        loop.close()
        btn_publish.after(0, lambda: btn_publish.configure(state=tk.NORMAL, text="3. Опубликовать выбранные"))
        btn_stop.after(0, lambda: btn_stop.configure(state=tk.DISABLED))


# ================= КНОПКИ УПРАВЛЕНИЯ =================

def toggle_all():
    if not checkbox_vars:
        return
    state = select_all_var.get()
    for var, _ in checkbox_vars:
        var.set(state)


def start_fetch(btn_fetch, btn_publish, scrollable_frame):
    btn_fetch.configure(state=tk.DISABLED, text="Идет сбор...")
    btn_publish.configure(state=tk.DISABLED)
    threading.Thread(target=run_async_fetch, args=(btn_fetch, btn_publish, scrollable_frame), daemon=True).start()


def start_publish(btn_publish, btn_stop):
    selected_matches = [match for var, match in checkbox_vars if var.get()]
    pattern_mode = pattern_var.get()  # <--- Добавляем эту строчку

    if not selected_matches:
        logging.warning("Вы не выбрали ни одного матча для публикации!")
        return

    btn_publish.configure(state=tk.DISABLED, text="Публикуем...")
    btn_stop.configure(state=tk.NORMAL)
    # Передаем pattern_mode в args:
    threading.Thread(target=run_async_publish, args=(selected_matches, btn_publish, btn_stop, pattern_mode),
                     daemon=True).start()


def stop_automation():
    global pipeline_task
    if pipeline_task and not pipeline_task.done():
        logging.info("Посылаем сигнал остановки... Ждем прерывания текущего шага.")
        pipeline_task.get_loop().call_soon_threadsafe(pipeline_task.cancel)


# ================= ИНТЕРФЕЙС =================

def create_gui():
    global select_all_var
    global pattern_var

    # Меняем tk.Tk() на ctk.CTk()
    app = ctk.CTk()
    app.title("AFL Publisher")
    app.geometry("900x800")

    try:
        app.iconbitmap("icon.ico")
    except Exception:
        pass

    select_all_var = ctk.BooleanVar(value=True)
    pattern_var = ctk.StringVar(value="Автовыбор")

    ctk.CTkLabel(app, text="Панель управления операторами AFL", font=("Arial", 20, "bold")).pack(pady=15)

    ctk.CTkButton(app, text="1. Жмяк (Открыть Chrome с портом 9222)", font=("Arial", 14),
                  fg_color="#2E7D32", hover_color="#1B5E20", height=40, command=launch_chrome).pack(pady=5, fill="x",
                                                                                                    padx=20)

    frame_controls = ctk.CTkFrame(app, fg_color="transparent")
    frame_controls.pack(pady=10, fill="x", padx=20)

    btn_fetch = ctk.CTkButton(frame_controls, text="2. Собрать расписание", font=("Arial", 14, "bold"),
                              fg_color="#F57C00", hover_color="#E65100", height=40)
    btn_fetch.pack(side=tk.LEFT, padx=(0, 5), expand=True, fill="x")

    btn_publish = ctk.CTkButton(frame_controls, text="3. Опубликовать выбранные", font=("Arial", 14, "bold"), height=40,
                                state=tk.DISABLED)
    btn_publish.pack(side=tk.LEFT, padx=5, expand=True, fill="x")

    btn_stop = ctk.CTkButton(frame_controls, text="СТОПЭ", font=("Arial", 14, "bold"), fg_color="#D32F2F",
                             hover_color="#C62828", height=40, width=100, state=tk.DISABLED, command=stop_automation)
    btn_stop.pack(side=tk.LEFT, padx=(5, 0))

    # === БЛОК С ЗАГОЛОВКОМ И ЧЕКБОКСОМ "ВЫБРАТЬ ВСЕ" ===
    header_frame = ctk.CTkFrame(app, fg_color="transparent")
    header_frame.pack(fill=tk.X, padx=20, pady=(15, 5))

    cb_select_all = ctk.CTkCheckBox(header_frame, text="Выбрать все", font=("Arial", 14, "bold"),
                                    variable=select_all_var, command=toggle_all)
    cb_select_all.pack(side=tk.LEFT)

    # --- Переключатель паттернов (прижат вправо) ---
    pattern_segmented = ctk.CTkSegmentedButton(header_frame, variable=pattern_var,
                                               values=["Автовыбор", "Паттерн 1", "Паттерн 2"])
    pattern_segmented.pack(side=tk.RIGHT)

    ctk.CTkLabel(header_frame, text="Паттерн:", font=("Arial", 14)).pack(side=tk.RIGHT, padx=(0, 10))

    # === ИДЕАЛЬНЫЙ СКРОЛЛИРУЕМЫЙ СПИСОК МАТЧЕЙ ===
    # В ctk скролл, колесико и ползунок работают из коробки
    scrollable_frame = ctk.CTkScrollableFrame(app, label_text="")
    scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

    ctk.CTkLabel(app, text="Логи (Читаем):", font=("Arial", 14)).pack(anchor="w", padx=20, pady=(10, 0))

    # Текстовое поле (TextBox) для логов
    log_console = ctk.CTkTextbox(app, height=200, font=("Consolas", 14), text_color="#00FF00", fg_color="#1E1E1E")
    log_console.pack(padx=20, pady=(5, 20), fill=tk.BOTH, expand=False)

    ui_handler = TextHandler(log_console)
    ui_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(ui_handler)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    btn_fetch.configure(command=lambda: start_fetch(btn_fetch, btn_publish, scrollable_frame))
    btn_publish.configure(command=lambda: start_publish(btn_publish, btn_stop))

    app.mainloop()


if __name__ == "__main__":
    create_gui()
