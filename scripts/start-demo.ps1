$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv, Python 3.12, Node.js and Docker Desktop first; see PROJECT_GUIDE.md.' }
if (-not (Test-Path -LiteralPath '.env')) {
    $demoPassword = [Guid]::NewGuid().ToString('N')
    (Get-Content '.env.example') -replace 'change-me',$demoPassword | Set-Content '.env'
}
$runtimeRoot = Join-Path $projectRoot '.dbdata\demo'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$env:UV_CACHE_DIR = Join-Path $runtimeRoot 'uv-cache'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { uv venv --python 3.12 .venv; if ($LASTEXITCODE) { throw 'Virtual environment setup failed' } }
uv pip install --python .venv\Scripts\python.exe -r requirements.lock
if ($LASTEXITCODE) { throw 'Python dependency installation failed' }
npm.cmd --prefix frontend ci
if ($LASTEXITCODE) { throw 'Frontend dependency installation failed' }
docker compose up -d --wait
if ($LASTEXITCODE) { throw 'Start Docker Desktop and try again' }
.venv\Scripts\python.exe scripts\seed_demo.py
if ($LASTEXITCODE) { throw 'Synthetic data setup failed' }
$env:POSTGRES_DB = 'connectome_demo'
$env:MONGO_DB = 'connectome_meta_demo'
$env:HOST = '127.0.0.1'
$env:PORT = '3000'
$env:API_URL = 'http://127.0.0.1:8000'
foreach ($demoPort in 8000,3000) {
    $portCheck = New-Object Net.Sockets.TcpClient
    try { $portCheck.Connect('127.0.0.1',$demoPort); throw "Port $demoPort is already in use. Stop that server before launching the demo." }
    catch [Net.Sockets.SocketException] { }
    finally { $portCheck.Dispose() }
}
$apiProcess = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -ArgumentList '-m uvicorn backend.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'api.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'api.stderr.log')
$frontendProcess = Start-Process -FilePath (Get-Command node).Source -ArgumentList 'server.js' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'frontend.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'frontend.stderr.log')
@{api=$apiProcess.Id;frontend=$frontendProcess.Id} | ConvertTo-Json | Set-Content (Join-Path $runtimeRoot 'processes.json')
$demoReady = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $demoHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/v1/health' -TimeoutSec 2
        if ($demoHealth.status -eq 'ok') { $demoReady = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $demoReady) { throw 'Demo servers did not become ready. Inspect .dbdata/demo/api.stderr.log and frontend.stderr.log.' }
Write-Output 'Synthetic demo started at http://127.0.0.1:3000. Logs and process IDs: .dbdata/demo/'
