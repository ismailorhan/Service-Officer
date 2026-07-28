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


def _fake_scm(monkeypatch, per_service=None):
    """Stand in for the held service-manager connection.

    The connection is the thing being measured elsewhere, so here it is replaced
    wholesale: `do(work)` hands the work a fake handle and counts the calls.
    """
    opens = []

    class Held:
        def do(self, work):
            opens.append("used")
            return work(object())

    monkeypatch.setattr(scm_windows, "held_for", lambda machine="": Held())
    monkeypatch.setattr(scm_windows, "state_on",
                        per_service or (lambda _scm, _name: ("Running", 42, 0)))
    monkeypatch.setattr(scm_windows, "start_type_on",
                        lambda _scm, _name: "Automatic")
    return opens


def test_the_session_is_made_once_and_reused(wnet, monkeypatch):
    """It is established before every command, so it has to be free when it is
    already there — otherwise a five-second poll reconnects all day."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    _fake_scm(monkeypatch)
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    for _ in range(4):
        conn.status("MSSQLSERVER")

    assert len(wnet.added) == 1


def test_every_service_on_a_machine_is_read_over_one_connection(wnet, monkeypatch):
    """Measured: opening a connection to a remote Windows machine in another domain
    took 21 seconds, and reading a service on the open one took 7 milliseconds. The
    poller asked per service and each question opened its own — three of them, so
    sixty-three seconds to learn one service's state."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")
    opens = _fake_scm(monkeypatch)
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    found = conn.statuses(["MSSQLSERVER", "SQLWriter", "B1ServerTools64"])

    assert set(found) == {"MSSQLSERVER", "SQLWriter", "B1ServerTools64"}
    assert all(s.state == "Running" and s.start_type == "Automatic"
               for s in found.values())
    assert len(opens) == 1, f"used the connection {len(opens)} times for one poll"


def test_one_missing_service_does_not_sink_the_whole_poll(wnet, monkeypatch):
    """A service that has been uninstalled is about that service. Losing the other
    four to it would blank the machine on screen."""
    monkeypatch.setattr("core.secrets.get", lambda _ref: "s3cret")

    def per_service(_scm, name):
        if name == "GoneAway":
            raise FakeError(1060, "The specified service does not exist")
        return ("Running", 7, 0)

    monkeypatch.setattr(scm_windows, "pywintypes",
                        type("m", (), {"error": FakeError}))
    _fake_scm(monkeypatch, per_service)
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    found = conn.statuses(["MSSQLSERVER", "GoneAway"])

    assert found["MSSQLSERVER"].state == "Running"
    assert found["GoneAway"].installed is False


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


def test_a_restart_waits_for_the_service_to_actually_stop(monkeypatch):
    """SAP's Server Tools is a Tomcat and takes its time. A restart of it left the
    service stopped for good: the wait ran out while it was still Stopping, the start
    was refused with 1056 "already running" — which is what Windows says about any
    service that is not Stopped — and 1056 was on the list of errors treated as
    nothing to worry about. So the app reported success about a service it had just
    turned off."""
    states = iter(["Running", "Stopping", "Stopping", "Stopping", "Stopped"])
    started = []
    monkeypatch.setattr(scm_windows, "stop_service", lambda name, machine="": None)
    monkeypatch.setattr(scm_windows, "query_status",
                        lambda name, machine="": next(states, "Stopped"))
    monkeypatch.setattr(scm_windows, "start_service",
                        lambda name, machine="": started.append(name))
    monkeypatch.setattr(scm_windows.time, "sleep", lambda _s: None)

    scm_windows.restart_service("B1ServerTools64", "10.77.3.112")

    assert started == ["B1ServerTools64"], "started it before it had stopped"


def test_a_service_that_will_not_stop_is_reported_not_swallowed(monkeypatch):
    """And it must be an error the app cannot mistake for "nothing to do": leaving a
    service stopped while saying the restart worked is the worst outcome available,
    because nobody goes to look."""
    monkeypatch.setattr(scm_windows, "stop_service", lambda name, machine="": None)
    monkeypatch.setattr(scm_windows, "query_status",
                        lambda name, machine="": "Stopping")
    monkeypatch.setattr(scm_windows, "start_service",
                        lambda name, machine="": pytest.fail("started it anyway"))
    monkeypatch.setattr(scm_windows.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scm_windows, "STOP_WAIT", 1.0)

    with pytest.raises(RuntimeError) as raised:
        scm_windows.restart_service("B1ServerTools64", "10.77.3.112")

    said = str(raised.value)
    assert "still stopping" in said and "not been started again" in said
    assert scm_windows.nothing_to_do(raised.value) == "", \
        "a restart that failed would be logged as nothing to do"


def test_already_running_on_the_start_waits_again_rather_than_giving_up(monkeypatch):
    """The state read a moment ago can be out of date, so this is a real race rather
    than only a too-short budget."""
    states = iter(["Stopped", "Stopping", "Stopped"])
    attempts = []

    def start(name, machine=""):
        attempts.append(name)
        if len(attempts) == 1:
            raise FakeError(scm_windows.ALREADY_RUNNING, "already running")

    monkeypatch.setattr(scm_windows, "stop_service", lambda name, machine="": None)
    monkeypatch.setattr(scm_windows, "query_status",
                        lambda name, machine="": next(states, "Stopped"))
    monkeypatch.setattr(scm_windows, "start_service", start)
    monkeypatch.setattr(scm_windows, "pywintypes",
                        type("m", (), {"error": FakeError}))
    monkeypatch.setattr(scm_windows.time, "sleep", lambda _s: None)

    scm_windows.restart_service("B1ServerTools64", "10.77.3.112")

    assert len(attempts) == 2, "gave up on the first refusal"


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


