@echo off
title EDR Agent - Install

echo ==================================================
echo  EDR Agent - Auto Install
echo  Server: DESKTOP-CLJB78R
echo ==================================================
echo.

set SERVER=DESKTOP-CLJB78R
set SERVER_URL=http://%SERVER%:8088

:: --- Admin rights check ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [*] Requesting admin rights...
    powershell -NoProfile -Command "$f='%~f0'; $d='%~dp0'; Start-Process -FilePath $f -WorkingDirectory $d -Verb RunAs"
    exit /b
)
echo [OK] Admin rights confirmed.
echo.

:: --- Python check / auto-install ---
python --version >nul 2>&1
if %errorLevel% equ 0 goto python_ok

echo [*] Python not found. Downloading Python 3.12...
echo     Requires internet. Takes 2-4 minutes.
echo.

set PYTMP=%TEMP%\python_edr_setup.exe
set PYURL=https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe

powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYTMP%' -UseBasicParsing"

if not exist "%PYTMP%" (
    echo.
    echo [ERROR] Failed to download Python.
    echo.
    echo Options:
    echo   1. Check internet and run install.bat again
    echo   2. Or install manually: python.org/downloads
    echo      Check "Add Python to PATH" during install
    echo      Then run install.bat again
    echo.
    pause
    exit /b 1
)

echo [*] Installing Python 3.12...
"%PYTMP%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
del "%PYTMP%" >nul 2>&1

:: Refresh PATH
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
set "PATH=%SYS_PATH%;%PATH%"

python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [!] Python installed but needs reboot.
    echo     Restart PC and run install.bat again.
    echo     (Python will NOT be downloaded again)
    echo.
    pause
    exit /b 0
)
echo [OK] Python installed!

:python_ok
echo [OK] Python:
python --version
echo.

:: --- Create agent folder ---
echo [*] Creating C:\EDR\agent ...
mkdir C:\EDR 2>nul
mkdir C:\EDR\agent 2>nul
mkdir C:\EDR\agent\data 2>nul
mkdir C:\EDR\agent\logs 2>nul
echo [OK] Folder ready.

:: --- Copy files ---
echo [*] Copying agent files...
xcopy /E /I /Y /Q "%~dp0agent" "C:\EDR\agent"
if %errorLevel% neq 0 (
    echo [ERROR] Failed to copy files!
    pause
    exit /b 1
)
echo [OK] Files copied.
echo.

:: --- Create config.ini ---
echo [*] Creating config.ini ...
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
echo [OK] Server: %SERVER_URL%

:: --- Write START_AGENT.bat (always fresh, no encoding issues) ---
(
    echo @echo off
    echo title EDR Agent
    echo set AGENT_DIR=%%~dp0
    echo if "%%AGENT_DIR:~-1%%"=="\" set AGENT_DIR=%%AGENT_DIR:~0,-1%%
    echo echo ========================================
    echo echo  EDR Agent
    echo echo  Folder: %%AGENT_DIR%%
    echo echo ========================================
    echo echo.
    echo :loop
    echo echo [%%time%%] -- Starting agent --
    echo if exist "%%AGENT_DIR%%\data\agent.lock" del "%%AGENT_DIR%%\data\agent.lock" ^>nul 2^>^&1
    echo cd /d "%%AGENT_DIR%%"
    echo python -u agent.py
    echo set EXIT_CODE=%%errorlevel%%
    echo if %%EXIT_CODE%% equ 0 ^(
    echo     echo [%%time%%] Agent stopped.
    echo     pause
    echo     exit /b 0
    echo ^)
    echo if %%EXIT_CODE%% equ 99 ^(
    echo     echo [%%time%%] Update applied -- restarting...
    echo     timeout /t 2 /nobreak ^>nul
    echo     goto loop
    echo ^)
    echo echo [%%time%%] Error code %%EXIT_CODE%%. Restarting in 5s... ^(CTRL+C to quit^)
    echo timeout /t 5 /nobreak ^>nul
    echo goto loop
) > "C:\EDR\agent\START_AGENT.bat"
echo [OK] START_AGENT.bat created.
echo.

:: --- Install Python packages ---
echo [*] Installing Python packages (3-10 minutes)...
cd /d C:\EDR\agent
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo.
    echo [!] Some packages failed. Agent will still run.
    echo.
) else (
    echo [OK] All packages installed.
    echo.
)

:: --- Desktop shortcut ---
echo [*] Creating desktop shortcut...
(
    echo @echo off
    echo cd /d C:\EDR\agent
    echo call START_AGENT.bat
) > "%USERPROFILE%\Desktop\EDR Agent.bat"
echo [OK] Shortcut: Desktop\EDR Agent.bat
echo.

:: --- Test server connection ---
echo [*] Testing server connection...
python -c "import urllib.request; urllib.request.urlopen('%SERVER_URL%/health', timeout=5); print('[OK] Server reachable!')" 2>nul
if %errorLevel% neq 0 (
    echo [!] Server not reachable right now - that is OK.
    echo     Agent will connect automatically when server starts.
)
echo.

echo ==================================================
echo  DONE! Agent installed at C:\EDR\agent
echo  Shortcut: Desktop\EDR Agent.bat
echo ==================================================
echo.
echo  Starting agent now...
echo  Close this window to stop the agent.
echo.

cd /d C:\EDR\agent
call START_AGENT.bat
