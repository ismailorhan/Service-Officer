"""Starting a window on somebody's desktop from a process that has no desktop.

A service runs in session 0, which has no visible desktop, so anything it starts is invisible —
running, holding the single-instance mutex, and unreachable. That is not hypothetical: the first
automatic update killed the tray application to replace its files and nothing put it back.

The Win32 calls themselves are Windows', and mocking them would test the mock. What is tested
here is the decision: which path is taken, and what happens when there is nobody to show
anything to.
"""

import pytest

from core import session


def test_nobody_signed_in_is_an_answer_not_a_failure(monkeypatch, tmp_path):
    """A server with nobody at it. The Startup shortcut takes over whenever somebody does sign
    in, and there is nothing to do now — so this must not fall back to starting an invisible
    process in session 0, which is the thing being avoided."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "console_session", lambda: session.NOBODY)

    def refuse(*_a, **_k):
        raise AssertionError("started a process with nobody to show it to")
    monkeypatch.setattr(session.subprocess, "Popen", refuse)

    assert session.start_for_the_person(str(exe)) == 0


def test_the_console_session_is_tried_first(monkeypatch, tmp_path):
    """The caller is usually a service, and for a service this is the only path that works."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "console_session", lambda: 1)
    tried = []
    monkeypatch.setattr(session, "_as_the_person",
                        lambda s, c, f: tried.append((s, c)) or 4242)

    def refuse(*_a, **_k):
        raise AssertionError("fell back without needing to")
    monkeypatch.setattr(session.subprocess, "Popen", refuse)

    assert session.start_for_the_person(str(exe)) == 4242
    assert tried and tried[0][0] == 1
    assert "ServiceOfficer.exe" in tried[0][1]


def test_an_installer_a_person_is_running_falls_back(monkeypatch, tmp_path):
    """WTSQueryUserToken needs SYSTEM, which a service has and an elevated installer does not.
    That person *has* a desktop, so an ordinary launch reaches it."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "console_session", lambda: 1)
    monkeypatch.setattr(session, "_as_the_person", lambda *_a: 0)

    class _Started:
        pid = 77
    seen = {}
    monkeypatch.setattr(session.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw) or _Started())

    assert session.start_for_the_person(str(exe)) == 77
    assert seen["argv"][0] == str(exe)
    assert seen["kw"]["creationflags"] & session.DETACHED_PROCESS


def test_a_missing_exe_is_not_an_error(monkeypatch, tmp_path):
    """A hub-only install has no tray application beside it, which is an ordinary answer."""
    monkeypatch.setattr(session, "console_session", lambda: 1)
    assert session.start_for_the_person(str(tmp_path / "nope.exe")) == 0


def test_the_desktop_is_named():
    r"""Without `winsta0\default` the process starts on no desktop at all and a Qt application
    exits on the spot."""
    assert session.DESKTOP == "winsta0" + chr(92) + "default"
