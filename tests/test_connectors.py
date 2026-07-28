"""The transport seam: does everything above it really not care what is below?

These tests use a connector that is not Windows at all. If they pass, a Linux
target is a new file rather than an edit to the watchdog, the health monitor and
four pages.
"""

import pytest

from core import connectors
from core import control
from core import state as st


class FakeConnector:
    """A target that is nothing like the SCM: no kill, no push, no sudo."""

    def __init__(self, machine=""):
        self.machine = machine
        self.calls = []
        self.state = st.STOPPED

    def abilities(self):
        return connectors.Abilities(control=False, kill=False, logs=True,
                                    push=False, why="no sudo on that account")

    def reachable(self):
        return True

    def list_services(self):
        return [connectors.ServiceInfo(name="b1s50000.service",
                                       display="SAP Business One Service Layer",
                                       status=self.state, start_type="enabled"),
                connectors.ServiceInfo(name="b1s50001.service", display="broken",
                                       status=st.STOPPED, installed=False)]

    def status(self, name):
        return connectors.Status(state=self.state, sub_state="dead",
                                 start_type="enabled", pid=1765,
                                 installed=name != "b1s50001.service")

    def start(self, name):
        self.calls.append(("start", name))
        self.state = st.RUNNING

    def stop(self, name):
        self.calls.append(("stop", name))
        self.state = st.STOPPED

    def restart(self, name):
        self.calls.append(("restart", name))

    def kill(self, name):
        raise RuntimeError("nope")

    def logs(self, name, lines=50):
        return [f"journal line for {name}"]

    def run(self, command, timeout=10.0):
        return 0, f"ran {command}"

    def stat(self, path):
        return True, 12.5


def use_fake(monkeypatch, machine="hanadev"):
    fake = FakeConnector(machine)
    monkeypatch.setattr(connectors, "for_machine",
                        lambda m="", config=None: fake)
    return fake


def test_control_speaks_to_whatever_connector_answers(monkeypatch):
    """The public functions everything already calls must route, not assume."""
    fake = use_fake(monkeypatch)

    assert control.query_status("b1s50000.service", "hanadev") == st.STOPPED
    control.start_service("b1s50000.service", "hanadev")
    assert control.query_status("b1s50000.service", "hanadev") == st.RUNNING
    control.stop_service("b1s50000.service", "hanadev")
    control.restart_service("b1s50000.service", "hanadev")

    assert fake.calls == [("start", "b1s50000.service"),
                          ("stop", "b1s50000.service"),
                          ("restart", "b1s50000.service")]


def test_the_picker_gets_the_shape_it_has_always_had(monkeypatch):
    """list_all_services still returns dicts, because that is what the settings
    picker was written against — the seam must not leak into the UI."""
    use_fake(monkeypatch)

    found = control.list_all_services("hanadev")

    assert found[0] == {"name": "b1s50000.service",
                        "display": "SAP Business One Service Layer",
                        "status": st.STOPPED}


def test_a_target_can_say_what_it_cannot_do(monkeypatch):
    """An account without sudo is a monitoring target, not an error."""
    use_fake(monkeypatch)

    can = control.abilities("hanadev")

    assert can.control is False and can.kill is False
    assert "sudo" in can.why


def test_a_broken_unit_is_not_merely_stopped(monkeypatch):
    """systemd reports units it cannot load. Calling that "Stopped" would have
    the watchdog trying to start something that can never start."""
    use_fake(monkeypatch)

    assert control.status_of("b1s50000.service", "hanadev").installed is True
    assert control.status_of("b1s50001.service", "hanadev").installed is False


def test_start_type_and_pid_come_from_one_query(monkeypatch):
    fake = use_fake(monkeypatch)

    assert control.start_type("x", "hanadev") == "enabled"
    assert control.process_id("x", "hanadev") == 1765
    assert control.status_of("x", "hanadev").sub_state == "dead"


def test_the_windows_connector_is_still_what_a_local_target_gets():
    """No monkeypatching here: the real registry must answer for this computer."""
    connectors.forget()
    got = connectors.for_machine("")

    assert type(got).__name__ == "WindowsConnector"
    assert got.abilities().kill is True          # local, so terminating is ours
    assert got.abilities().push is True          # and the SCM pushes changes


