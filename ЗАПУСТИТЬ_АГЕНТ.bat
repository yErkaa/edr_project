@echo off
:: Проверяем права администратора — если нет, перезапускаемся с ними
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Запрашиваю права администратора...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title EDR Agent — School Protection
color 0A
echo ========================================
echo   EDR AGENT  -  School Protection
echo ========================================
echo.

:: Обновляем код из GitHub
echo [%time%] Проверяю обновления...
cd /d "C:\edr_agent"
git pull
echo.

:: Запускаем агент с авторестартом
set VENV=C:\edr_agent\edr_agent_for_laptop2\.venv\Scripts\python.exe
set AGENT=C:\edr_agent\agent\agent.py

:loop
echo [%time%] Запускаю агент...
"%VENV%" "%AGENT%"
echo [%time%] Агент остановился. Перезапуск через 5 секунд...
timeout /t 5 /nobreak
goto loop
