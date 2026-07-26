"""The transport seam: does everything above it really not care what is below?

These tests use a connector that is not Windows at all. If they pass, a Linux
target is a new file rather than an edit to the watchdog, the health monitor and
four pages.
"""

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
