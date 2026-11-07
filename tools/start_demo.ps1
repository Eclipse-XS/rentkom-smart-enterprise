[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 180,
    [switch]$SkipSmokeTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  RENTKOM SMART ENTERPRISE: ONE-COMMAND STARTUP & ORCHESTRATION" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Locate project root & environment
Write-Host "[1/6] Validating Project Root & Environment..." -ForegroundColor Yellow
if (-not (Test-Path "$ProjectRoot\compose.yaml")) {
    throw "Project root missing compose.yaml: $ProjectRoot"
}

# 2. Validate Docker Desktop availability
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not available or not running."
}
Write-Host "PASS Docker Desktop daemon is active." -ForegroundColor Green

# 3. Validate required environment variables without printing secrets
$EnvFile = "$ProjectRoot\.env"
if (-not (Test-Path $EnvFile)) {
    throw "Missing required .env file at $EnvFile. Copy from .env.example first."
}

$RequiredEnvKeys = @(
    "INTERNAL_API_KEY",
    "TEST_WEBHOOK_SECRET",
    "TELEGRAM_SECRET_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "RENTKOM_APP_PASSWORD"
)

$EnvContent = Get-Content $EnvFile
foreach ($key in $RequiredEnvKeys) {
    $match = $EnvContent | Where-Object { $_ -match "^\s*$key\s*=\s*(.+)" }
    if (-not $match) {
        throw "Missing required configuration variable '$key' in .env."
    }
}
Write-Host "PASS Required environment variables present (secrets redacted)." -ForegroundColor Green

# Project-specific scripts are tracked; Docker, Python, cloudflared and Ollama are external prerequisites.
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw "Python is not available. Install Python 3.13 or create .venv." }
    $PythonExe = $pythonCommand.Source
}
$cloudflaredCandidates = @(
    (Get-Command cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$sshCommand = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $cloudflaredCandidates -and -not $sshCommand) {
    throw "Neither cloudflared nor OpenSSH is available; no supported HTTPS tunnel can start."
}
if ($cloudflaredCandidates) { Write-Host "PASS cloudflared primary tunnel client is available." -ForegroundColor Green }
if ($sshCommand) { Write-Host "PASS OpenSSH Ethernet fallback is available." -ForegroundColor Green }
Write-Host "PASS Python and HTTPS tunnel prerequisites are available." -ForegroundColor Green

# 4. Validate Host Ollama
try {
    $ollama = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    Write-Host "PASS Host Ollama is accessible at http://127.0.0.1:11434." -ForegroundColor Green
} catch {
    throw "Host Ollama is not accessible on http://127.0.0.1:11434. Please start Ollama before starting Rentkom."
}

# 5. Build only FastAPI so tracked Python changes are loaded, then start services without rebuilding unrelated images.
Write-Host "`n[2/6] Starting Core Docker Containers (Connected Profile)..." -ForegroundColor Yellow
docker compose --profile connected build business-api
if ($LASTEXITCODE -ne 0) { throw "FastAPI image build failed." }
docker compose --profile connected up -d --no-build postgres business-api qdrant grafana n8n
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

# Helper for readiness polling
function Wait-HttpReady {
    param([string]$Name, [string]$Url, [int]$Timeout)
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "PASS $Name is ready ($Url)" -ForegroundColor Green
                return
            }
        } catch {
            Start-Sleep -Seconds 3
        }
    } while ((Get-Date) -lt $deadline)
    throw "FAIL $Name did not become ready within $Timeout seconds: $Url"
}

# 6. Verify Service Readiness
Write-Host "`n[3/6] Verifying Service Readiness..." -ForegroundColor Yellow

