"""Windows platform layer for JARVIS.

Upstream JARVIS is macOS-only: Terminal, notifications, screenshots, window
listing and dialog answering all go through AppleScript. This module is the
Windows equivalent of each of those, kept in ONE file so the rest of the code
base only grows a `if sys.platform == "win32": return winplat.x(...)` at the
top of the affected functions. Nothing here runs on macOS or Linux, and the
module imports cleanly everywhere (every Win32 handle is resolved lazily).

The same rules the macOS code follows hold here:

* argument lists, never a shell string — nothing untrusted is ever parsed by
  cmd.exe or PowerShell (notification text travels in environment variables
  and is XML-escaped by PowerShell itself);
* nothing raises into a voice turn — every public coroutine returns a
  success flag or an outcome string;
* dialog answering keeps the closed vocabulary (Return, Escape, 1-9) and the
  "target by identity, never by focus" rule: the key is written into the
  console that the session's own process is attached to, re-checked at press
  time, and never into whatever window happens to be in front.

One Windows-specific trap is handled here and nowhere else: `os.kill(pid, 0)`
on Windows is NOT a liveness probe — it calls TerminateProcess and kills the
target. `pid_alive` below is the safe replacement.
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("jarvis.winplat")

IS_WINDOWS = sys.platform == "win32"

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_ERROR_PIPE_BUSY = 231

PIPE_PREFIX = "\\\\.\\pipe\\"


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _u32():
    return ctypes.WinDLL("user32", use_last_error=True)


# ── processes ──────────────────────────────────────────────────────────────

def pid_alive(pid) -> bool:
    """True if a process with this pid exists. Never touches the process.

    OpenProcess + GetExitCodeProcess. ACCESS_DENIED on OpenProcess still
    means the process exists (an elevated one we may not query).
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        k32 = _k32()
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle(h)
    except Exception as e:                      # pragma: no cover - defensive
        log.warning(f"pid_alive({pid}) failed: {e}")
        return False


def kill_tree(pid) -> None:
    """Kill a process AND its children (MCP servers, tool shells).

    `Process.kill()` on Windows is TerminateProcess on one pid; the node
    children of a `claude` process would be orphaned. `taskkill /T` walks the
    tree. Fire-and-forget: never blocks the caller.
    """
    try:
        subprocess.Popen(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW)
    except Exception as e:                      # pragma: no cover - defensive
        log.warning(f"taskkill for {pid} failed: {e}")


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def claude_argv(spec: str | None) -> list[str]:
    """The argv prefix that starts Claude Code on this machine.

    Two Windows traps are avoided here:

    * `shlex.split` in POSIX mode eats the backslashes of a Windows path, so
      `C:\\Users\\os\\...\\claude.exe` became `C:Usersos...claude.exe`.
    * The npm install is a `claude.cmd` batch shim. Arguments to a batch file
      go through cmd.exe, which truncates them at the first newline and
      re-parses `&`, `|`, `^` and `%` — and the brain's system prompt is
      multi-line text. So a `.cmd` is resolved to `node <package>\\cli.js`
      (or the package's bundled `claude.exe`) and cmd.exe is never involved.
    """
    spec = (spec or "claude").strip()
    rest: list[str] = []
    if os.path.isfile(_strip_quotes(spec)):
        exe = _strip_quotes(spec)
    else:
        parts = [_strip_quotes(p) for p in shlex.split(spec, posix=False)] or ["claude"]
        exe, rest = parts[0], parts[1:]
        if not os.path.isfile(exe):
            exe = shutil.which(exe) or exe

    if exe.lower().endswith((".cmd", ".bat", ".ps1")) or not os.path.splitext(exe)[1]:
        base = Path(exe).parent
        pkg = base / "node_modules" / "@anthropic-ai" / "claude-code"
        for native in (pkg / "bin" / "claude.exe", pkg / "claude.exe"):
            if native.is_file():
                return [str(native)] + rest
        for entry in (pkg / "cli.js", pkg / "cli.mjs"):
            if entry.is_file():
                node = base / "node.exe"
                node_path = str(node) if node.is_file() else (shutil.which("node") or "node")
                return [node_path, str(entry)] + rest
        # A bare `claude.exe` next to the shim (native installer layout).
        sibling = Path(exe).with_suffix(".exe")
        if sibling.is_file():
            return [str(sibling)] + rest
    return [exe] + rest


