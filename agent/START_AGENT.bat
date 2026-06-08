@echo off
title EDR Agent

set AGENT_DIR=%~dp0
if "%AGENT_DIR:~-1%"=="\" set AGENT_DIR=%AGENT_DIR:~0,-1%

echo ========================================
echo  EDR Agent
echo  Folder: %AGENT_DIR%
echo ========================================
echo.

python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python not found!
    echo Install Python from python.org
    pause
    exit /b 1
)

:loop
echo.
echo [%time%] -- Starting agent --

if exist "%AGENT_DIR%\data\agent.lock" (
    del "%AGENT_DIR%\data\agent.lock" >nul 2>&1
)

cd /d "%AGENT_DIR%"
python -u agent.py
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% equ 0 (
    echo.
    echo [%time%] Agent stopped.
    pause
    exit /b 0
)

if %EXIT_CODE% equ 99 (
    echo.
    echo [%time%] Update applied -- restarting...
    timeout /t 2 /nobreak >nul
    goto loop
)

echo.
echo [%time%] Agent exited with error (code %EXIT_CODE%).
echo         Restarting in 5 seconds... (CTRL+C to quit)
timeout /t 5 /nobreak >nul
goto loop
