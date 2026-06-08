@echo off
title EDR Agent - Laptop 2

:: --- Admin rights ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -NoProfile -Command "$f='%~f0'; $d='%~dp0'; Start-Process -FilePath $f -WorkingDirectory $d -Verb RunAs"
    exit /b
)

set BASE=C:\edr_agent
set EDR_SERVER=http://DESKTOP-CLJB78R:8088
set AGENT_ID=LAPTOP2

echo ========================================
echo  EDR Agent  -  Laptop 2
echo  Server: %EDR_SERVER%
echo ========================================
echo.

:: --- Python: venv or system ---
set VENV_PY=%BASE%\edr_agent_for_laptop2\.venv\Scripts\python.exe
if exist "%VENV_PY%" (
    set PYTHON=%VENV_PY%
    echo [OK] Python: venv
) else (
    set PYTHON=python
    echo [OK] Python: system
)

:: --- Git pull (get latest code from GitHub) ---
echo.
echo [%time%] Pulling latest code from GitHub...
cd /d "%BASE%"
git pull
echo.

:loop
echo [%time%] -- Starting agent --

if exist "%BASE%\agent\data\agent.lock" del "%BASE%\agent\data\agent.lock" >nul 2>&1

cd /d "%BASE%\agent"
"%PYTHON%" -u agent.py
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% equ 0 (
    echo [%time%] Agent stopped.
    pause
    exit /b 0
)

if %EXIT_CODE% equ 99 (
    echo [%time%] Update applied -- restarting...
    timeout /t 2 /nobreak >nul
    goto loop
)

echo [%time%] Error (code %EXIT_CODE%). Restarting in 5s...
timeout /t 5 /nobreak >nul
goto loop
