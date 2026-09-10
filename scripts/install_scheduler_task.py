import os
import sys
import subprocess
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TASK_NAME = "GOAL_Autopilot"

def generate_task_xml(pythonw_path: str, script_path: str, work_dir: str) -> str:
    """
    Генерирует XML конфигурацию задачи для Планировщика Windows:
    1. Пятница: с 12:00 каждые 2 часа (12:00, 14:00, 16:00, 18:00, 20:00, 22:00).
    2. Пятница: дополнительная проверка ровно в 17:00.
    3. Суббота: с 00:00 каждые 2 часа (00:00, 02:00, 04:00, перерыв на выключение ПК 05:00-07:00, затем 08:00, 10:00, 12:00... до 23:59).
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>2026-09-10T12:00:00</Date>
    <Author>GOAL 3.2</Author>
    <Description>Автономный мониторинг расписания и создание трансляций</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
  </Settings>
  <Triggers>
    <!-- 1. Пятница: с 12:00 каждые 2 часа в течение 12 часов -->
    <CalendarTrigger>
      <StartBoundary>2026-09-11T12:00:00</StartBoundary>
      <Repetition>
        <Interval>PT2H</Interval>
        <Duration>PT12H</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
    <!-- 2. Пятница: дополнительная проверка в 17:00 -->
    <CalendarTrigger>
      <StartBoundary>2026-09-11T17:00:00</StartBoundary>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
    <!-- 3. Суббота: с 00:00 каждые 2 часа на 24 часа (00:00, 02:00, 04:00, пауза 5-7, затем 08:00, 10:00 ... до 23:59) -->
    <CalendarTrigger>
      <StartBoundary>2026-09-12T00:00:00</StartBoundary>
      <Repetition>
        <Interval>PT2H</Interval>
        <Duration>P1D</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Saturday />
        </DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw_path}</Command>
      <Arguments>"{script_path}"</Arguments>
      <WorkingDirectory>{work_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def install_task():
    """
    Регистрация задачи в Планировщике заданий Windows через XML.
    """
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(cur_dir)
    script_path = os.path.join(project_root, "auto_runner.py")
    
    pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_path):
        pythonw_path = sys.executable

    xml_content = generate_task_xml(pythonw_path, script_path, project_root)
    xml_path = os.path.join(cur_dir, "task_schedule.xml")

    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml_content)

    print(f"=== Установка задачи '{TASK_NAME}' в Планировщик Windows ===")
    print(f"Путь скрипта: {script_path}")
    print(f"Рабочая директория: {project_root}")
    print(f"Интерпретатор: {pythonw_path}")
    print("Расписание:")
    print("  • Пятница: с 12:00 каждые 2 часа + доп. проверка в 17:00")
    print("  • Суббота: с 00:00 каждые 2 часа (00:00, 02:00, 04:00, выключение ПК 5-7, с 08:00 до 23:59)")

    schtasks_cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/xml", xml_path,
        "/f"
    ]

    try:
        res = subprocess.run(schtasks_cmd, capture_output=True, check=False)
        stdout_txt = (res.stdout or b"").decode("cp866", errors="replace").strip()
        stderr_txt = (res.stderr or b"").decode("cp866", errors="replace").strip()
        if res.returncode == 0:
            print("\n[OK] Задача успешно зарегистрирована и обновлена в Планировщике заданий Windows!")
            print("Расписание активно. Бот будет тихо проверять новые матчи и писать вам в Telegram ЛС.")
            return True
        else:
            print(f"\n[Ошибка] Код {res.returncode}: {stderr_txt or stdout_txt}")
            print("Совет: попробуйте запустить командную строку или батник от имени Администратора.")
            return False
    except Exception as e:
        print(f"\n[Ошибка] Сбой при вызове schtasks: {e}")
        return False
    finally:
        try:
            if os.path.exists(xml_path):
                os.remove(xml_path)
        except Exception:
            pass


def remove_task():
    """Удаление задачи из Планировщика заданий Windows."""
    print(f"=== Удаление задачи '{TASK_NAME}' из Планировщика Windows ===")
    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        res = subprocess.run(cmd, capture_output=True, check=False)
        stdout_txt = (res.stdout or b"").decode("cp866", errors="replace").strip()
        stderr_txt = (res.stderr or b"").decode("cp866", errors="replace").strip()
        if res.returncode == 0:
            print(f"[OK] Задача '{TASK_NAME}' успешно удалена.")
            return True
        else:
            print(f"[Инфо] {stderr_txt or stdout_txt}")
            return False
    except Exception as e:
        print(f"[Ошибка] Сбой при удалении задачи: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true", help="Удалить задачу")
    args = parser.parse_args()

    if args.remove:
        remove_task()
    else:
        install_task()
