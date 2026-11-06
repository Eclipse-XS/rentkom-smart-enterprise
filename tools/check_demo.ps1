[CmdletBinding()]
param(
    [switch]$Detailed
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$hasCoreFailure = $false
$hasDegraded = $false
$externalGoogleReady = $false
$externalTunnelReady = $false
$externalTelegramReady = $false

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  RENTKOM SMART ENTERPRISE: COMPREHENSIVE SYSTEM AUDIT" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

function Test-HttpEndpoint {
    param([string]$Name, [string]$Url, [int]$TimeoutSec = 8)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Host "PASS $Name - HTTP $($response.StatusCode)" -ForegroundColor Green
            return $true
        }
        Write-Host "FAIL $Name - HTTP $($response.StatusCode)" -ForegroundColor Red
    } catch {
        Write-Host "FAIL $Name - $($_.Exception.Message)" -ForegroundColor Red
    }
    return $false
}

# --- 1. Infrastructure HTTP Endpoints ---
Write-Host "`n--- 1. Infrastructure HTTP Endpoints ---" -ForegroundColor Yellow
if (-not (Test-HttpEndpoint -Name "FastAPI Readiness" -Url "http://127.0.0.1:8000/api/v1/health/ready")) { $hasCoreFailure = $true }
if (-not (Test-HttpEndpoint -Name "FastAPI Swagger"   -Url "http://127.0.0.1:8000/docs")) { $hasCoreFailure = $true }
if (-not (Test-HttpEndpoint -Name "n8n Readiness"     -Url "http://127.0.0.1:5678/healthz/readiness")) { $hasCoreFailure = $true }
if (-not (Test-HttpEndpoint -Name "Qdrant Readiness"  -Url "http://127.0.0.1:6333/healthz")) { $hasCoreFailure = $true }
if (-not (Test-HttpEndpoint -Name "Grafana Health"    -Url "http://127.0.0.1:3000/api/health")) { $hasCoreFailure = $true }

$ollamaOk = Test-HttpEndpoint -Name "Host Ollama API"   -Url "http://127.0.0.1:11434/api/tags"
if (-not $ollamaOk) { $hasDegraded = $true }

# --- 2. Docker Container States ---
Write-Host "`n--- 2. Docker Container States ---" -ForegroundColor Yellow
$expectedContainers = @("rentkom-postgres", "rentkom-business-api", "rentkom-n8n", "rentkom-grafana", "rentkom-qdrant")
foreach ($cName in $expectedContainers) {
    $state = docker inspect --format '{{.State.Status}}' $cName 2>$null
    if ($LASTEXITCODE -eq 0 -and $state -eq "running") {
        Write-Host "PASS Container $cName - running" -ForegroundColor Green
    } else {
        Write-Host "FAIL Container $cName - not running ($state)" -ForegroundColor Red
        $hasCoreFailure = $true
    }
}

# --- 3. n8n Workflow Activation Audit ---
Write-Host "`n--- 3. n8n Workflow Activation Audit ---" -ForegroundColor Yellow
$activeWfs = docker exec rentkom-n8n n8n list:workflow --active=true 2>$null
$requiredPublished = @(
    @{ Id = "RentkomTelegramUnified01"; Name = "Rentkom Unified Telegram Router" },
    @{ Id = "gaPyYUSgGKVK2vKc"; Name = "Customer Feedback Ingestion & Alert" },
    @{ Id = "LAB4RealEstate01"; Name = "Forms to PostgreSQL and Gmail" }
)

foreach ($item in $requiredPublished) {
    if ($activeWfs -match $item.Id) {
        Write-Host "PASS Required workflow published: $($item.Name) ($($item.Id))" -ForegroundColor Green
    } else {
        Write-Host "FAIL Required workflow NOT published: $($item.Name) ($($item.Id))" -ForegroundColor Red
        $hasCoreFailure = $true
    }
}

$forbiddenPublished = @(
    @{ Id = "i4IMaq12Rl7G6xwy"; Name = "Legacy Telegram Rule Bot" },
    @{ Id = "LAB6TelegramRAG1"; Name = "Legacy Standalone RAG Bot" }
)

foreach ($item in $forbiddenPublished) {
    if ($activeWfs -match $item.Id) {
        Write-Host "FAIL Conflicting workflow IS ACTIVE: $($item.Name) ($($item.Id))" -ForegroundColor Red
        $hasCoreFailure = $true
    } else {
        Write-Host "PASS Conflicting workflow safely inactive: $($item.Name)" -ForegroundColor Green
    }
}

