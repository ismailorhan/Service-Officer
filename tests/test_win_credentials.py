"""Reaching another Windows machine as a named account.

Until now a remote Windows target was only reachable as whoever was signed in at
this keyboard: the SCM takes no credentials, so an administrator on the target had
to be the person sitting here. These tests cover the other option — a user name and
password — without touching a real machine: `win32wnet` is replaced, because what is
worth testing is that the session is established with the right target and account
*before* the first SCM call, and that a refusal is reported in words.
"""

import pytest

pytest.importorskip("win32wnet")

from core import config as cfg_mod            # noqa: E402
from core import connectors                   # noqa: E402
from core import scm_windows                  # noqa: E402
from core import win_session                  # noqa: E402


class FakeWnet:
    """win32wnet, remembering what it was asked and refusing when told to."""

    def __init__(self, fail=None):
        self.added = []
        self.cancelled = []
        self.fail = fail                       # an exception to raise on connect

    def WNetAddConnection2(self, kind, local, remote, provider, user, password):
        if self.fail is not None:
            raise self.fail
        self.added.append((remote, user, password))

    def WNetCancelConnection2(self, remote, flags, force):
        self.cancelled.append(remote)


class FakeError(Exception):
    """Stands in for pywintypes.error, which carries a winerror and a strerror."""

    def __init__(self, code, text):
        super().__init__(code, "WNetAddConnection2", text)
        self.winerror = code
        self.strerror = text


@pytest.fixture
def wnet(monkeypatch):
    fake = FakeWnet()
    monkeypatch.setattr(win_session, "win32wnet", fake)
    monkeypatch.setattr(win_session, "_open", {})
    return fake


def _machine(**over):
    settings = dict(name="ctl053", label="SQL server", kind="windows",
                    address="10.77.3.51", auth="password",
                    username="CT\\svc-officer", secret_ref="machine/ctl053")
    settings.update(over)
    return cfg_mod.Machine(**settings)


def test_the_session_is_established_against_the_host_not_the_id(wnet, monkeypatch):
    """The id is a label — "SQL server" or "sd" — and does not have to resolve. The
    address is where the machine actually is."""
    monkeypatch.setattr(scm_windows, "reachable", lambda host: True)
    conn = scm_windows.WindowsConnector("ctl053", _machine())
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")

    assert conn.reachable() is True

    assert wnet.added == [(r"\\10.77.3.51\IPC$", "CT\\svc-officer", "s3cret")]


def test_signing_in_happens_before_the_first_control_call(wnet, monkeypatch):
    """Not after it, and not never: the SCM call has to travel on the session, so
    the order is the whole point."""
    order = []
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    monkeypatch.setattr(win_session, "ensure",
                        lambda host, user, password: order.append(f"sign in {host}"))
    monkeypatch.setattr(scm_windows, "restart_service",
                        lambda name, host: order.append(f"restart {name} on {host}"))

    scm_windows.WindowsConnector("ctl053", _machine()).restart("MSSQLSERVER")

    assert order == ["sign in 10.77.3.51", "restart MSSQLSERVER on 10.77.3.51"]


def test_the_signed_in_account_option_touches_nothing(wnet, monkeypatch):
    """The old behaviour has to stay available and stay free: no session, no
    password, nothing to set up."""
    monkeypatch.setattr(scm_windows, "reachable", lambda host: True)
    conn = scm_windows.WindowsConnector("ctl053", _machine(auth="current_user"))

    assert conn.reachable() is True
    assert wnet.added == []


def test_a_wrong_password_is_reported_in_words(monkeypatch):
    """"1326" is not something to put in front of a person."""
    monkeypatch.setattr(win_session, "win32wnet",
                        FakeWnet(fail=FakeError(1326, "Logon failure")))
    monkeypatch.setattr(win_session, "pywintypes",
                        type("m", (), {"error": FakeError}))
    monkeypatch.setattr(win_session, "_open", {})

    with pytest.raises(RuntimeError) as raised:
        win_session.ensure("10.77.3.51", "CT\\svc", "wrong")

    assert "user name or password is wrong" in str(raised.value)


