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

$backend  = "Set-Location -LiteralPath '$PSScriptRoot'; `$env:PYTHONUTF8='1'; & '$vpy' server.py --host 127.0.0.1 --port $Port"
$frontend = "Set-Location -LiteralPath '$PSScriptRoot\frontend'; npm run dev"

Start-Process $shell -ArgumentList '-NoExit', '-Command', $backend
Start-Sleep -Seconds 3
Start-Process $shell -ArgumentList '-NoExit', '-Command', $frontend
Start-Sleep -Seconds 4

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

$url = 'http://localhost:5173'
if ($chrome) { Start-Process $chrome $url }
else { Write-Host "Open Chrome at $url (the microphone only works in Chrome)." -ForegroundColor Yellow }
Write-Host "JARVIS: $url   Dashboard: $url/dashboard.html"
