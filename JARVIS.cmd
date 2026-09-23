@echo off
rem Doble clic para arrancar JARVIS. Instala la primera vez, luego abre todo.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primera vez: instalando JARVIS...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if errorlevel 1 (
    echo.
    echo La instalacion fallo. Copia el texto de arriba y pegaselo a Claude.
    pause
    exit /b 1
  )
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