def test_an_existing_session_as_someone_else_is_explained(monkeypatch):
    """Windows allows one account per machine, so this is a real dead end and the
    message has to say what to do about it."""
    monkeypatch.setattr(win_session, "win32wnet",
                        FakeWnet(fail=FakeError(1219, "Multiple connections")))
    monkeypatch.setattr(win_session, "pywintypes",
                        type("m", (), {"error": FakeError}))
    monkeypatch.setattr(win_session, "_open", {})

    with pytest.raises(RuntimeError) as raised:
        win_session.ensure("10.77.3.51", "CT\\svc", "s3cret")

    said = str(raised.value)
    assert "one account per machine" in said and "/delete" in said


def test_the_session_is_made_once_and_reused(wnet, monkeypatch):
    """It is established before every command, so it has to be free when it is
    already there — otherwise a five-second poll reconnects all day."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    monkeypatch.setattr(scm_windows, "query_status", lambda name, host: "Running")
    monkeypatch.setattr(scm_windows, "start_type", lambda name, host: "Automatic")
    monkeypatch.setattr(scm_windows, "process_id", lambda name, host: 42)
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    for _ in range(4):
        conn.status("MSSQLSERVER")

    assert len(wnet.added) == 1


def test_a_new_account_replaces_the_old_session(wnet, monkeypatch):
    """Editing the user must not leave the previous account answering for it."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    monkeypatch.setattr(scm_windows, "reachable", lambda host: True)
    scm_windows.WindowsConnector("ctl053", _machine()).reachable()

    win_session.ensure("10.77.3.51", "CT\\someone-else", "other")

    assert wnet.cancelled == [r"\\10.77.3.51\IPC$"]
    assert [user for _r, user, _p in wnet.added] == ["CT\\svc-officer",
                                                     "CT\\someone-else"]


def test_no_password_saved_says_so_rather_than_failing_obscurely(wnet, monkeypatch):
    monkeypatch.setattr("core.secrets.get", lambda _ref: "")
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    with pytest.raises(RuntimeError) as raised:
        conn.start("MSSQLSERVER")

    assert "no password saved" in str(raised.value)


def test_an_unanswering_machine_gives_up_instead_of_hanging(monkeypatch):
    """Measured on 10.77.3.110, which has RPC's dynamic ports firewalled:
    OpenSCManager took **42 seconds** to answer "the RPC server is unavailable".
    That is a hang to anyone watching, and it held up the poll of every machine
    behind it."""
    import time

    def never(_machine):
        time.sleep(30)
        return True

    monkeypatch.setattr(scm_windows, "_ask_scm", never)
    started = time.perf_counter()

    assert scm_windows.reachable("10.77.3.110", timeout=0.3) is False

    assert time.perf_counter() - started < 3, "waited for the whole call"


def test_this_computer_is_never_put_behind_a_timeout(monkeypatch):
    """There is no network in the way of the local SCM, and a timeout there could
    only ever report the machine we are running on as unreachable."""
    monkeypatch.setattr(scm_windows, "_ask_scm", lambda machine: machine == "")

    assert scm_windows.reachable("") is True


def test_forgetting_a_machine_signs_out_of_it(wnet, monkeypatch):
    """A cached connector is dropped when settings change; the Windows session it
    established outlives the object, so it has to be dropped too."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    monkeypatch.setattr(scm_windows, "reachable", lambda host: True)
    record = _machine()
    connectors.use_config(lambda: cfg_mod.Config(machines=[cfg_mod.Machine(),
                                                           record]))
    connectors.for_machine("ctl053").reachable()

    connectors.forget("ctl053")

    assert wnet.cancelled == [r"\\10.77.3.51\IPC$"]
