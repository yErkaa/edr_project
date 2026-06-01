@echo off
chcp 65001 >nul
title EDR Agent

echo ================================
echo   EDR Agent - Защита ПД
echo ================================
echo.

REM ── Создаём папку карантина (нужны права администратора) ──
if not exist "C:\EDR_Quarantine" (
    mkdir "C:\EDR_Quarantine" >nul 2>&1
)

REM ── Копируем файлы агента в C:\EDR (если ещё не скопированы) ──
if not exist "C:\EDR\agent\agent.py" (
    echo Первый запуск: копируем файлы агента...
    mkdir "C:\EDR\agent\data" >nul 2>&1
    mkdir "C:\EDR\agent\logs" >nul 2>&1
    xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent" >nul
    echo Готово.
    echo.
)

REM ── Запускаем агент через встроенный Python ──
echo Запускаем агент...
echo Сервер: http://DESKTOP-CLJB78R:8088
echo.
echo (Не закрывайте это окно - агент работает пока окно открыто)
echo (Чтобы остановить агент - закройте это окно)
echo.

"%~dp0python\python.exe" -u "C:\EDR\agent\agent.py"

echo.
echo Агент остановлен.
pause