# -- routing by transport ---------------------------------------------------
def test_a_linux_machine_is_reached_over_ssh_and_a_windows_one_is_not():
    """The whole seam in one test: what a machine *is* decides how it is reached,
    and that decision lives in exactly one place."""
    from core import config as cfg_mod
    from core import ssh_linux

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(),                                     # this computer
        cfg_mod.Machine(name="CTL052", kind="windows"),
        cfg_mod.Machine(name="hanadev", kind="linux", address="192.168.230.2",
                        username="svcofficer", host_fingerprint="SHA256:x"),
    ])
    connectors.use_config(lambda: cfg)
    try:
        assert type(connectors.for_machine("")).__name__ == "WindowsConnector"
        assert type(connectors.for_machine("CTL052")).__name__ == "WindowsConnector"
        assert isinstance(connectors.for_machine("hanadev"),
                          ssh_linux.LinuxConnector)
    finally:
        connectors.use_config(None)


def test_the_local_machine_stays_windows_however_the_file_is_edited():
    """There is no SSH transport to ourselves, and a hand-edited config must not
    be able to make this computer unreachable."""
    from core import config as cfg_mod

    cfg = cfg_mod.from_dict({"machines": [{"name": "", "kind": "linux"}]})

    assert cfg.machine("").kind == "windows"
    assert cfg.machine("").auth == "current_user"


def test_a_linux_machine_defaults_to_key_authentication():
    """The Windows token means nothing on a Linux box, so the sensible default
    differs by transport rather than being one value for both."""
    from core import config as cfg_mod

    cfg = cfg_mod.from_dict({"machines": [{"name": "hanadev", "kind": "linux"}]})
    machine = cfg.machine("hanadev")

    assert machine.auth == "key"
    assert machine.poll_seconds == 5
    assert machine.where() == "hanadev"


def test_machine_transport_settings_survive_a_save_and_load():
    from core import config as cfg_mod

    cfg = cfg_mod.Config(machines=[
        cfg_mod.Machine(name="hanadev", label="SUSE dev", kind="linux",
                        address="192.168.230.2", port=2222, auth="key",
                        username="svcofficer", key_path=r"C:\keys\id_ed25519",
                        host_fingerprint="SHA256:H+WZyx", poll_seconds=10)])
    again = cfg_mod.from_dict(cfg_mod.to_dict(cfg)).machine("hanadev")

    assert (again.kind, again.address, again.port) == ("linux", "192.168.230.2", 2222)
    assert again.username == "svcofficer" and again.host_fingerprint == "SHA256:H+WZyx"
    assert again.poll_seconds == 10
    assert again.where() == "192.168.230.2:2222"


def test_nonsense_in_the_machines_section_is_repaired_not_obeyed():
    from core import config as cfg_mod

    cfg = cfg_mod.from_dict({"machines": [
        {"name": "x", "kind": "solaris", "auth": "telepathy",
         "port": 999999, "poll_seconds": 0}]})
    machine = cfg.machine("x")

    assert machine.kind == "windows"          # an unknown transport is not usable
    assert machine.auth == "current_user"
    assert machine.port == 65535
    assert machine.poll_seconds == 2          # a floor, or we would poll flat out


def test_forgetting_a_machine_closes_what_it_had_open():
    """An SSH session left open to where a machine used to be would keep
    answering for it after the user repointed it."""
    closed = []

    class Runner:
        def close(self):
            closed.append(True)

        def __call__(self, command, timeout=15.0):
            return 0, ""

    from core import config as cfg_mod
    from core import ssh_linux

    conn = ssh_linux.LinuxConnector(cfg_mod.Machine(name="hanadev", kind="linux"),
                                    runner=Runner())
    connectors._cache["hanadev"] = conn

    connectors.forget("hanadev")

    assert closed == [True]
    assert "hanadev" not in connectors._cache


def test_a_caller_that_knows_the_machine_can_say_so():
    """The bug this closes: the panel edits a *copy* of the config, and the
    standalone panel wires no registry at all — so a Linux machine chosen in the
    service picker was reached through the Windows service manager and answered
    "(1722, 'OpenSCManager', 'The RPC server is unavailable.')".
    """
    from core import config as cfg_mod
    from core import control, ssh_linux

    connectors.use_config(None)                  # as the standalone panel is
    connectors.forget()
    unsaved = cfg_mod.Machine(name="hanadev", kind="linux",
                              address="192.168.230.2", username="root")
    try:
        # Without the record: nothing known about it, so the Windows default.
        assert type(connectors.for_machine("hanadev")).__name__ == "WindowsConnector"
        connectors.forget()
        # With it: the right transport, before anything has been saved.
        assert isinstance(connectors.for_machine("hanadev", unsaved),
                          ssh_linux.LinuxConnector)
        connectors.forget()
        # And through the public API the picker actually calls.
        listed = []
        conn = connectors.for_machine("hanadev", unsaved)
        conn._run = lambda cmd, timeout=15.0: (listed.append(cmd), (0, "[]"))[1]
        control.list_all_services("hanadev", unsaved)
        assert any("systemctl" in c for c in listed), listed
    finally:
        connectors.forget()


