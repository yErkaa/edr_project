@echo off
chcp 65001 >nul
title EDR Agent - Установка на школьный ПК

echo =======================================
echo  EDR Agent - Установка на школьный ПК
echo =======================================
echo.

REM ── Проверка прав администратора ──────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ОШИБКА] Запустите install.bat от имени администратора!
    echo   Правая кнопка мыши на install.bat -^> "Запуск от имени администратора"
    pause
    exit /b 1
)
echo [OK] Права администратора есть.

REM ── Проверка Python ────────────────────────────────────────
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [ОШИБКА] Python не установлен!
    echo   1. Откройте браузер и зайдите на https://www.python.org/downloads/
    echo   2. Скачайте Python 3.10 или новее
    echo   3. При установке ОБЯЗАТЕЛЬНО поставьте галочку "Add Python to PATH"
    echo   4. После установки запустите install.bat снова
    pause
    exit /b 1
)
echo [OK] Python найден.
python --version

REM ── Ввод IP сервера ────────────────────────────────────────
echo.
echo Введи IP-адрес сервера (первый ноутбук, вида 192.168.X.X):
set /p SERVER_IP="IP сервера: "

if "%SERVER_IP%"=="" (
    echo [ОШИБКА] IP не введён!
    pause
    exit /b 1
)
echo [OK] Сервер: http://%SERVER_IP%:8088

REM ── Создание папки C:\EDR\agent ────────────────────────────
echo.
echo [*] Создаём C:\EDR\agent ...
if exist "C:\EDR\agent" (
    echo     Папка уже существует, обновляем файлы...
)
mkdir C:\EDR >nul 2>&1
mkdir C:\EDR\agent >nul 2>&1
mkdir C:\EDR\agent\data >nul 2>&1
mkdir C:\EDR\agent\logs >nul 2>&1

REM ── Копирование файлов агента ──────────────────────────────
echo [*] Копируем файлы агента...
xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent" >nul
echo [OK] Файлы скопированы.

REM ── Настройка config.ini ───────────────────────────────────
echo [*] Настраиваем config.ini с IP сервера...
(
    echo [server]
    echo host     = %SERVER_IP%
    echo port     = 8088
    echo base_url = http://%SERVER_IP%:8088
    echo url      = http://%SERVER_IP%:8088
    echo.
    echo [agent]
    echo agent_id    =
    echo id          =
    echo watch_paths =
    echo.
    echo [protection]
    echo enabled              = true
    echo password             = school2024
    echo max_attempts         = 3
    echo block_mail           = false
    echo monitor_clipboard    = true
    echo min_confidence       = 0.15
    echo min_confidence_image = 0.40
) > "C:\EDR\agent\config.ini"
echo [OK] config.ini создан.

REM ── Установка зависимостей ─────────────────────────────────
echo.
echo [*] Устанавливаем Python-зависимости (может занять 2-5 минут)...
cd /d C:\EDR\agent
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %errorLevel% neq 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Некоторые пакеты не установились.
    echo     Агент всё равно запустится, часть функций может не работать.
) else (
    echo [OK] Все зависимости установлены.
)

REM ── Создание ярлыка для запуска агента ────────────────────
echo.
echo [*] Создаём ярлык на Рабочем столе...
set DESKTOP=%USERPROFILE%\Desktop
set SHORTCUT_PATH=%DESKTOP%\EDR Agent.bat
(
    echo @echo off
    echo cd /d C:\EDR\agent
    echo call START_AGENT.bat
) > "%SHORTCUT_PATH%"
echo [OK] Ярлык создан: %SHORTCUT_PATH%

REM ── Финальная проверка подключения к серверу ─────────────
echo.
echo [*] Проверяем подключение к серверу...
python -c "import urllib.request; urllib.request.urlopen('http://%SERVER_IP%:8088/health', timeout=5); print('[OK] Сервер доступен!')" 2>nul
if %errorLevel% neq 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Сервер сейчас недоступен.
    echo     Убедись что на первом ноутбуке запущен START_EDR.bat или START_SERVER_ONLY.bat
    echo     Агент сохранит события локально и отправит когда сервер появится.
)

echo.
echo ============================================================
echo  [ГОТОВО] Агент установлен в C:\EDR\agent
echo  Сервер:  http://%SERVER_IP%:8088
echo  Ярлык:   Рабочий стол -^> "EDR Agent.bat"
echo ============================================================
echo.
echo Запускаем агент через START_AGENT.bat...
echo (Закройте это окно чтобы остановить агент)
echo.

cd /d C:\EDR\agent
call START_AGENT.bat
