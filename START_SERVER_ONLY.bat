@echo off
chcp 65001 >nul
title EDR Server

set BASE=C:\Users\dinar\PycharmProjects\edr
set UVICORN=%BASE%\.venv\Scripts\uvicorn.exe

echo ============================================
echo  EDR Server - School Personal Data Protection
echo  Dashboard: http://127.0.0.1:8088
echo  Login: admin / (пароль из server\.env — ADMIN_PASSWORD)
echo ============================================
echo.

if not exist "%UVICORN%" (
    echo [ERROR] uvicorn not found: %UVICORN%
    echo Run: pip install uvicorn
    pause
    exit /b 1
)

:: Kill any process already on port 8088
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8088 " ^| findstr "LISTENING"') do (
    echo [*] Stopping process on port 8088 (PID %%a)...
    taskkill /F /PID %%a >nul 2>&1
)

echo [*] Starting EDR Server on port 8088...
echo     Accessible from other PCs at: http://192.168.0.3:8088
echo.

cd /d %BASE%\server
%UVICORN% api:app --host 0.0.0.0 --port 8088

pause