# ---------------------------------------------------------------------------
# WinRM: the three things SMB cannot do on another Windows machine
# ---------------------------------------------------------------------------
def test_a_remote_machine_is_asked_what_it_can_do(monkeypatch):
    """It used to be told. `local = not self.machine` meant a remote machine was reported
    unable to do three things without anybody having tried — and `sc` and the admin shares
    were already proving that some things *do* work across that boundary."""
    from core import config as cfg_mod
    from core import scm_windows, winrm_windows

    asked = []
    monkeypatch.setattr(winrm_windows, "probe",
                        lambda host, user="", password="", **k:
                            asked.append(host) or {"ok": True, "why": "", "name": "SC-SQL"})
    record = cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows")
    can = scm_windows.WindowsConnector("sc-sql", record).abilities()

    assert asked == ["10.77.3.112"], "nothing asked the machine"
    assert (can.kill, can.logs, can.command_check) == (True, True, True)
    assert can.file_check is True
    # Still polled: there is no doorbell for a remote service manager.
    assert can.push is False
    assert "polled" in can.why


def test_without_winrm_it_says_what_is_missing_and_what_to_do(monkeypatch):
    """A refusal nobody can act on is worse than no feature. The reason travels from the
    probe into the sentence the panel shows."""
    from core import config as cfg_mod
    from core import scm_windows, winrm_windows

    monkeypatch.setattr(
        winrm_windows, "probe",
        lambda host, user="", password="", **k: {
            "ok": False, "name": "",
            "why": "On that machine, as an administrator:  winrm quickconfig"})
    record = cfg_mod.Machine(name="sc-sap", address="10.77.3.110", kind="windows")
    can = scm_windows.WindowsConnector("sc-sap", record).abilities()

    assert (can.kill, can.logs, can.command_check) == (False, False, False)
    assert can.control is True and can.file_check is True, \
        "control and File ride the IPC$ session and are not affected"
    assert "winrm quickconfig" in can.why


def test_this_computer_is_never_asked_about_winrm(monkeypatch):
    """Everything WinRM would provide is already available locally and cheaper — and a
    PowerShell process per question would be 100 ms for nothing."""
    from core import scm_windows, winrm_windows

    monkeypatch.setattr(winrm_windows, "probe",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("asked WinRM about this computer")))
    can = scm_windows.WindowsConnector("", None).abilities()

    assert (can.kill, can.logs, can.command_check, can.push) == (True, True, True, True)
    assert can.why == ""


def test_a_remote_kill_goes_by_process_id(monkeypatch):
    """By id, not by name: the service manager has already said which process this service
    is, and killing by name on a machine running two of them is a different, worse thing."""
    from core import config as cfg_mod
    from core import scm_windows, winrm_windows

    killed = []
    monkeypatch.setattr(scm_windows, "process_id", lambda name, machine="": 4242)
    monkeypatch.setattr(winrm_windows, "kill",
                        lambda host, pid, user="", password="":
                            killed.append((host, pid)) or (True, ""))
    record = cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows")

    pid = scm_windows.WindowsConnector("sc-sql", record).kill("MSSQLSERVER")

    assert pid == 4242
    assert killed == [("10.77.3.112", 4242)]


def test_a_remote_kill_that_fails_says_why(monkeypatch):
    from core import config as cfg_mod
    from core import scm_windows, winrm_windows

    monkeypatch.setattr(scm_windows, "process_id", lambda name, machine="": 7)
    monkeypatch.setattr(winrm_windows, "kill",
                        lambda *a, **k: (False, "10.77.3.112 refused the account."))
    record = cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows")

    with pytest.raises(RuntimeError) as raised:
        scm_windows.WindowsConnector("sc-sql", record).kill("MSSQLSERVER")
    assert "refused the account" in str(raised.value)


def test_editing_a_machines_credentials_forgets_what_winrm_learned(monkeypatch):
    """The old password may have been the reason it did not work, or the reason it did."""
    from core import config as cfg_mod
    from core import scm_windows, winrm_windows

    forgotten = []
    monkeypatch.setattr(winrm_windows, "forget", forgotten.append)
    monkeypatch.setattr(scm_windows, "disconnect", lambda host: None)
    record = cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows",
                             auth="signed-in")

    scm_windows.WindowsConnector("sc-sql", record).forget()

    assert forgotten == ["10.77.3.112"]
