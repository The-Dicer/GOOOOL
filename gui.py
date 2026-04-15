import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import asyncio
import logging
import subprocess
import os

# Импортируем нашу главную функцию из main.py
from main import main as run_pipeline

# Глобальный флаг для остановки
stop_event = None
pipeline_task = None


class TextHandler(logging.Handler):
    # Перехват логов для UI

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
    # Открывает изолированный Chrome с портом 9222
    try:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_path):
            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

        if not os.path.exists(chrome_path):
            messagebox.showerror("Ошибка", "Chrome не найден по стандартному пути!")
            return

        profile_path = os.path.join(os.getcwd(), "chrome_debug_profile")
        os.makedirs(profile_path, exist_ok=True)

        subprocess.Popen([
            chrome_path,
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile_path}"
        ])

        logging.info("Изолированный Chrome запущен!")
        logging.info(
            "ВНИМАНИЕ: Авторизуйся в AFL Graphics, Rutube и Footballista в этом окне, затем жми 'Запуск'. Footballista открой на вкладке со всеми играми, а еще один раз хром попросит ДОСТУП К БУФЕРУ ОБМЕНА")
    except Exception as e:
        logging.error(f"Не удалось запустить Chrome: {e}")


async def run_cancellable_pipeline():
    # Асинхронная обертка, которую можно отменить
    global pipeline_task
    pipeline_task = asyncio.create_task(run_pipeline())
    try:
        await pipeline_task
        logging.info("Все задачи выполнены!")
    except asyncio.CancelledError:
        logging.warning("Процесс был принудительно остановлен пользователем! Повторный запуск начнет все с начала")
    except Exception as e:
        # НОВЫЙ БЛОК: ОБРАБОТКА СМЕРТИ БРАУЗЕРА
        error_msg = str(e).lower()
        if "target closed" in error_msg or "econnrefused" in error_msg:
            logging.error("СВЯЗЬ С БРАУЗЕРОМ ПОТЕРЯНА! Похоже, вы закрыли Chrome. Либо вы Женя.")
            logging.info("Нажмите '1. Жмыяк...' и запустите процесс заново.")
        else:
            logging.error(f"Непредвиденная ошибка оркестратора: {e}")


def run_async_loop(btn_start, btn_stop):
    # Запускает event loop в отдельном потоке
    global stop_event

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    logging.info("Запуск процесса...")

    try:
        loop.run_until_complete(run_cancellable_pipeline())
    finally:
        loop.close()
        # Восстанавливаем состояние кнопок
        btn_start.after(0, lambda: btn_start.config(state=tk.NORMAL,
                                                    text="2. Погнали"))
        btn_stop.after(0, lambda: btn_stop.config(state=tk.DISABLED))


def start_automation(btn_start, btn_stop):
    # Кнопка 'Старт'
    btn_start.config(state=tk.DISABLED, text="Выполняется (Смотрите лог)...")
    btn_stop.config(state=tk.NORMAL)

    threading.Thread(target=run_async_loop, args=(btn_start, btn_stop), daemon=True).start()


def stop_automation():
    # Кнопка 'Стоп'
    global pipeline_task
    if pipeline_task and not pipeline_task.done():
        logging.info("Посылаем сигнал остановки... Ждем прерывания текущего шага.")
        # Отправляем сигнал CancelledError внутрь запущенного main.py
        pipeline_task.get_loop().call_soon_threadsafe(pipeline_task.cancel)


def create_gui():
    root = tk.Tk()
    root.title("AFL Publisher")
    root.geometry("850x650")
    root.configure(bg="#f0f0f0")

    try:
        root.iconbitmap("icon.ico")
    except Exception:
        pass

    lbl_title = tk.Label(root, text="Панель управления операторами AFL", font=("Arial", 16, "bold"), bg="#f0f0f0")
    lbl_title.pack(pady=10)

    btn_chrome = tk.Button(root, text="1. Жмыяк (Открыть Chrome с портом 9222)",
                           font=("Arial", 12), bg="#4CAF50", fg="white",
                           command=launch_chrome, width=50, height=2)
    btn_chrome.pack(pady=5)

    # Фрейм для кнопок Старт и Стоп
    frame_controls = tk.Frame(root, bg="#f0f0f0")
    frame_controls.pack(pady=10)

    btn_start = tk.Button(frame_controls, text="2. Погнали",
                          font=("Arial", 12, "bold"), bg="#008CBA", fg="white",
                          width=50, height=2)

    btn_stop = tk.Button(frame_controls, text="СТОПЭ",
                         font=("Arial", 12, "bold"), bg="#f44336", fg="white",
                         width=10, height=2, state=tk.DISABLED, command=stop_automation)

    btn_start.config(command=lambda: start_automation(btn_start, btn_stop))

    btn_start.pack(side=tk.LEFT, padx=5)
    btn_stop.pack(side=tk.LEFT, padx=5)

    lbl_log = tk.Label(root, text="Логи (Читаем):", font=("Arial", 10), bg="#f0f0f0")
    lbl_log.pack(anchor="w", padx=20)

    log_console = scrolledtext.ScrolledText(root, state='disabled', width=90, height=20, bg="black", fg="lightgreen",
                                            font=("Consolas", 10))
    log_console.pack(padx=20, pady=5, fill=tk.BOTH, expand=True)

    ui_handler = TextHandler(log_console)
    ui_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(ui_handler)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    root.mainloop()


if __name__ == "__main__":
    create_gui()
