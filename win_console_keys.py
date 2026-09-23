"""Press ONE key in the console another process is attached to (Windows).

Run by winplat as a separate, DETACHED process — never imported by the
server — because AttachConsole changes the console of the whole process.

    python win_console_keys.py probe <pid>
        prints the console's id (a number) or "no_console"
    python win_console_keys.py send <pid> <key> <expected_id>
        key is exactly one of: return, escape, 1..9
        prints ok | gone | moved | no_console | bad_key | failed

The key goes into that console's INPUT BUFFER with WriteConsoleInputW, the
same queue a real keypress lands in. It is not a focus-based SendInput: it
reaches only the console the target pid is attached to, whatever window the
user is working in, and the console id is re-checked immediately before the
write (the Windows analogue of dialog.py's "moved" check).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_KEYS = {"return": (0x0D, "\r"), "escape": (0x1B, "\x1b")}
_KEYS.update({str(d): (0x30 + d, str(d)) for d in range(1, 10)})

KEY_EVENT = 0x0001
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value


class _KEY_EVENT_RECORD(ctypes.Structure):
    # Fixed-width types so the layout is the Win32 one (16 bytes) exactly.
    _fields_ = [("bKeyDown", ctypes.c_int32), ("wRepeatCount", ctypes.c_uint16),
                ("wVirtualKeyCode", ctypes.c_uint16), ("wVirtualScanCode", ctypes.c_uint16),
                ("UnicodeChar", ctypes.c_uint16), ("dwControlKeyState", ctypes.c_uint32)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", ctypes.c_uint16), ("Event", _EVENT)]


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetConsoleWindow.restype = wintypes.HWND
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_INPUT_RECORD),
                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _attach(k, pid: int) -> str | None:
    """Attach to pid's console; its id as text, or None."""
    k.FreeConsole()
    if not k.AttachConsole(wintypes.DWORD(pid)):
        return None
    hwnd = k.GetConsoleWindow()
    return str(int(hwnd)) if hwnd else "0"


def main(argv: list[str]) -> str:
    if len(argv) < 2:
        return "failed"
    mode = argv[0]
    try:
        pid = int(argv[1])
    except ValueError:
        return "failed"
    k = _k32()

    if mode == "probe":
        cid = _attach(k, pid)
        k.FreeConsole()
        return cid if cid is not None else "no_console"

    if mode != "send" or len(argv) < 4:
        return "failed"
    key, expected = argv[2], argv[3]
    if key not in _KEYS:
        return "bad_key"
    cid = _attach(k, pid)
    if cid is None:
        err = ctypes.get_last_error()
        # 87 = ERROR_INVALID_PARAMETER: no such process -> the tab is gone.
        return "gone" if err == 87 else "no_console"
    try:
        if expected and cid != expected:
            return "moved"
        h = k.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE,
                          FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if not h or h == INVALID_HANDLE:
            return "failed"
        try:
            vk, ch = _KEYS[key]
            scan = ctypes.WinDLL("user32").MapVirtualKeyW(vk, 0)
            recs = (_INPUT_RECORD * 2)()
            for i, down in enumerate((True, False)):
                recs[i].EventType = KEY_EVENT
                ev = recs[i].Event.KeyEvent
                ev.bKeyDown = 1 if down else 0
                ev.wRepeatCount = 1
                ev.wVirtualKeyCode = vk
                ev.wVirtualScanCode = scan
                ev.UnicodeChar = ord(ch)
                ev.dwControlKeyState = 0
            written = wintypes.DWORD()
            if not k.WriteConsoleInputW(h, recs, 2, ctypes.byref(written)) or written.value != 2:
                return "failed"
            return "ok"
        finally:
            k.CloseHandle(h)
    finally:
        k.FreeConsole()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("failed")
        sys.exit(0)
    try:
        result = main(sys.argv[1:])
    except Exception:
        result = "failed"
    sys.stdout.write(result + "\n")
    sys.stdout.flush()
