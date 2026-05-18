@echo off
title EDR System - School Personal Data Protection

set BASE=C:\Users\dinar\PycharmProjects\edr
set PYTHON=%BASE%\.venv\Scripts\python.exe
set UVICORN=%BASE%\.venv\Scripts\uvicorn.exe

echo ============================================
echo  EDR - School Personal Data Protection
echo  Starting Server and Agent...
echo ============================================

:: Check .venv folder exists
if not exist "%BASE%\.venv" (
    echo [ERROR] .venv not found at: %BASE%\.venv
    echo Please create the virtual environment first.
    pause
    exit /b 1
)

:: Check python.exe exists
if not exist "%PYTHON%" (
    echo [ERROR] python.exe not found: %PYTHON%
    pause
    exit /b 1
)

:: Check uvicorn.exe exists
if not exist "%UVICORN%" (
    echo [ERROR] uvicorn.exe not found: %UVICORN%
    pause
    exit /b 1
)

:: Kill ALL python/pythonw processes (prevents duplicate agents)
echo [*] Stopping all Python processes...
taskkill /F /IM python.exe   >nul 2>&1
taskkill /F /IM pythonw.exe  >nul 2>&1

:: Kill old EDR windows by title (uvicorn etc.)
echo [*] Stopping old EDR Server and Agent windows...
taskkill /F /FI "WINDOWTITLE eq EDR Server" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq EDR Agent"  >nul 2>&1

:: Kill any process listening on port 8088
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8088 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Remove stale lock file if agent was killed
if exist "%BASE%\agent\data\agent.lock" del /F /Q "%BASE%\agent\data\agent.lock" >nul 2>&1

echo [*] Waiting 2 seconds for processes to stop...
timeout /t 2 /nobreak >nul

:: Create log directories if missing
if not exist "%BASE%\agent\logs"  mkdir "%BASE%\agent\logs"
if not exist "%BASE%\server\logs" mkdir "%BASE%\server\logs"

:: -------------------------------------------------------
:: Window 1: FastAPI Server (cd into server\ for imports)
:: -------------------------------------------------------
echo [*] Starting EDR Server on port 8088...
start "EDR Server" cmd /k "cd /d %BASE%\server && %UVICORN% api:app --host 0.0.0.0 --port 8088"

:: Wait 3 seconds for the server to initialize
echo [*] Waiting 3 seconds for server to start...
timeout /t 3 /nobreak >nul

:: -------------------------------------------------------
:: Window 2: Python Agent (cd into agent\ for imports)
:: -------------------------------------------------------
echo [*] Starting EDR Agent...
start "EDR Agent" cmd /k "cd /d %BASE%\agent && %PYTHON% -u agent.py"

:: Open dashboard in browser
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8088"

echo.
echo ============================================
echo  [OK] Server:     http://127.0.0.1:8088
echo  [OK] Login:      admin / admin123
echo  [OK] Agent log:  agent\logs\agent.log
echo  [OK] Server log: server\logs\server.log
echo ============================================
echo.
echo  Close this window - server and agent keep running.
pause