# ── folders ────────────────────────────────────────────────────────────────

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    import uuid
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def desktop_path() -> Path:
    """The real Desktop, including a OneDrive-redirected one.

    `Path.home() / "Desktop"` is empty on most Windows machines that sign in
    with a Microsoft account: the Desktop lives in `OneDrive\\Desktop`.
    """
    fallback = Path.home() / "Desktop"
    if not IS_WINDOWS:
        return fallback
    try:
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        ptr = ctypes.c_wchar_p()
        fid = _guid("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")   # FOLDERID_Desktop
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD,
                                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(ptr)) == 0:
            try:
                return Path(ptr.value)
            finally:
                ole32.CoTaskMemFree(ptr)
    except Exception as e:                      # pragma: no cover - defensive
        log.warning(f"desktop_path lookup failed: {e}")
    return fallback


# ── named pipes (the session inbox on Windows) ─────────────────────────────

def pipe_exists(path: str | None) -> bool:
    if not path:
        return False
    if not path.lower().startswith(PIPE_PREFIX.lower()):
        return os.path.exists(path)
    name = path[len(PIPE_PREFIX):].lower()
    try:
        return name in (n.lower() for n in os.listdir(PIPE_PREFIX))
    except OSError:
        return False


def write_pipe(path: str, payload: bytes, timeout: float = 5.0) -> str:
    """Write one payload to a named pipe. Returns sent / not_live / failed."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with open(path, "wb", buffering=0) as f:
                f.write(payload)
            return "sent"
        except FileNotFoundError:
            return "not_live"
        except OSError as e:
            if getattr(e, "winerror", None) == _ERROR_PIPE_BUSY and time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            log.warning(f"pipe write to {path} failed: {e}")
            return "failed"


# ── notifications ──────────────────────────────────────────────────────────

# Fixed script. The untrusted text (a session title, a transcript line) is
# read from environment variables and XML-escaped by PowerShell; it is never
# part of the script source, so there is nothing to inject into.
_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:JARVIS_N_TITLE)
$s = [Security.SecurityElement]::Escape($env:JARVIS_N_SUB)
$m = [Security.SecurityElement]::Escape($env:JARVIS_N_MSG)
$lines = "<text>$t</text>"
if ($s) { $lines += "<text>$s</text>" }
$lines += "<text>$m</text>"
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'>$lines</binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def _powershell() -> str | None:
    # Windows PowerShell 5.1 specifically: it can load WinRT types, pwsh 7 cannot.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    p = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(p) if p.is_file() else shutil.which("powershell")


def notifications_available() -> bool:
    return IS_WINDOWS and _powershell() is not None


async def notify(title: str, message: str, subtitle: str = "", timeout: float = 10.0) -> bool:
    ps = _powershell()
    if not ps:
        return False
    env = dict(os.environ)
    env.update({"JARVIS_N_TITLE": title, "JARVIS_N_MSG": message, "JARVIS_N_SUB": subtitle})
    encoded = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        proc = await asyncio.create_subprocess_exec(
            ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        log.warning(f"notifier: could not start PowerShell: {e}")
        return False
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        log.warning("notifier: PowerShell toast timed out")
        return False
    if proc.returncode != 0:
        log.warning(f"notifier: toast failed: {err.decode(errors='replace').strip()[:300]}")
        return False
    return True


# ── terminal, browser, editor ──────────────────────────────────────────────

def _shell_exe() -> str:
    return shutil.which("pwsh") or _powershell() or "powershell.exe"


async def open_terminal(cwd: str | None = None, command: str = "") -> bool:
    """A new console window (Windows Terminal when it is the default host),
    started IN `cwd`, optionally running one command.

    `cwd` is passed as the process's working directory, never spliced into a
    command line. `command` has already been through `builds.command_problem`
    (no `;`, `&`, `|`, `$`, backtick, quotes or newlines), so PowerShell sees
    one plain command.
    """
    argv = [_shell_exe(), "-NoLogo", "-NoExit"]
    if command:
        argv += ["-Command", command]
    try:
        subprocess.Popen(argv, cwd=cwd or None, creationflags=CREATE_NEW_CONSOLE,
                         close_fds=True)
        return True
    except OSError as e:
        log.error(f"open_terminal failed: {e}")
        return False


def _find_app(candidates: list[Path], which: list[str]) -> str | None:
    for c in candidates:
        if c.is_file():
            return str(c)
    for w in which:
        found = shutil.which(w)
        if found and found.lower().endswith(".exe"):
            return found
    return None


def _env_path(var: str, *rest: str) -> Path | None:
    base = os.environ.get(var)
    return Path(base, *rest) if base else None


def chrome_path() -> str | None:
    cands = [p for p in (
        _env_path("ProgramFiles", "Google", "Chrome", "Application", "chrome.exe"),
        _env_path("ProgramFiles(x86)", "Google", "Chrome", "Application", "chrome.exe"),
        _env_path("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
    ) if p]
    return _find_app(cands, ["chrome"])


def firefox_path() -> str | None:
    cands = [p for p in (
        _env_path("ProgramFiles", "Mozilla Firefox", "firefox.exe"),
        _env_path("ProgramFiles(x86)", "Mozilla Firefox", "firefox.exe"),
    ) if p]
    return _find_app(cands, ["firefox"])


_URL_SCHEMES = ("http://", "https://", "file:///")


async def open_url(url: str, browser: str = "chrome") -> tuple[bool, str]:
    """Open a URL. Returns (success, app name as spoken)."""
    if not url.lower().startswith(_URL_SCHEMES):
        log.warning(f"refusing to open a non-web URL: {url[:80]!r}")
        return False, "the browser"
    if browser.lower() == "firefox":
        exe, name = firefox_path(), "Firefox"
    else:
        exe, name = chrome_path(), "Chrome"
    try:
        if exe:
            subprocess.Popen([exe, url], close_fds=True)
        else:
            os.startfile(url)                    # the default browser
            name = "your browser"
        return True, name
    except OSError as e:
        log.error(f"open_url failed: {e}")
        return False, name


def vscode_path() -> str | None:
    cands = [p for p in (
        _env_path("LOCALAPPDATA", "Programs", "Microsoft VS Code", "Code.exe"),
        _env_path("ProgramFiles", "Microsoft VS Code", "Code.exe"),
    ) if p]
    found = _find_app(cands, [])
    if found:
        return found
    shim = shutil.which("code")               # ...\Microsoft VS Code\bin\code.cmd
    if shim:
        exe = Path(shim).parent.parent / "Code.exe"
        if exe.is_file():
            return str(exe)
    return None


async def open_in_editor(path: str) -> tuple[bool, str]:
    """VS Code if installed; otherwise Explorer for a folder, Notepad for a
    file. Deliberately NOT `os.startfile(file)`: the default action of a
    .bat, .ps1, .exe or .js file on Windows is to RUN it."""
    code = vscode_path()
    try:
        if code:
            subprocess.Popen([code, str(path)], close_fds=True)
            return True, "VS Code"
        if os.path.isdir(path):
            subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
            return True, "Explorer"
        subprocess.Popen(["notepad.exe", str(path)], close_fds=True)
        return True, "Notepad"
    except OSError as e:
        log.error(f"open_in_editor failed: {e}")
        return False, "your editor"


# ── the screen ─────────────────────────────────────────────────────────────

def capture_png(display: int | None, max_edge: int) -> tuple[bytes, int, int, bool]:
    """(png, width, height, looks_blank) of one monitor, shrunk to max_edge.

    `display` is 1-based like `screencapture -D`; None is the primary
    monitor. Needs `mss` and `Pillow` (installed by install.ps1). Raises
    RuntimeError with a speakable message.
    """
    try:
        import mss
        from PIL import Image, ImageStat
    except ImportError as e:
        raise RuntimeError("the screenshot libraries aren't installed, sir — "
                           "run install.ps1 again") from e
    with mss.mss() as sct:
        monitors = sct.monitors[1:]
        if not monitors:
            raise RuntimeError("I couldn't find a display, sir")
        if display:
            if display < 1 or display > len(monitors):
                raise RuntimeError(f"there's no display {display}, sir")
            mon = monitors[display - 1]
        else:
            mon = next((m for m in monitors if m["left"] == 0 and m["top"] == 0), monitors[0])
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    img.thumbnail((max_edge, max_edge))
    sample = img.resize((32, 32))
    stat = ImageStat.Stat(sample)
    blank = all(s < 1.5 for s in stat.stddev)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.width, img.height, blank


def list_windows(max_windows: int) -> list[tuple[str, str, bool]]:
    """(app, title, frontmost) for visible top-level windows, front first."""
    u32 = _u32()
    k32 = _k32()
    dwm = ctypes.WinDLL("dwmapi")
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetWindow.restype = wintypes.HWND
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD]

    fg = u32.GetForegroundWindow()
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER, DWMWA_CLOAKED = -20, 0x80, 4, 14
    out: list[tuple[str, str, bool]] = []

    def app_name(hwnd) -> str:
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return "unknown"
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).stem
            return "unknown"
        finally:
            k32.CloseHandle(h)

    def cb(hwnd, _lparam):
        try:
            if not u32.IsWindowVisible(hwnd) or u32.GetWindow(hwnd, GW_OWNER):
                return True
            if u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked),
                                      ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.strip()
            if not title or title in ("Program Manager",):
                return True
            out.append((app_name(hwnd), title, bool(fg) and hwnd == fg))
        except Exception:
            pass
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)     # z-order: front-most first
    out.sort(key=lambda w: not w[2])
    return out[:max_windows]


# ── answering a prompt in a console session ────────────────────────────────

# The helper runs in its OWN process, started DETACHED (no console), so that
# AttachConsole never touches the server's console. It prints one word.
_CONSOLE_HELPER = Path(__file__).with_name("win_console_keys.py")


async def _console_helper(*args: str, timeout: float) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_CONSOLE_HELPER), *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=DETACHED_PROCESS)
    except OSError as e:
        log.warning(f"console helper could not start: {e}")
        return "failed"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "failed"
    if err:
        log.debug(f"console helper stderr: {err.decode(errors='replace').strip()[:200]}")
    return out.decode(errors="replace").strip() or "failed"


def console_id_for_pid(pid) -> str | None:
    """`con:<id>` for the console a live pid is attached to, else None.

    The Windows stand-in for a tty: two processes share it exactly when they
    share a console window/tab. Synchronous (called via to_thread)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or not pid_alive(pid):
        return None
    try:
        out = subprocess.run([sys.executable, str(_CONSOLE_HELPER), "probe", str(pid)],
                             capture_output=True, text=True, timeout=3.0,
                             stdin=subprocess.DEVNULL, creationflags=DETACHED_PROCESS)
    except Exception as e:
        log.warning(f"console probe for {pid} failed: {e}")
        return None
    word = out.stdout.strip()
    return f"con:{word}" if word.isdigit() else None


async def console_send_key(pid: int, normalized_key: str, console_id: str,
                           timeout: float = 10.0) -> str:
    """Write one key into the console that owns `pid`, if it is still the
    console `console_id` names. Returns ok / gone / moved / no_console / failed."""
    expected = console_id.split(":", 1)[1] if console_id and ":" in console_id else ""
    return await _console_helper("send", str(int(pid)), normalized_key, expected,
                                 timeout=timeout)
