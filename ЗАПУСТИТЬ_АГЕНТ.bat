@echo off
chcp 65001 >nul 2>&1

:: Request admin rights if not already elevated
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title EDR Agent - School Protection
color 0A
echo ========================================
echo   EDR AGENT  -  School Protection
echo ========================================
echo.

:: Pull latest code from GitHub
echo [%time%] Checking for updates...
cd /d "C:\edr_agent"
git pull
echo.

set VENV=C:\edr_agent\edr_agent_for_laptop2\.venv\Scripts\python.exe
set AGENT=C:\edr_agent\agent\agent.py

:loop
echo [%time%] Starting agent...
"%VENV%" "%AGENT%"
echo [%time%] Agent stopped. Restarting in 5 seconds...
timeout /t 5 /nobreak
goto loop
