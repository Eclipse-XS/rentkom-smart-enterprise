[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  RENTKOM SMART ENTERPRISE: STOPPING APPLICATION STACK" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Stop Cloudflare Tunnel supervisor & cloudflared processes
Write-Host "[1/2] Stopping Cloudflare Tunnel & Webhook Supervisor..." -ForegroundColor Yellow
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) { $PythonExe = "python" }

try {
    & $PythonExe "$ProjectRoot\tools\manage_tunnel.py" --action stop
    Write-Host "PASS Cloudflare Tunnel and supervisor stopped." -ForegroundColor Green
} catch {
    Write-Host "WARN Could not stop tunnel cleanly: $_" -ForegroundColor Yellow
}

# 2. Stop Docker Compose containers gracefully
Write-Host "`n[2/2] Stopping Docker Containers (Preserving Volumes & Data)..." -ForegroundColor Yellow
try {
    & $PythonExe "$ProjectRoot\tools\notify_telegram_lifecycle.py" shutdown
} catch {
    Write-Host "WARN Shutdown notification was not delivered." -ForegroundColor Yellow
}
docker compose --profile connected stop n8n grafana business-api qdrant postgres
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL Failed to stop one or more demo containers." -ForegroundColor Red
    exit 1
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "DEMO STACK STOPPED" -ForegroundColor Green
Write-Host "Persistent PostgreSQL, n8n, Qdrant, and Grafana volumes were preserved." -ForegroundColor Green
Write-Host "Host Ollama was not stopped or modified." -ForegroundColor Green
Write-Host "============================================================`n"
