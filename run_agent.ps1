Write-Host "========================================" -ForegroundColor Green
Write-Host "  EDR AGENT  -  file monitoring         " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

$agentDir = "C:\Users\dinar\PycharmProjects\edr\agent"
$python   = "C:\Users\dinar\PycharmProjects\edr\.venv\Scripts\python.exe"

# Create logs directory if missing
New-Item -ItemType Directory -Path "$agentDir\logs" -Force | Out-Null

# Run agent from its own directory so local imports work
Set-Location $agentDir

$attempt = 0
while ($true) {
    $attempt++
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting agent (attempt $attempt)..." -ForegroundColor Yellow
    & $python -u agent.py
    $exitCode = $LASTEXITCODE
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Agent exited (code $exitCode). Restarting in 5s..." -ForegroundColor Red
    Start-Sleep -Seconds 5
}