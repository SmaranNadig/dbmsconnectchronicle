# Start the configured Windows public-data workspace.
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)]
    [int]$DashboardPort = 3000
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $project 'frontend'
$logs = Join-Path $project '.dbdata\public'
$python = Join-Path $project '.venv\Scripts\python.exe'
$apiPort = 8200
$api = "http://127.0.0.1:$apiPort"
$site = "http://127.0.0.1:$DashboardPort"

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([IO.Path]::GetFileName($Executable)) $($Arguments[0]) failed with exit code $LASTEXITCODE."
    }
}

function Test-LinuxEngine {
    try {
        $engine = & $docker info --format '{{.OSType}}' 2>$null
        return ($LASTEXITCODE -eq 0 -and $engine -eq 'linux')
    } catch {
        return $false
    }
}

function Test-Listening([int]$Port) {
    $socket = New-Object Net.Sockets.TcpClient
    try {
        $socket.Connect('127.0.0.1', $Port)
        return $true
    } catch [Net.Sockets.SocketException] {
        return $false
    } finally {
        $socket.Dispose()
    }
}

function Wait-Health([string]$Address, [string]$LogName) {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        try {
            $health = Invoke-RestMethod -Uri "$Address/api/v1/health" -UseBasicParsing -TimeoutSec 3
            if ($health.status -eq 'ok') { return $health }
        } catch {
            # The process or PostgreSQL connection pool may still be starting.
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "No healthy project service at $Address. Check that port and $logs\$LogName."
}

function Read-Site([string]$Path) {
    Invoke-RestMethod -Uri "$site/api/v1/$Path" -UseBasicParsing -TimeoutSec 45
}

Push-Location -LiteralPath $project
$startupLock = $null
try {
    if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
        throw 'Create .env from .env.example with settings for the public database. See PROJECT_GUIDE.md.'
    }
    if ($DashboardPort -eq $apiPort) { throw 'Choose a dashboard port other than 8200.' }

    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $lockPath = Join-Path $logs 'startup.lock'
    $lockDeadline = (Get-Date).AddSeconds(120)
    do {
        try {
            $startupLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
        } catch [IO.IOException] {
            if ((Get-Date) -ge $lockDeadline) { throw "Could not acquire the startup lock at $lockPath." }
            Start-Sleep -Seconds 1
        }
    } until ($startupLock)

    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    $docker = if ($dockerCommand) { $dockerCommand.Source } else {
        @(
            "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
            "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $docker) { throw 'Install Docker Desktop to start the local project databases.' }
    if (-not (Test-LinuxEngine)) {
        Write-Host 'Starting Docker Desktop and waiting for its Linux engine...'
        Invoke-Checked $docker @('desktop', 'start')
        $deadline = (Get-Date).AddSeconds(90)
        while (-not (Test-LinuxEngine)) {
            if ((Get-Date) -ge $deadline) { throw 'The Docker Linux engine did not become ready.' }
            Start-Sleep -Seconds 2
        }
    }

    Write-Host 'Starting PostgreSQL, three shards, and MongoDB...'
    Invoke-Checked $docker @('compose', 'up', '-d', '--wait', '--wait-timeout', '120')

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $uv = (Get-Command uv.exe).Source
        Invoke-Checked $uv @('venv', '--python', '3.12', '.venv')
    }
    $pythonCheck = 'import alembic, fastapi, uvicorn, psycopg, psycopg_pool, pymongo, pandas, scipy, sklearn, igraph, pyarrow'
    try { & $python -c $pythonCheck *> $null; $dependenciesReady = $LASTEXITCODE -eq 0 }
    catch { $dependenciesReady = $false }
    if (-not $dependenciesReady) {
        $uv = (Get-Command uv.exe).Source
        Invoke-Checked $uv @('--cache-dir', (Join-Path $logs 'uv-cache'), 'pip', 'install', '--python', $python,
            '--require-hashes', '-r', 'requirements.lock')
    }

    $node = (Get-Command node.exe).Source
    Push-Location -LiteralPath $frontend
    try {
        try { & $node -e "require('express'); require('http-proxy-middleware')" *> $null; $nodeReady = $LASTEXITCODE -eq 0 }
        catch { $nodeReady = $false }
        if (-not $nodeReady) { Invoke-Checked (Get-Command npm.cmd).Source @('ci') }
    } finally {
        Pop-Location
    }

    Write-Host 'Applying the database migrations...'
    Invoke-Checked $python @('-m', 'alembic', 'upgrade', 'head')
    $configCode = @'
import json
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend.config import get_settings, load_catalog

print(json.dumps(dict(
    database=get_settings().postgres_db,
    revision=ScriptDirectory.from_config(Config('alembic.ini')).get_current_head(),
    releases=[f'{dataset}/{version}' for dataset, item in load_catalog().items()
              if item.get('adapter') for version in item['releases']]
)))
'@
    $publicConfig = Invoke-Checked $python @('-c', $configCode) | ConvertFrom-Json

    if (-not (Test-Listening $apiPort)) {
        Write-Host "Starting the API at $api..."
        Start-Process -FilePath $python -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port $apiPort" `
            -WorkingDirectory $project -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logs 'api.stdout.log') `
            -RedirectStandardError (Join-Path $logs 'api.stderr.log')
    }
    $apiHealth = Wait-Health $api 'api.stderr.log'
    if ($apiHealth.schema_revision -ne $publicConfig.revision) {
        throw "The API at $api is serving a different schema revision. Check the process using port $apiPort."
    }

    if (-not (Test-Listening $DashboardPort)) {
        Write-Host "Starting the dashboard at $site..."
        $priorHost = $env:HOST
        $priorPort = $env:PORT
        $priorApi = $env:API_URL
        try {
            $env:HOST = '127.0.0.1'
            $env:PORT = [string]$DashboardPort
            $env:API_URL = $api
            Start-Process -FilePath $node -ArgumentList 'server.js' -WorkingDirectory $frontend -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $logs "dashboard-$DashboardPort.stdout.log") `
                -RedirectStandardError (Join-Path $logs "dashboard-$DashboardPort.stderr.log")
        } finally {
            $env:HOST = $priorHost
            $env:PORT = $priorPort
            $env:API_URL = $priorApi
        }
    }

    $siteHealth = Wait-Health $site "dashboard-$DashboardPort.stderr.log"
    $overview = Read-Site 'overview'
    if ($siteHealth.schema_revision -ne $publicConfig.revision -or
        $overview.database -ne $publicConfig.database -or $overview.data_mode -notin @('public', 'empty')) {
        throw "The website at $site is not serving the configured public data. Check the port and setup in PROJECT_GUIDE.md."
    }

    $loaded = @($overview.releases | ForEach-Object { "$($_.dataset)/$($_.version_label)" })
    $missing = @($publicConfig.releases | Where-Object { $_ -notin $loaded })
    if ($missing.Count) {
        throw "Load the missing public releases using README.md: $($missing -join ', ')."
    }
    $synced = @($overview.metadata_syncs | Where-Object { $_.documents_written -gt 0 } |
        ForEach-Object { "$($_.dataset)/$($_.version_label)" })
    $missingSyncs = @($publicConfig.releases | Where-Object { $_ -notin $synced })
    $datasetCodes = @($overview.datasets | ForEach-Object { $_.code })
    $missingDocuments = @($datasetCodes | Where-Object { -not $overview.mongo_documents.$_ })
    if ($overview.mongo_documents.unavailable -or $missingSyncs.Count -or $missingDocuments.Count) {
        throw 'MongoDB source records are unavailable or incomplete. Check metadata sync in README.md.'
    }

    $distribution = Read-Site 'distribution'
    foreach ($strategy in 'hash', 'anatomical', 'community') {
        $placement = $distribution.deployments.$strategy
        if (-not $placement.verified -or -not $placement.fresh -or @($placement.shards).Count -ne 3) {
            throw "The $strategy shard deployment needs preparation. See the Distributed storage section of README.md."
        }
    }

    $visualRuns = Read-Site 'circuit/runs'
    $memoryRuns = Read-Site 'memory/runs'
    if (-not $visualRuns -or -not $memoryRuns) {
        throw 'Prepare and run both labs once using docs/CIRCUIT_LAB.md and docs/MEMORY_LAB.md.'
    }

    Write-Host "Public-data dashboard ready at $site"
    Write-Host "$($loaded.Count) releases, MongoDB source records, three shard strategies, and both labs verified."
    Write-Host "API docs at $api/docs"
} finally {
    if ($startupLock) { $startupLock.Dispose() }
    Pop-Location
}
