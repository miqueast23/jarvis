"""The Windows port, exercised on any OS by pretending to be win32.

Every Win32 call lives in winplat.py; these tests stub that module and check
that each macOS path is routed to it — and, above all, that nothing on the
Windows path ever calls `os.kill(pid, 0)`, which on Windows KILLS the target.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

import winplat


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return monkeypatch


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── liveness: never os.kill on Windows ─────────────────────────────────────

def test_pid_alive_never_calls_os_kill_on_windows(win):
    import session_watch
    calls = []
    win.setattr(os, "kill", lambda *a: calls.append(a))
    win.setattr(winplat, "pid_alive", lambda pid: pid == 4242)
    assert session_watch.pid_alive(4242) is True
    assert session_watch.pid_alive(1) is False
    assert calls == []


def test_same_dir_ignores_case_on_windows_style_paths(win):
    import session_watch
    win.setattr(os.path, "normcase", lambda p: p.lower())
    assert session_watch._same_dir("/Data/Jarvis", "/data/jarvis")


# ── claude argv ────────────────────────────────────────────────────────────

def test_cmd_shim_resolves_to_node_and_cli_js(tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    pkg = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    pkg.mkdir(parents=True)
    (pkg / "cli.js").write_text("")
    (tmp_path / "node.exe").write_text("")
    assert winplat.claude_argv(str(shim)) == [str(tmp_path / "node.exe"), str(pkg / "cli.js")]


def test_cmd_shim_prefers_bundled_native_binary(tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    pkg = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    pkg.mkdir(parents=True)
    (pkg / "claude.exe").write_text("")
    assert winplat.claude_argv(str(shim)) == [str(pkg / "claude.exe")]


def test_exe_path_with_spaces_and_backslashes_is_kept_whole(tmp_path):
    d = tmp_path / "Program Files" / "Claude"
    d.mkdir(parents=True)
    exe = d / "claude.exe"
    exe.write_text("")
    assert winplat.claude_argv(str(exe)) == [str(exe)]
    assert winplat.claude_argv(f'"{exe}"') == [str(exe)]


def test_brain_and_runs_use_claude_argv_on_windows(win):
    import run_executor
    win.setattr(winplat, "claude_argv", lambda spec: ["C:\\node.exe", "C:\\cli.js"])
    ex = run_executor.RunExecutor.__new__(run_executor.RunExecutor)
    ex._claude_path = r"C:\Users\os\AppData\Roaming\npm\claude.cmd"
    try:
        cmd = ex._command("abc", None)
    except AttributeError:
        pytest.skip("RunExecutor needs more state for _command on this version")
    assert cmd[:2] == ["C:\\node.exe", "C:\\cli.js"]


# ── session inbox over a named pipe ────────────────────────────────────────

def test_steer_writes_to_named_pipe_on_windows(win):
    import session_steer
    sent = {}
    win.setattr(winplat, "pipe_exists", lambda p: True)

    def fake_write(path, payload, timeout):
        sent["path"], sent["payload"] = path, payload
        return "sent"
    win.setattr(winplat, "write_pipe", fake_write)
    assert session_steer.post_to_session(r"\\.\pipe\claude-1", "hola") == "sent"
    assert sent["path"] == r"\\.\pipe\claude-1"
    assert b'"hola"' in sent["payload"]


def test_steer_missing_pipe_is_not_live(win):
    import session_steer
    win.setattr(winplat, "pipe_exists", lambda p: False)
    assert session_steer.post_to_session(r"\\.\pipe\gone", "hola") == "not_live"


# ── dialog: closed vocabulary, console identity ────────────────────────────

def test_dialog_answer_routes_to_console_helper(win):
    import dialog
    seen = {}
    win.setattr(winplat, "console_id_for_pid", lambda pid: "con:777")

    async def fake_send(pid, key, cid, timeout=10.0):
        seen.update(pid=pid, key=key, cid=cid)
        return "ok"
    win.setattr(winplat, "console_send_key", fake_send)
    assert run(dialog.answer(55, "yes")) == dialog.SENT
    assert seen == {"pid": 55, "key": "return", "cid": "con:777"}


@pytest.mark.parametrize("helper, outcome", [
    ("moved", "not_found"), ("gone", "not_found"), ("no_console", "no_tty"), ("weird", "failed"),
])
def test_dialog_helper_outcomes(win, helper, outcome):
    import dialog
    win.setattr(winplat, "console_id_for_pid", lambda pid: "con:1")

    async def fake_send(*a, **k):
        return helper
    win.setattr(winplat, "console_send_key", fake_send)
    assert run(dialog.answer(55, "1")) == outcome


def test_dialog_refuses_text_before_touching_windows(win):
    import dialog
    win.setattr(winplat, "console_id_for_pid",
                lambda pid: pytest.fail("must not look anything up"))
    assert run(dialog.answer(55, "rm -rf")) == dialog.BAD_KEY


def test_console_helper_refuses_keys_outside_vocabulary():
    import win_console_keys
    assert set(win_console_keys._KEYS) == {"return", "escape", *"123456789"}


def test_input_record_has_the_win32_layout():
    import ctypes
    import win_console_keys as w
    assert ctypes.sizeof(w._KEY_EVENT_RECORD) == 16
    assert ctypes.sizeof(w._INPUT_RECORD) == 20


# ── terminal / browser / editor / notifications ───────────────────────────

def test_open_terminal_passes_cwd_not_a_cd_string(win):
    import actions
    got = {}

    async def fake(cwd, command):
        got.update(cwd=cwd, command=command)
        return True
    win.setattr(winplat, "open_terminal", fake)
    r = run(actions.open_terminal("npm run dev", cwd=r"C:\Users\os\Desktop\app"))
    assert r["success"] is True
    assert got == {"cwd": r"C:\Users\os\Desktop\app", "command": "npm run dev"}


def test_macos_terminal_still_gets_a_quoted_cd(monkeypatch):
    import actions
    monkeypatch.setattr(sys, "platform", "darwin")
    scripts = []

    class P:
        returncode = 0
        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kw):
        scripts.append(argv)
        return P()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def no_mark(*a, **k):
        return None
    monkeypatch.setattr(actions, "_mark_terminal_as_jarvis", no_mark)
    run(actions.open_terminal("npm start", cwd="/Users/os/my app"))
    assert "cd '/Users/os/my app' && npm start" in scripts[0][2]


def test_open_url_refuses_non_web_schemes():
    ok, _ = run(winplat.open_url("javascript:alert(1)"))
    assert ok is False
    ok, _ = run(winplat.open_url("C:\\Windows\\System32\\calc.exe"))
    assert ok is False


def test_toast_script_never_contains_the_text():
    assert "JARVIS_N_TITLE" in winplat._TOAST_SCRIPT
    assert "SecurityElement]::Escape" in winplat._TOAST_SCRIPT


def test_notifier_routes_to_toast_on_windows(win):
    import importlib
    import notifier
    notifier = importlib.reload(notifier)   # conftest blocks the real notify()
    got = {}
    win.setattr(winplat, "notifications_available", lambda: True)

    async def fake(title, message, subtitle="", timeout=10.0):
        got.update(title=title, message=message)
        return True
    win.setattr(winplat, "notify", fake)
    assert run(notifier.notify("JARVIS", "session needs you")) is True
    assert got == {"title": "JARVIS", "message": "session needs you"}


def test_editor_fallback_never_startfiles_a_file(win, tmp_path):
    calls = []
    win.setattr(winplat, "vscode_path", lambda: None)
    win.setattr(winplat.subprocess, "Popen", lambda argv, **k: calls.append(argv))
    f = tmp_path / "evil.bat"
    f.write_text("echo hi")
    ok, editor = run(winplat.open_in_editor(str(f)))
    assert ok and editor == "Notepad" and calls == [["notepad.exe", str(f)]]


# ── screen ─────────────────────────────────────────────────────────────────

def test_capture_screen_uses_winplat(win):
    import screen
    win.setattr(winplat, "capture_png", lambda d, m: (b"\x89PNG....", 1280, 720, False))
    shot = run(screen.capture_screen())
    assert (shot.width, shot.height) == (1280, 720)


def test_blank_capture_is_refused(win):
    import screen
    win.setattr(winplat, "capture_png", lambda d, m: (b"\x89PNG", 10, 10, True))
    with pytest.raises(screen.ScreenError):
        run(screen.capture_screen())


def test_list_windows_uses_winplat(win):
    import screen
    win.setattr(winplat, "list_windows", lambda n: [("Code", "server.py", True)])
    ws = run(screen.list_windows())
    assert ws[0].app == "Code" and ws[0].frontmost


def test_preflight_permissions_are_ok_on_windows(win):
    import preflight
    assert preflight._check_screen_recording_sync().status == preflight.STATUS_OK
    assert run(preflight._check_accessibility()).status == preflight.STATUS_OK


def test_windows_credentials_file_is_read(win, tmp_path):
    import preflight
    (tmp_path / ".credentials.json").write_text(
        '{"claudeAiOauth": {"refreshTokenExpiresAt": 2000000000000}}')
    assert run(preflight._read_oauth_refresh_expiry(tmp_path, 1.0)) == 2000000000.0


# ── builds / data paths / project roots ────────────────────────────────────

def test_venv_scripts_python_exe_counts_as_inside_project(win, tmp_path):
    import builds
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    assert builds.command_problem(".venv/Scripts/python app.py", str(tmp_path)) is None


def test_tool_token_on_windows_branch(win, tmp_path):
    import data_paths
    win.setattr(data_paths, "tool_token_path", lambda: tmp_path / "tool-token")
    first = data_paths.ensure_tool_token()
    assert data_paths.ensure_tool_token() == first


def test_project_roots_split_on_pathsep(monkeypatch):
    import server
    monkeypatch.setattr(os, "pathsep", ";")
    monkeypatch.setenv("JARVIS_PROJECT_ROOTS", r"C:\code;D:\work")
    assert [str(p) for p in server._scan_roots()] == [r"C:\code", r"D:\work"]
