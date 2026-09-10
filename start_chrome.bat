@echo off
chcp 65001 > nul
set "NO_PROXY=localhost,127.0.0.1,::1"
set "no_proxy=localhost,127.0.0.1,::1"
echo ============================================================
echo      GOAL 3.2: Запуск Chrome для Rutube и Footballista
echo ============================================================
echo.
echo Открытие браузера с рабочим профилем...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%~dp0chrome_debug_profile" "https://studio.rutube.ru/streams"
echo Chrome запущен на порту 9222.
timeout /t 3 > nul