# --- 4. Qdrant Knowledge Base & Vectors ---
Write-Host "`n--- 4. Qdrant Knowledge Base & Vectors ---" -ForegroundColor Yellow
try {
    $qdrantInfo = Invoke-RestMethod -Uri "http://127.0.0.1:6333/collections/rag_real_estate_active" -TimeoutSec 5
    $pointsCount = $qdrantInfo.result.points_count
    if ($pointsCount -ge 30) {
        Write-Host "PASS Qdrant collection 'rag_real_estate_active' ready with $pointsCount vector points" -ForegroundColor Green
    } else {
        Write-Host "WARN Qdrant collection 'rag_real_estate_active' has only $pointsCount points" -ForegroundColor Yellow
        $hasDegraded = $true
    }
} catch {
    Write-Host "FAIL Qdrant collection query failed: $($_.Exception.Message)" -ForegroundColor Red
    $hasDegraded = $true
}

# --- 5. External Integrations Audit ---
Write-Host "`n--- 5. External Integrations Audit ---" -ForegroundColor Yellow
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) { $PythonExe = "python" }

$auditJsonRaw = & $PythonExe "$ProjectRoot\tools\audit_integrations.py" 2>&1
$auditData = $null
try {
    $auditData = $auditJsonRaw | ConvertFrom-Json
} catch {
    Write-Host "WARN Could not parse audit JSON: $auditJsonRaw" -ForegroundColor Yellow
}

if ($auditData) {
    if ($auditData.google.status -eq "READY") {
        Write-Host "PASS Google OAuth2: $($auditData.google.message)" -ForegroundColor Green
        $externalGoogleReady = $true
    } else {
        Write-Host "WARN Google OAuth2: $($auditData.google.message)" -ForegroundColor Yellow
    }

    if ($auditData.tunnel.status -eq "READY") {
        Write-Host "PASS Cloudflare Tunnel: $($auditData.tunnel.message)" -ForegroundColor Green
        $externalTunnelReady = $true
    } else {
        Write-Host "INFO Cloudflare Tunnel: $($auditData.tunnel.message)" -ForegroundColor DarkGray
    }

    if ($auditData.telegram.status -eq "READY") {
        Write-Host "PASS Telegram Bot API: $($auditData.telegram.message)" -ForegroundColor Green
        $externalTelegramReady = $true
    } elseif ($auditData.telegram.status -eq "DEGRADED") {
        Write-Host "WARN Telegram Bot API: $($auditData.telegram.message)" -ForegroundColor Yellow
    } else {
        Write-Host "INFO Telegram Bot API: $($auditData.telegram.message)" -ForegroundColor DarkGray
    }
}

# --- 6. Telegram Operating Mode Audit ---
Write-Host "`n--- 6. Telegram Operating Mode Audit ---" -ForegroundColor Yellow
try {
    $modeResp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/telegram/mode" -TimeoutSec 5
    Write-Host "PASS Active Telegram Bot Mode: $($modeResp.mode)" -ForegroundColor Green
    Write-Host "     Source: $($modeResp.source) (Updated at: $($modeResp.updated_at))" -ForegroundColor Cyan
} catch {
    Write-Host "WARN Could not retrieve active Telegram mode: $($_.Exception.Message)" -ForegroundColor Yellow
    $hasDegraded = $true
}

# --- 6. Overall System Classification ---
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  RENTKOM SYSTEM CLASSIFICATION & VERDICT" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if ($hasCoreFailure) {
    Write-Host "`nVERDICT: [FAILED]" -ForegroundColor Red
    Write-Host "Explanation: One or more core infrastructure services, containers, or mandatory workflows failed." -ForegroundColor Red
    exit 1
}

if ($hasDegraded) {
    Write-Host "`nVERDICT: [DEGRADED]" -ForegroundColor Yellow
    Write-Host "Explanation: Core stack is operational, but AI/RAG models or Qdrant vector index are degraded." -ForegroundColor Yellow
    exit 2
}

if ($externalGoogleReady -and $externalTunnelReady -and $externalTelegramReady) {
    Write-Host "`nVERDICT: [EXTERNAL_READY]" -ForegroundColor Green
    Write-Host "Explanation: All local microservices, AI models, and real external integrations (Google Sheets, Gmail OAuth, Cloudflare Tunnel, Telegram Webhook) are fully operational." -ForegroundColor Green
    exit 0
}

Write-Host "`nVERDICT: [LOCAL_READY]" -ForegroundColor Cyan
Write-Host "Explanation: All local microservices, synthetic event webhooks, and AI models are fully operational. External integrations are either offline or awaiting tunnel/credentials." -ForegroundColor Cyan
exit 0
