# JARVIS - Windows launcher. Opens the backend and the frontend in two
# windows and points Chrome at the orb.
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
param([int]$Port = 8340)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$vpy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { Write-Host 'Run .\install.ps1 first.' -ForegroundColor Red; exit 1 }

# UTF-8 everywhere: Windows' default code page mangles accents (and Spanish)
# in transcripts, memory files and the MCP pipe.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = 'powershell.exe' }

# Spanish dictation and voice unless the user already chose a language.
$fenv = Join-Path $PSScriptRoot 'frontend\.env'
if (-not (Test-Path $fenv)) { Set-Content -Path $fenv -Value 'VITE_JARVIS_LANG=es-VE' -Encoding ASCII }

$frontend = "Set-Location -LiteralPath '$PSScriptRoot\frontend'; npm run dev"

function Wait-Port([int]$p, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { Start-Sleep -Milliseconds 700 }
    }
    return $false
}

$log = Join-Path $PSScriptRoot 'jarvis-backend.log'
$backend = "Set-Location -LiteralPath '$PSScriptRoot'; `$env:PYTHONUTF8='1'; & '$vpy' server.py --host 127.0.0.1 --port $Port 2>&1 | Tee-Object -FilePath '$log'"

Write-Host 'Starting the JARVIS backend...' -ForegroundColor Cyan
Start-Process $shell -ArgumentList '-NoExit', '-Command', $backend
if (-not (Wait-Port $Port 90)) {
    Write-Host "The backend did not start. Look at its window, or send jarvis-backend.log to Claude." -ForegroundColor Red
    exit 1
}
Write-Host 'Starting the interface...' -ForegroundColor Cyan
Start-Process $shell -ArgumentList '-NoExit', '-Command', $frontend
if (-not (Wait-Port 5173 90)) {
    Write-Host "The interface (Vite) did not start. Look at its window." -ForegroundColor Red
    exit 1
}

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

$url = 'http://localhost:5173'
if ($chrome) { Start-Process $chrome $url }
else { Write-Host "Open Chrome at $url (the microphone only works in Chrome)." -ForegroundColor Yellow }
Write-Host "JARVIS: $url   Dashboard: $url/dashboard.html"
Write-Host 'Click once on the page, allow the microphone, and talk.' -ForegroundColor Green
