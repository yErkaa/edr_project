Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  EDR SERVER  -  http://127.0.0.1:8088  " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$serverDir = "C:\Users\dinar\PycharmProjects\edr\server"
$uvicorn   = "C:\Users\dinar\PycharmProjects\edr\.venv\Scripts\uvicorn.exe"

Set-Location $serverDir

# Create logs directory if missing
New-Item -ItemType Directory -Path "$serverDir\logs" -Force | Out-Null

& $uvicorn api:app --host 0.0.0.0 --port 8088 --reload 2>&1 |
    Tee-Object -FilePath "$serverDir\logs\server.log" -Append