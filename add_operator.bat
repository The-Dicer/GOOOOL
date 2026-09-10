@echo off
chcp 65001 > nul
set "NO_PROXY=localhost,127.0.0.1,::1"
set "no_proxy=localhost,127.0.0.1,::1"
echo ============================================================
echo      GOAL 3.2: Мастер подключения операторов
echo ============================================================
echo.
python "%~dp0scripts\add_operator.py"
echo.
pause
