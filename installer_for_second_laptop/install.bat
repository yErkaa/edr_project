@echo off
chcp 65001 >nul
title EDR Agent - Установка

echo ================================================
echo  EDR Agent - Автоматическая установка
echo  Сервер: DESKTOP-CLJB78R
echo ================================================
echo.

REM ── Адрес сервера (менять только если сменился сервер) ────
set SERVER=DESKTOP-CLJB78R
set SERVER_URL=http://%SERVER%:8088

REM ── Права администратора ─────────────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Нужны права администратора. Перезапускаем...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
echo [OK] Права администратора.

REM ── Python: проверяем, при необходимости скачиваем ───────
python --version >nul 2>&1
if %errorLevel% equ 0 goto python_ok

echo.
echo [*] Python не найден. Скачиваем автоматически...
echo     (нужен интернет, займёт 2-4 минуты)
echo.

set PYTHON_URL=https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe
set PYTHON_TMP=%TEMP%\python_edr_setup.exe

powershell -NoProfile -Command ^
    "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_TMP%' -UseBasicParsing" >nul 2>&1

if not exist "%PYTHON_TMP%" (
    echo [ОШИБКА] Не удалось скачать Python.
    echo          Проверь интернет-соединение и запусти install.bat снова.
    pause
    exit /b 1
)

echo [*] Устанавливаем Python 3.12 ...
"%PYTHON_TMP%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
del "%PYTHON_TMP%" >nul 2>&1

REM Обновляем PATH из реестра в текущей сессии
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
set "PATH=%SYS_PATH%;%PATH%"

python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [!] Python установлен, но требует перезагрузки.
    echo     Перезагрузи компьютер и запусти install.bat снова.
    pause
    exit /b 0
)
echo [OK] Python установлен!

:python_ok
echo [OK] Python:
python --version

REM ── Создание папки агента ────────────────────────────────
echo.
echo [*] Устанавливаем агент в C:\EDR\agent ...
mkdir C:\EDR >nul 2>&1
mkdir C:\EDR\agent >nul 2>&1
mkdir C:\EDR\agent\data >nul 2>&1
mkdir C:\EDR\agent\logs >nul 2>&1

REM ── Копирование файлов ───────────────────────────────────
echo [*] Копируем файлы агента...
xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent" >nul
echo [OK] Файлы скопированы.

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

REM ── Установка зависимостей ───────────────────────────────
echo.
echo [*] Устанавливаем зависимости Python (3-10 минут)...
cd /d C:\EDR\agent
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %errorLevel% neq 0 (
    echo [!] Часть пакетов не установилась.
    echo     Агент запустится, некоторые функции могут не работать.
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
echo [OK] Ярлык создан: Рабочий стол\EDR Agent.bat

REM ── Проверка связи с сервером ────────────────────────────
echo.
echo [*] Проверяем связь с сервером...
python -c "import urllib.request; urllib.request.urlopen('%SERVER_URL%/health', timeout=5); print('[OK] Сервер доступен!')" 2>nul
if %errorLevel% neq 0 (
    echo [!] Сервер сейчас недоступен (это нормально если он ещё не запущен).
    echo     Агент сохранит данные локально и подключится когда сервер появится.
)

echo.
echo ================================================
echo  ГОТОВО! Агент установлен.
echo  Запуск в следующий раз: Рабочий стол\EDR Agent.bat
echo ================================================
echo.
echo Запускаем агент...
echo (Закрой это окно чтобы остановить агент)
echo.

cd /d C:\EDR\agent
call START_AGENT.bat
