@echo off
chcp 65001 >nul
title EDR Agent - Установка

echo ================================================
echo  EDR Agent - Автоматическая установка
echo  Сервер: DESKTOP-CLJB78R
echo ================================================
echo.

REM ── Адрес сервера ────────────────────────────────────────
set SERVER=DESKTOP-CLJB78R
set SERVER_URL=http://%SERVER%:8088

REM ── Автоматический запрос прав администратора ────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [*] Запрашиваем права администратора...
    powershell -NoProfile -Command "$f='%~f0'; $d='%~dp0'; Start-Process -FilePath $f -WorkingDirectory $d -Verb RunAs"
    exit /b
)
echo [OK] Права администратора.
echo.

REM ── Проверка Python ───────────────────────────────────────
python --version >nul 2>&1
if %errorLevel% equ 0 goto python_ok

echo [*] Python не найден. Скачиваем Python 3.12...
echo     Нужен интернет. Займёт 2-4 минуты.
echo.

set PYTMP=%TEMP%\python_edr_setup.exe
set PYURL=https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe

powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYTMP%' -UseBasicParsing"

if not exist "%PYTMP%" (
    echo.
    echo [ОШИБКА] Не удалось скачать Python.
    echo.
    echo Варианты:
    echo   1. Проверь интернет-соединение и запусти install.bat снова
    echo   2. Или установи Python вручную: python.org/downloads
    echo      При установке поставь галочку "Add Python to PATH"
    echo      Потом запусти install.bat снова
    echo.
    pause
    exit /b 1
)

echo [*] Устанавливаем Python 3.12 (тихая установка)...
"%PYTMP%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
del "%PYTMP%" >nul 2>&1

echo [*] Обновляем PATH...
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
set "PATH=%SYS_PATH%;%PATH%"

python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [!] Python установлен, но нужна перезагрузка.
    echo     Перезагрузи компьютер и запусти install.bat снова.
    echo     (Python скачивать повторно НЕ нужно)
    echo.
    pause
    exit /b 0
)
echo [OK] Python установлен успешно!

:python_ok
echo [OK] Python найден:
python --version
echo.

REM ── Создание папки агента ────────────────────────────────
echo [*] Создаём C:\EDR\agent ...
mkdir C:\EDR 2>nul
mkdir C:\EDR\agent 2>nul
mkdir C:\EDR\agent\data 2>nul
mkdir C:\EDR\agent\logs 2>nul
echo [OK] Папка создана.

REM ── Копирование файлов ───────────────────────────────────
echo [*] Копируем файлы агента...
xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent"
if %errorLevel% neq 0 (
    echo [ОШИБКА] Не удалось скопировать файлы!
    pause
    exit /b 1
)
echo [OK] Файлы скопированы.
echo.

REM ── Создание config.ini ──────────────────────────────────
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
echo [OK] Сервер: %SERVER_URL%
echo.

REM ── Установка зависимостей ───────────────────────────────
echo [*] Устанавливаем зависимости (3-10 минут)...
cd /d C:\EDR\agent
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo.
    echo [!] Некоторые пакеты не установились.
    echo     Агент запустится, часть функций может не работать.
    echo.
) else (
    echo [OK] Все зависимости установлены.
    echo.
)

REM ── Ярлык на Рабочем столе ───────────────────────────────
echo [*] Создаём ярлык на Рабочем столе...
(
    echo @echo off
    echo cd /d C:\EDR\agent
    echo call START_AGENT.bat
) > "%USERPROFILE%\Desktop\EDR Agent.bat"
echo [OK] Ярлык: Рабочий стол\EDR Agent.bat
echo.

REM ── Связь с сервером ─────────────────────────────────────
echo [*] Проверяем связь с сервером %SERVER_URL% ...
python -c "import urllib.request; urllib.request.urlopen('%SERVER_URL%/health', timeout=5); print('[OK] Сервер доступен!')" 2>nul
if %errorLevel% neq 0 (
    echo [!] Сервер недоступен сейчас - это нормально.
    echo     Агент подключится автоматически когда сервер запустится.
)
echo.

echo ================================================
echo  УСТАНОВКА ЗАВЕРШЕНА!
echo.
echo  Агент запускается...
echo  Закрой это окно чтобы остановить агент.
echo ================================================
echo.

cd /d C:\EDR\agent
call START_AGENT.bat
