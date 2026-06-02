@echo off
chcp 65001 >nul
title EDR Agent

echo ========================================
echo  EDR Agent - Автозапуск
echo ========================================
echo.

REM Проверка Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не установлен!
    echo Установите Python с python.org и запустите снова.
    pause
    exit /b 1
)

:loop
echo.
echo [%time%] ── Запуск агента ──

REM Удаляем устаревший lock файл перед каждым стартом
if exist "C:\EDR\agent\data\agent.lock" (
    del "C:\EDR\agent\data\agent.lock" >nul 2>&1
)

cd /d C:\EDR\agent
python -u agent.py
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% equ 0 (
    echo.
    echo [%time%] Агент остановлен.
    pause
    exit /b 0
)

if %EXIT_CODE% equ 99 (
    echo.
    echo [%time%] Обновление применено — перезапуск...
    timeout /t 2 /nobreak >nul
    goto loop
)

echo.
echo [%time%] Агент завершился с ошибкой (код %EXIT_CODE%).
echo         Перезапуск через 5 секунд... (CTRL+C чтобы выйти)
timeout /t 5 /nobreak >nul
goto loop
