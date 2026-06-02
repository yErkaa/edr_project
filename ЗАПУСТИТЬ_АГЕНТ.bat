@echo off
chcp 65001 >nul 2>&1

REM Запрашиваем права администратора если нет
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title EDR Agent

set BASE=C:\edr_agent

echo ========================================
echo   EDR Agent  -  Laptop 2
echo   Папка: %BASE%
echo ========================================
echo.

REM ── Настройки подключения ──────────────────────────────────
REM Адрес сервера (ноутбук 1). Меняй если изменился IP.
set EDR_SERVER=http://DESKTOP-CLJB78R:8088

REM Имя этого агента в дашборде
set AGENT_ID=LAPTOP2

echo [OK] Сервер: %EDR_SERVER%
echo [OK] Агент:  %AGENT_ID%
echo.

REM ── Python ─────────────────────────────────────────────────
set VENV_PY=%BASE%\edr_agent_for_laptop2\.venv\Scripts\python.exe
if exist "%VENV_PY%" (
    set PYTHON=%VENV_PY%
    echo [OK] Python: venv
) else (
    set PYTHON=python
    echo [OK] Python: системный
)

REM ── Обновление кода из GitHub (один раз при старте) ────────
echo.
echo [%time%] Обновление кода из GitHub...
cd /d "%BASE%"
git pull
echo.

:loop
echo [%time%] -- Запуск агента --

REM Удаляем устаревший lock файл
if exist "%BASE%\agent\data\agent.lock" (
    del "%BASE%\agent\data\agent.lock" >nul 2>&1
)

cd /d "%BASE%\agent"
"%PYTHON%" -u agent.py
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% equ 0 (
    echo.
    echo [%time%] Агент остановлен.
    pause
    exit /b 0
)

if %EXIT_CODE% equ 99 (
    echo.
    echo [%time%] Обновление применено -- перезапуск...
    timeout /t 2 /nobreak >nul
    goto loop
)

echo.
echo [%time%] Агент завершился с ошибкой (код %EXIT_CODE%).
echo         Перезапуск через 5 секунд... (CTRL+C чтобы выйти)
timeout /t 5 /nobreak >nul
goto loop
