"""Starting a window on somebody's desktop from a process that has no desktop.

Three attempts got this wrong and each was only visible on a real machine, so this file is split
deliberately: the calls into Windows are exercised **for real**, and only the decisions above
them are stubbed.

That split is the lesson. The first version's tests stubbed `console_session` — every one of them
replaced the single function that was broken, which is not testing a boundary, it is deleting
one. A wrong DLL name and a wrong *question* both live at that boundary, and nothing above it can
see either.
"""

import ctypes.wintypes

import pytest

from core import session


# ── the boundary, unstubbed ───────────────────────────────────────────────
def test_every_windows_function_this_uses_actually_exists():
    """`ctypes.WinDLL(x)` loads happily and fails only when a missing name is *used*, so a wrong
    DLL is an AttributeError at the call rather than at import. 2.2.10 shipped two:

        WTSGetActiveConsoleSessionId  kernel32  — not wtsapi32, despite the name
        CreateProcessAsUserW          advapi32  — not kernel32
    """
    for holder, name in ((session._kernel, "ProcessIdToSessionId"),
                         (session._kernel, "GetCurrentProcessId"),
                         (session._kernel, "CloseHandle"),
                         (session._wts, "WTSEnumerateSessionsW"),
                         (session._wts, "WTSFreeMemory"),
                         (session._wts, "WTSQueryUserToken"),
                         (session._advapi, "CreateProcessAsUserW"),
                         (session._userenv, "CreateEnvironmentBlock"),
                         (session._userenv, "DestroyEnvironmentBlock")):
        assert hasattr(holder, name), f"{name} is not in that DLL"


def test_the_last_error_is_the_one_this_call_set():
    """Without `use_last_error=True`, `ctypes.get_last_error()` reads a private copy ctypes only
    fills in for DLLs opened that way — so it stays at whatever it was, and every "error %s" in
    this module's log becomes a number about nothing. The diagnosis is the whole point of that
    logging: two releases were lost to a failure whose only evidence was an exit code.

    Asked by making a call fail on purpose, because there is no public attribute to read: an
    unknown name on a WinDLL is looked up as an *export*, so `dll._use_last_error` asks the DLL
    for a function by that name and raises.
    """
    import ctypes
    ctypes.set_last_error(0)
    answer = ctypes.wintypes.DWORD()
    # No process has this id. The call fails and Windows sets a reason.
    assert not session._kernel.ProcessIdToSessionId(0xFFFFFFF0, ctypes.byref(answer))
    assert ctypes.get_last_error() != 0, \
        "the failure's reason did not arrive — the DLL was opened without use_last_error"


def test_this_process_knows_which_session_it_is_in():
    """Not compared against a number: which session a test runs in depends on how it was
    started. That it answers, and that a normal process is not in session 0, is the claim."""
    answer = session.my_session()
    assert isinstance(answer, int)
    assert answer != session.SERVICES_SESSION, \
        "a test run from a desktop should not be in the services session"


def test_the_sessions_somebody_is_in_can_really_be_listed():
    """The question the previous version got wrong. It asked for the *console* session, which on
    a server sits `Connected` with nobody in it while the operator is on RDP — measured:

        session 0  Services   Disconnected
        session 1  RDP-Tcp#0  Active        <- the person
        session 2  Console    Connected     <- nobody
        WTSGetActiveConsoleSessionId -> 2
    """
    found = session.active_sessions()
    assert isinstance(found, list)
    assert all(isinstance(s, int) for s in found)
    assert session.SERVICES_SESSION not in found, \
        "session 0 has no desktop and must never be offered as one"
    # This test is running in a session somebody is signed into, by definition.
    assert session.my_session() in found, found


# ── the decisions above it ────────────────────────────────────────────────
def test_a_service_never_falls_back_to_an_ordinary_launch(monkeypatch, tmp_path):
    """The worse of the two bugs. From a process with no desktop, an ordinary `CreateProcess` is
    not a fallback — it *is* the failure this module exists to prevent: a tray application
    running in session 0 that nobody can see. It happened, and the process was still sitting
    there afterwards.
    """
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "active_sessions", lambda: [1])
    monkeypatch.setattr(session, "_as_the_person", lambda *_a: 0)   # no token
    monkeypatch.setattr(session, "my_session", lambda: session.SERVICES_SESSION)

    def refuse(*_a, **_k):
        raise AssertionError("a service started an invisible process in session 0")
    monkeypatch.setattr(session.subprocess, "Popen", refuse)

    assert session.start_for_the_person(str(exe)) == []


def test_a_process_with_a_desktop_may_fall_back(monkeypatch, tmp_path):
    """An elevated installer a person is running cannot borrow a token — that needs SYSTEM — but
    it is already on that person's desktop, so an ordinary launch reaches it."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "active_sessions", lambda: [1])
    monkeypatch.setattr(session, "_as_the_person", lambda *_a: 0)
    monkeypatch.setattr(session, "my_session", lambda: 1)

    class _Started:
        pid = 77
    seen = {}
    monkeypatch.setattr(session.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw) or _Started())

    assert session.start_for_the_person(str(exe)) == [77]
    assert seen["argv"][0] == str(exe)
    assert seen["kw"]["creationflags"] & session.DETACHED_PROCESS


def test_one_copy_per_session_somebody_is_in(monkeypatch, tmp_path):
    """What was taken away: the installer's `taskkill /F /IM`, run as SYSTEM, ends every copy on
    the machine. Two people signed in means two to put back."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "active_sessions", lambda: [1, 3])
    tried = []
    monkeypatch.setattr(session, "_as_the_person",
                        lambda s, c, f: tried.append(s) or (100 + s))

    def refuse(*_a, **_k):
        raise AssertionError("fell back although a session was borrowed")
    monkeypatch.setattr(session.subprocess, "Popen", refuse)

    assert session.start_for_the_person(str(exe)) == [101, 103]
    assert tried == [1, 3]


def test_nobody_signed_in_is_an_answer_not_a_failure(monkeypatch, tmp_path):
    """A server with nobody at it. The Startup shortcut takes over whenever somebody signs in,
    and an invisible copy in session 0 would stop even that by holding a mutex in a session
    nobody will ever look at."""
    exe = tmp_path / "ServiceOfficer.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(session, "active_sessions", lambda: [])
    monkeypatch.setattr(session, "my_session", lambda: session.SERVICES_SESSION)

    def refuse(*_a, **_k):
        raise AssertionError("started a process with nobody to show it to")
    monkeypatch.setattr(session.subprocess, "Popen", refuse)

    assert session.start_for_the_person(str(exe)) == []


def test_a_missing_exe_is_not_an_error(tmp_path):
    """A hub-only install has no tray application beside it, which is an ordinary answer."""
    assert session.start_for_the_person(str(tmp_path / "nope.exe")) == []


def test_the_desktop_is_named():
    r"""Without `winsta0\default` the process starts on no desktop at all and a Qt application
    exits on the spot."""
    assert session.DESKTOP == "winsta0" + chr(92) + "default"
