@echo off
chcp 65001 >nul
title Сборка EDR Installer

echo ============================================
echo   Сборка установщика EDR Agent (installer_v2)
echo ============================================
echo.
echo Этот скрипт:
echo   1. Скачает Python 3.12 (встроенный, без установки)
echo   2. Установит все зависимости агента
echo   3. Подготовит папку installer_v2 готовую для флешки
echo.
echo Нужен интернет. Займёт 5-10 минут.
echo.
pause

REM ── Определяем папку скрипта ──
set ROOT=%~dp0
set BUILD_DIR=%TEMP%\edr_build
set INSTALLER=%ROOT%installer_v2

REM ── Очищаем временную папку ──
if exist "%BUILD_DIR%" rmdir /S /Q "%BUILD_DIR%"
mkdir "%BUILD_DIR%"

REM ── Скачиваем Python embeddable ──
echo.
echo [1/4] Скачиваем Python 3.12...
set PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
set PY_ZIP=%BUILD_DIR%\python_embed.zip
powershell -Command "(New-Object Net.WebClient).DownloadFile('%PY_URL%', '%PY_ZIP%')"
if %errorLevel% neq 0 (
    echo [ОШИБКА] Не удалось скачать Python. Проверь интернет.
    pause & exit /b 1
)
echo [OK] Python скачан.

REM ── Распаковываем Python ──
echo.
echo [2/4] Распаковываем Python...
set PY_DIR=%BUILD_DIR%\python
powershell -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%PY_DIR%' -Force"
echo [OK] Распакован.

REM ── Включаем site-packages ──
echo python312.zip>  "%PY_DIR%\python312._pth"
echo .>>             "%PY_DIR%\python312._pth"
echo.>>              "%PY_DIR%\python312._pth"
echo import site>>   "%PY_DIR%\python312._pth"

REM ── Устанавливаем pip ──
echo.
echo [3/4] Устанавливаем pip...
powershell -Command "(New-Object Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', '%BUILD_DIR%\get-pip.py')"
"%PY_DIR%\python.exe" "%BUILD_DIR%\get-pip.py" --no-warn-script-location >nul 2>&1
echo [OK] pip установлен.

REM ── Устанавливаем зависимости агента ──
echo.
echo [4/4] Устанавливаем зависимости (может занять 5-7 минут)...
"%PY_DIR%\python.exe" -m pip install --no-warn-script-location --quiet ^
    requests psutil pywin32 pyperclip python-dotenv ^
    Pillow python-docx openpyxl python-pptx pdfplumber ^
    PyMuPDF pytesseract joblib scikit-learn
if %errorLevel% neq 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Некоторые пакеты не установились, но агент всё равно запустится.
) else (
    echo [OK] Все зависимости установлены.
)

REM ── Копируем Python в installer_v2 ──
echo.
echo Копируем Python в installer_v2...
if exist "%INSTALLER%\python" rmdir /S /Q "%INSTALLER%\python"
xcopy /E /I /Y /Q "%PY_DIR%" "%INSTALLER%\python" >nul
echo [OK] Python скопирован.

REM ── Проверка ──
echo.
echo Проверка...
"%INSTALLER%\python\python.exe" -c "import requests, psutil, win32file; print('[OK] Все модули работают')"

REM ── Итог ──
echo.
echo ============================================
echo   ГОТОВО!
echo ============================================
echo.
echo Папка installer_v2 готова к копированию на флешку.
echo Размер:
powershell -Command "$s=(Get-ChildItem '%INSTALLER%' -Recurse | Measure-Object -Property Length -Sum).Sum/1MB; Write-Host ('  ' + [math]::Round($s) + ' MB')"
echo.
echo Что делать дальше:
echo   1. Скопируй папку installer_v2 на флешку
echo   2. На нужном ноутбуке открой флешку
echo   3. Двойной клик на START.bat
echo   4. Готово!
echo.
pause
