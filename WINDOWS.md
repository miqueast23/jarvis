# JARVIS en Windows

Port a Windows de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (commit `16e37bd`, 7 sept 2026).
Trae la versión nueva del video: el cerebro corre sobre tu plan de Claude Code (sin API), memoria persistente en Markdown, dashboard con Runs / Sessions / Memory / Specs / Projects / Usage, y el flujo de construcción spec → plan → ejecución.

## Instalación (una sola vez)

Necesitas **Python 3.11+**, **Node.js 18+**, **Google Chrome** y una API key de **Fish Audio** (la voz; es lo único que se paga aparte).

```powershell
cd C:\ruta\a\jarvis
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

El instalador crea `.venv`, instala dependencias (incluye `mss`, `Pillow` y `cryptography` para Windows), instala Chromium para Playwright, instala el frontend, genera `cert.pem`/`key.pem` **sin openssl** y copia `.env`.

Después:

1. Abre `.env` y pon `FISH_API_KEY=...` (opcional: `USER_NAME=Os`).
2. Si nunca iniciaste sesión en Claude Code: ejecuta `claude`, haz `/login` y ciérralo.
3. **No** dejes `ANTHROPIC_API_KEY` en el entorno: JARVIS la elimina de todos modos para cobrar siempre contra tu suscripción.

## Arranque

```powershell
.\start.ps1
```

Abre dos ventanas (backend en `https://127.0.0.1:8340` y Vite en `http://localhost:5173`) y lanza Chrome. Haz clic una vez en la página para habilitar audio y habla. Dashboard: `http://localhost:5173/dashboard.html`.

**Dictado en español (opcional):** crea `frontend\.env` con `VITE_JARVIS_LANG=es-VE` (o `es-ES`) y reinicia. Por defecto es `en-US`. La voz de Fish Audio es británica; puedes cambiarla con `FISH_VOICE_ID`.

## Qué cambia respecto a macOS

| Función | macOS (original) | Windows (este port) |
|---|---|---|
| Abrir terminal en un proyecto | Terminal.app vía AppleScript | Nueva consola PowerShell (se abre en Windows Terminal si es tu terminal predeterminada), con la carpeta como directorio de trabajo |
| Notificación "una sesión te necesita" | Centro de notificaciones | Notificación toast de Windows |
| Responder a un permiso de Claude Code (Enter / Esc / 1-9) | Tecla en la pestaña de Terminal.app dueña del tty | Tecla escrita en el búfer de entrada de la **consola del propio proceso** `claude` (`win_console_keys.py`). Mismo vocabulario cerrado y re-verificación antes de pulsar; nunca escribe en la ventana que tengas enfrente |
| Mirar la pantalla / ventanas abiertas | `screencapture` + AppleScript | `mss` + Pillow / EnumWindows. No hace falta conceder permisos |
| Abrir en editor | VS Code o `open` | VS Code (`Code.exe`); si no está, Explorador para carpetas y Bloc de notas para archivos (nunca "ejecutar" un .bat/.ps1 por doble clic) |
| Enviar mensajes a otra sesión | Socket Unix | Named pipe (`\\.\pipe\...`) |
| Escritorio | `~/Desktop` | Escritorio real, incluido el redirigido a OneDrive |

Correcciones importantes debajo del capó:

- **`os.kill(pid, 0)` en Windows mata el proceso** (no es una comprobación). El vigilante de sesiones lo hacía cada segundo: en Windows habría cerrado todas tus sesiones de Claude Code. Ahora usa `OpenProcess`/`GetExitCodeProcess`.
- `claude.cmd` (instalación npm) se resuelve a `node …\cli.js` (o al `claude.exe` incluido) para que cmd.exe no corte el prompt del sistema en el primer salto de línea ni reinterprete `&`, `|`, `%`.
- Las rutas `C:\…` ya no pasan por `shlex` POSIX, que se comía las barras invertidas.
- Cancelar un run mata el árbol completo (`taskkill /T`), incluidos los servidores MCP hijos.
- UTF-8 forzado (`PYTHONUTF8=1`) para que tildes y ñ no se corrompan en transcripciones, memoria y el canal MCP.
- `JARVIS_PROJECT_ROOTS` se separa con `;` en Windows (el `:` choca con `C:`).
- El proxy de Vite apunta a `127.0.0.1` en lugar de `localhost` (en Windows `localhost` suele resolver primero a `::1` y el backend escucha en IPv4).

## Conectar tu propio Jarvis / Timo (Obsidian)

JARVIS solo ve los servidores MCP que declares en `data\jarvis\connections.json`. Para que lea y escriba en tu bóveda de Obsidian:

```json
{
  "mcpServers": {
    "timo": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\TU_USUARIO\\Documents\\Obsidian\\Timo"]
    }
  }
}
```

Reinicia y pregúntale *"what are you connected to?"*. Su memoria propia sigue en `data\jarvis\memory\` (un archivo Markdown por hecho), así que también puedes enlazarla desde la bóveda.

## Aplicarlo sobre tu fork existente

Si ya tienes un fork adaptado, en vez de reemplazar la carpeta:

```powershell
git fetch https://github.com/ethanplusai/jarvis main
git merge FETCH_HEAD          # trae la versión nueva del upstream
git apply --3way jarvis-windows.patch
```

Resuelve los conflictos donde tu fork ya tenía cambios propios para Windows; los módulos nuevos (`winplat.py`, `win_console_keys.py`, `install.ps1`, `start.ps1`) no chocan con nada.

## Límites conocidos

- `get_chrome_tab_info` (leer la pestaña activa de Chrome) no existe en Windows: Chrome no expone scripting. JARVIS simplemente no la usa.
- No uses `--reload` con `server.py` en Windows: uvicorn cambia a un event loop que no puede lanzar subprocesos.
- Responder permisos funciona con sesiones en consola (Windows Terminal, PowerShell, cmd). Las sesiones de la app de escritorio de Claude o en segundo plano no tienen consola a la que escribir; JARVIS lo dirá en voz alta en vez de pulsar nada.
- Este port se verificó con la batería de tests del proyecto (más 30 tests nuevos que simulan Windows) en Linux; la primera ejecución real en tu PC es la prueba de fuego. Si algo falla, pega la salida de la ventana del backend a Claude Code.
