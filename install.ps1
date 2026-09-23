# JARVIS - Windows installer. Run from the repo folder:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

Step 'Checking prerequisites'
$py = $null
foreach ($cand in 'py -3.12', 'py -3.11', 'py -3', 'python') {
    $parts = [string[]]($cand -split ' ')
    $exe = $parts[0]; $pre = @($parts | Select-Object -Skip 1)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try { $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $v = $null }
        if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]'3.11') { $py = $parts; break }
    }
}
if (-not $py) { Fail 'Python 3.11+ not found. Install it from python.org (tick "Add to PATH").' }
$pyExe = $py[0]; $pyPre = @($py | Select-Object -Skip 1)
Write-Host "  Python: $($py -join ' ')"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail 'Node.js 18+ not found. Install the LTS from nodejs.org.' }
Write-Host "  Node:   $(node --version)"

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host '  Claude Code not found - installing it with npm...' -ForegroundColor Yellow
    npm install -g @anthropic-ai/claude-code
}
Write-Host "  Claude: $(claude --version)"

Step 'Creating the Python virtual environment (.venv)'
if (-not (Test-Path .venv)) {
    & $pyExe @pyPre -m venv .venv
}
$vpy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install -r requirements-windows.txt
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }

Step 'Installing the Playwright browser (read_page / look_at_page)'
& $vpy -m playwright install chromium

Step 'Installing the frontend'
Push-Location frontend
npm install
Pop-Location

Step 'Certificates (required: the Vite proxy talks HTTPS to the backend)'
& $vpy scripts\make_cert.py

Step '.env'
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host '  Created .env - put your FISH_API_KEY in it.' -ForegroundColor Yellow
} else { Write-Host '  .env already exists - left as is.' }

Step 'Voice language'
$fenv = Join-Path $PSScriptRoot 'frontend\.env'
if (-not (Test-Path $fenv)) {
    Set-Content -Path $fenv -Value 'VITE_JARVIS_LANG=es-VE' -Encoding ASCII
    Write-Host '  Dictation and voice set to Spanish (es-VE). Change it in frontend\.env.'
}

Step 'Claude Code login'
Write-Host '  If you have never logged in, run:  claude   (then /login) and close it.'
Write-Host '  JARVIS runs on your Claude subscription - no API key needed.'

Write-Host "`nDone. Start JARVIS with:  .\start.ps1" -ForegroundColor Green