def test_a_remote_machine_never_gets_this_computer_s_event_log(wnet, monkeypatch):
    """The original point of this test, and it still holds: `eventlog.read` opens the log
    with `OpenEventLog(None, ...)` — always *this* machine — so using it for another one
    served our own events under that service's name.

    What changed is the answer to "then what". It used to refuse; now it asks the target
    over WinRM. The thing that must never happen is unchanged.
    """
    from core import eventlog, winrm_windows

    def refuse(*a, **k):
        raise AssertionError("read this computer's event log for another machine")

    monkeypatch.setattr(eventlog, "read", refuse)
    monkeypatch.setattr(winrm_windows, "probe",
                        lambda host, user="", password="", **k: {"ok": True, "why": "",
                                                                "name": "CTL053"})
    monkeypatch.setattr(winrm_windows, "logs",
                        lambda host, service, lines=50, user="", password="":
                            [f"from {host}: {service} started"])
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    assert conn.abilities().logs is True, "WinRM can read it, so it is offered"
    assert conn.logs("MSSQLSERVER") == ["from 10.77.3.51: MSSQLSERVER started"]


def test_a_remote_machine_without_winrm_says_what_to_do(wnet, monkeypatch):
    """And when it cannot be read, the sentence has to be actionable — "not supported" was
    something nobody could do anything with."""
    from core import winrm_windows

    monkeypatch.setattr(
        winrm_windows, "probe",
        lambda host, user="", password="", **k: {
            "ok": False, "name": "",
            "why": "On that machine, as an administrator:  winrm quickconfig"})
    conn = scm_windows.WindowsConnector("ctl053", _machine())

    can = conn.abilities()
    assert can.logs is False
    assert "event log read" in can.why
    assert "winrm quickconfig" in can.why


def test_this_computer_still_reads_its_own(monkeypatch):
    local = scm_windows.WindowsConnector("")
    monkeypatch.setattr("core.eventlog.read",
                        lambda *a, **k: [{"ts": "t", "level": "Information",
                                          "summary": "started", "message": ""}])

    assert local.abilities().logs is True
    assert local.logs("Dnscache") == ["t  Information  started"]


def test_a_file_on_another_windows_machine_is_read_over_its_admin_share(monkeypatch,
                                                                       tmp_path):
    r"""Measured: C$ answered in 18 ms over the session already established to IPC$.
    So a File check on a remote Windows service is a path translation — C:\x becomes
    \\host\C$\x — and needs nothing installed on the target."""
    import os
    import time as _t

    beat = tmp_path / "heartbeat.txt"
    beat.write_text("alive", encoding="utf-8")
    os.utime(beat, (_t.time() - 120, _t.time() - 120))

    conn = scm_windows.WindowsConnector("ctl053", _machine())
    monkeypatch.setattr(conn, "_sign_in", lambda: None)
    # The translation is the whole trick; point it at the real temp file.
    monkeypatch.setattr(scm_windows, "_admin_share_path",
                        lambda host, path: str(beat))

    exists, age = conn.stat(r"D:\b1\heartbeat.txt")

    assert exists is True
    assert 110 < age < 200


def test_the_admin_share_translation_is_what_you_would_type():
    assert scm_windows._admin_share_path("10.0.0.9", r"C:\b1\beat.txt") == \
        r"\\10.0.0.9\C$\b1\beat.txt"
    assert scm_windows._admin_share_path("host", r"D:\logs\x.log") == \
        r"\\host\D$\logs\x.log"
    # A path that is not a local drive letter cannot be reached this way.
    assert scm_windows._admin_share_path("host", r"\\already\unc") == ""
    assert scm_windows._admin_share_path("host", "relative\\path") == ""


def test_a_missing_file_on_another_machine_is_absent_not_an_error(monkeypatch):
    conn = scm_windows.WindowsConnector("ctl053", _machine())
    monkeypatch.setattr(conn, "_sign_in", lambda: None)
    monkeypatch.setattr(scm_windows, "_admin_share_path",
                        lambda host, path: r"\ctl053\C$\nope\missing.txt")

    exists, age = conn.stat(r"C:\nope\missing.txt")

    assert exists is False and age == 0.0


def test_a_closed_smb_port_names_that_as_the_problem(monkeypatch):
    """The first thing that has to work. If 445 is shut, nothing else matters and
    the message says so rather than blaming the service manager."""
    said = scm_windows.diagnose("10.77.3.110",
                                port_open=lambda host, port: False)
    assert "445" in said and "SMB" in said
    assert "first thing" in said


def test_an_open_port_but_dead_scm_names_the_firewall_rule(monkeypatch):
    """The common one, and the one whose native error tells you nothing. 445 is up,
    the service manager is not, so the Remote Service Management rule is off."""
    monkeypatch.setattr(scm_windows, "reachable", lambda host: False)
    said = scm_windows.diagnose("10.77.3.110",
                                port_open=lambda host, port: True)
    assert "Remote Service Management" in said
    assert "File and Printer Sharing" in said
    assert "RPC server is unavailable" in said


def test_a_machine_that_answers_has_nothing_to_diagnose(monkeypatch):
    monkeypatch.setattr(scm_windows, "reachable", lambda host: True)
    assert scm_windows.diagnose("10.77.3.112",
                                port_open=lambda host, port: True) == ""