# Wait for PostgreSQL container health
$pgDeadline = (Get-Date).AddSeconds(30)
$pgReady = $false
do {
    $pgHealth = docker inspect --format '{{.State.Health.Status}}' rentkom-postgres 2>$null
    if ($pgHealth -eq "healthy") {
        Write-Host "PASS PostgreSQL container is healthy (Port 5433 -> 5432)" -ForegroundColor Green
        $pgReady = $true
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $pgDeadline)
if (-not $pgReady) { throw "PostgreSQL did not become healthy within 30 seconds." }

Wait-HttpReady -Name "FastAPI Business Core" -Url "http://127.0.0.1:8000/api/v1/health/ready" -Timeout $TimeoutSeconds
Wait-HttpReady -Name "n8n Automation Engine" -Url "http://127.0.0.1:5678/healthz/readiness" -Timeout $TimeoutSeconds
Wait-HttpReady -Name "Qdrant Vector DB"      -Url "http://127.0.0.1:6333/healthz" -Timeout $TimeoutSeconds
Wait-HttpReady -Name "Grafana Observability" -Url "http://127.0.0.1:3000/api/health" -Timeout $TimeoutSeconds

# 7. Reconcile Automation & Workflow Publication States
Write-Host "`n[4/6] Reconciling Automation Workflows & Credentials from Manifest..." -ForegroundColor Yellow
& $PythonExe "$ProjectRoot\tools\reconcile_automation.py"
if ($LASTEXITCODE -ne 0) {
    New-Item -ItemType Directory -Force -Path "$ProjectRoot\runtime" | Out-Null
    Write-Host "WARNING: Automation reconciliation reported issues (check above output)." -ForegroundColor Yellow
} else {
    Write-Host "PASS Automation manifests reconciled and verified." -ForegroundColor Green
}

# 8. Manage External HTTPS Tunnel & Telegram Webhook
Write-Host "`n[5/6] Managing External HTTPS Tunnel & Telegram Webhook..." -ForegroundColor Yellow
$tunnelStatus = & $PythonExe "$ProjectRoot\tools\manage_tunnel.py" --action status 2>&1
if ($LASTEXITCODE -ne 0) {
    # Always launch a candidate. The manager's OS lock, not a reusable PID, decides
    # ownership. A second candidate exits without starting another cloudflared.
    Write-Host "Starting or joining the HTTPS tunnel supervisor..." -ForegroundColor Cyan
    $supLog = "$ProjectRoot\runtime\supervisor.log"
    $supErr = "$ProjectRoot\runtime\supervisor_err.log"
    Start-Process -FilePath $PythonExe -ArgumentList @("""$ProjectRoot\tools\manage_tunnel.py""", "--action", "daemon") -RedirectStandardOutput $supLog -RedirectStandardError $supErr -WindowStyle Hidden
    # Match the bounded command timeout; a Quick Tunnel handshake can exceed 30s.
    $tunnelDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $tunnelReady = $false
    do {
        Start-Sleep -Seconds 2
        $tunnelStatus = & $PythonExe "$ProjectRoot\tools\manage_tunnel.py" --action status 2>&1
        if ($LASTEXITCODE -eq 0) {
            $tunnelReady = $true
            break
        }
    } while ((Get-Date) -lt $tunnelDeadline)
    if (-not $tunnelReady) {
        $failureDetail = ""
        if (Test-Path "$ProjectRoot\runtime\supervisor_err.log") {
            $failureDetail = (Get-Content "$ProjectRoot\runtime\supervisor_err.log" -Tail 3) -join " "
        }
        Write-Host "Tunnel recovery failed after $TimeoutSeconds seconds. $failureDetail" -ForegroundColor Red
        Write-Host "Inspect runtime\supervisor_err.log and runtime\cloudflared.log." -ForegroundColor Red
        throw "External HTTPS tunnel or Telegram webhook is NOT READY."
    }
}
if ($LASTEXITCODE -eq 0) {
    Write-Host "PASS External HTTPS Tunnel & Telegram Webhook active:" -ForegroundColor Green
    $tunnelStatus | ForEach-Object { Write-Host "     $_" -ForegroundColor Cyan }
}

# 9. Comprehensive System Audit
Write-Host "`n[6/6] Executing Comprehensive System Audit..." -ForegroundColor Yellow
if (-not $SkipSmokeTests) {
    & "$ProjectRoot\tools\check_demo.ps1"
    if ($LASTEXITCODE -ne 0) { throw "System audit failed with exit code $LASTEXITCODE. STATUS: NOT READY" }
} else {
    Write-Host "Skipping audit checks per -SkipSmokeTests flag." -ForegroundColor DarkGray
}

# Read tunnel URL if available
$publicTunnelUrl = $null
$tunnelStateFile = "$ProjectRoot\runtime\tunnel_state.json"
if (Test-Path $tunnelStateFile) {
    try {
        $tState = Get-Content $tunnelStateFile -Raw | ConvertFrom-Json
        $publicTunnelUrl = $tState.url
    } catch {}
}

Write-Host "`nRENTKOM STARTUP"
Write-Host ""
Write-Host "Docker:       OK"
Write-Host "FastAPI:      OK"
Write-Host "n8n:          OK"
Write-Host "PostgreSQL:   OK"
Write-Host "Qdrant:       OK"
Write-Host "Ollama:       OK"
Write-Host "Tunnel:       OK"
Write-Host "Telegram:     WEBHOOK CONFIGURED"
try {
    $modeResp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/telegram/mode" -TimeoutSec 3
    Write-Host "Active Telegram Mode:   $($modeResp.mode) (Switch via .\tools\set_telegram_mode.ps1 -Mode LR2|LR6)" -ForegroundColor Magenta
} catch {}
Write-Host ""
Write-Host "STATUS: READY" -ForegroundColor Green
try {
    & $PythonExe "$ProjectRoot\tools\notify_telegram_lifecycle.py" startup
} catch {
    Write-Host "WARNING: Startup notification was not delivered." -ForegroundColor Yellow
}
