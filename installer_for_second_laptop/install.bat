@echo off
chcp 65001 >nul
title EDR Agent - Установка

echo =======================================
echo  EDR Agent - Установка на школьный ПК
echo =======================================
echo.

REM ── Адрес сервера (менять только если сменился ноутбук-сервер) ──
set SERVER=DESKTOP-CLJB78R
set SERVER_URL=http://%SERVER%:8088

REM ── Проверка прав администратора ──────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ОШИБКА] Запустите install.bat от имени администратора!
    echo   Правая кнопка мыши на install.bat -^> "Запуск от имени администратора"
    pause
    exit /b 1
)
echo [OK] Права администратора.

REM ── Проверка Python ────────────────────────────────────────
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [ОШИБКА] Python не установлен!
    echo.
    echo   Установи Python ПЕРЕД запуском этого файла:
    echo   1. Открой браузер: https://www.python.org/downloads/
    echo   2. Скачай Python 3.10 или новее
    echo   3. При установке поставь галочку "Add Python to PATH"
    echo   4. Перезагрузи компьютер
    echo   5. Запусти install.bat снова
    echo.
    pause
    exit /b 1
)
echo [OK] Python:
python --version

REM ── Создание папки агента ──────────────────────────────────
echo.
echo [*] Устанавливаем агент в C:\EDR\agent ...
mkdir C:\EDR >nul 2>&1
mkdir C:\EDR\agent >nul 2>&1
mkdir C:\EDR\agent\data >nul 2>&1
mkdir C:\EDR\agent\logs >nul 2>&1

REM ── Копирование файлов ────────────────────────────────────
echo [*] Копируем файлы агента...
xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent" >nul
echo [OK] Файлы скопированы.

REM ── Настройка config.ini ─────────────────────────────────
echo [*] Создаём config.ini ...
(
    echo [server]
    echo host     = %SERVER%
    echo port     = 8088
    echo base_url = %SERVER_URL%
    echo url      = %SERVER_URL%
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
echo [OK] config.ini: сервер = %SERVER_URL%

REM ── Установка зависимостей ────────────────────────────────
echo.
echo [*] Устанавливаем зависимости Python (3-10 минут)...
cd /d C:\EDR\agent
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %errorLevel% neq 0 (
    echo [!] Некоторые пакеты не установились.
    echo     Агент запустится, часть функций может не работать.
) else (
    echo [OK] Все зависимости установлены.
)

REM ── Ярлык на Рабочем столе ───────────────────────────────
echo.
echo [*] Создаём ярлык на Рабочем столе...
(
    echo @echo off
    echo cd /d C:\EDR\agent
    echo call START_AGENT.bat
) > "%USERPROFILE%\Desktop\EDR Agent.bat"
echo [OK] Ярлык: Рабочий стол\EDR Agent.bat

REM ── Проверка связи с сервером ────────────────────────────
echo.
echo [*] Проверяем связь с сервером %SERVER_URL% ...
python -c "import urllib.request; urllib.request.urlopen('%SERVER_URL%/health', timeout=5); print('[OK] Сервер доступен!')" 2>nul
if %errorLevel% neq 0 (
    echo [!] Сервер недоступен прямо сейчас.
    echo     Убедись что на первом ноутбуке запущен START_EDR.bat
    echo     Агент запустится в автономном режиме и подключится когда сервер появится.
)

echo.
echo ====================================================
echo  УСТАНОВКА ЗАВЕРШЕНА
echo  Агент:  C:\EDR\agent
echo  Сервер: %SERVER_URL%
echo  Ярлык:  Рабочий стол\EDR Agent.bat
echo ====================================================
echo.
echo  Запускаем агент...
echo  Закрой это окно чтобы остановить агент.
echo.

cd /d C:\EDR\agent
call START_AGENT.bat
