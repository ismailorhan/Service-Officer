"""What `ServiceOfficerHub.exe` does with each way of being started.

This file exists because of a bug it would have caught. The service control manager starts
a service by launching the exe **with no arguments** — nothing on the command line says
which service it is — and the process has about thirty seconds to connect back. hub.py fell
through that case to pywin32's `HandleCommandLine`, which found no command, printed usage
and exited. Windows reports that as error 1053, "the service did not respond in a timely
fashion", which says nothing about the cause, and it only happens on a machine where the
service is really installed — so no amount of running `--console` would have found it.

The dispatcher itself cannot be tested here (it only works when the SCM is the parent, and
fails with error 1063 anywhere else). What is tested is that this argument list reaches it
rather than something else.
"""

import sys

import pytest

import hub


@pytest.fixture
def routes(monkeypatch):
    """Every exit from main(), replaced by a note of which one was taken."""
    taken = []
    monkeypatch.setattr(hub, "_dispatch", lambda: taken.append("dispatch") or 0)
    monkeypatch.setattr(hub, "_console", lambda: taken.append("console") or 0)
    monkeypatch.setattr(hub, "_apply_recovery",
                        lambda: taken.append("recovery"))
    monkeypatch.setattr(hub.hub_auth, "ensure_certificate",
                        lambda path: taken.append("certificate")
                        or ("hub.pem", "SHA256:x"))

    class FakeServiceUtil:
        @staticmethod
        def HandleCommandLine(cls, *_a, **_k):
            taken.append("commandline")

    monkeypatch.setitem(sys.modules, "win32serviceutil", FakeServiceUtil)
    monkeypatch.setattr(hub, "_service_class", lambda: object)
    return taken


def run(routes, *args) -> list:
    """main() with this argv, returning which route it took."""
    import unittest.mock
    with unittest.mock.patch.object(sys, "argv", ["ServiceOfficerHub.exe", *args]):
        assert hub.main() == 0
    return routes


def test_no_arguments_is_the_service_control_manager_starting_us(routes):
    """The bug. This is the only way a service ever actually starts, and it used to end
    up in HandleCommandLine printing usage."""
    assert run(routes) == ["dispatch"]


def test_install_registers_and_then_sets_the_recovery_policy(routes):
    """The recovery policy is the layer that survives the process being gone entirely, so
    it has to be applied by the same command that registers the service — nobody is going
    to run a second one."""
    assert run(routes, "--startup", "auto", "install") == ["commandline", "recovery"]


def test_the_other_service_commands_are_left_to_pywin32(routes):
    for command in ("start", "stop", "remove", "restart"):
        routes.clear()
        assert run(routes, command) == ["commandline"], command


def test_console_runs_it_here(routes):
    assert run(routes, "--console") == ["console"]


def test_fingerprint_prints_and_stops(routes, capsys):
    assert run(routes, "--fingerprint") == ["certificate"]
    assert "SHA256:x" in capsys.readouterr().out


def test_the_client_commands_never_reach_the_service_framework(routes, monkeypatch):
    """`client add` on a machine where the service framework is missing still has to
    work — it is how a token is issued, and it is the first thing anybody runs."""
    monkeypatch.setattr(hub.hub_auth, "add_client", lambda name: "a-token")
    monkeypatch.setattr(hub.cfg_mod, "load", lambda path=None: hub.cfg_mod.Config())

    took = run(routes, "client", "add", "ismail-laptop")

    assert "commandline" not in took and "dispatch" not in took


def test_a_stray_word_is_pywin32s_problem_not_a_silent_dispatch(routes):
    """Something unrecognised must not be treated as "the SCM started us": that would
    hang for thirty seconds and then report a timeout, instead of printing usage."""
    assert run(routes, "wibble") == ["commandline"]


def test_pair_local_writes_the_machines_copy_not_the_installers(tmp_path, monkeypatch):
    """`client pair --local` runs from the installer, as whoever is installing. If it
    writes that account's own client.json — which is where a client's settings now live —
    then the second person to sign into that server is asked for a token nobody has, and
    the whole point of the command is gone.

    It was exactly that for one release. Caught by looking at where the files landed on a
    real install.
    """
    from core import config as cfg_mod
    from core import hub_auth, local, secrets

    machine_json = tmp_path / "ProgramData" / "client.json"
    user_json = tmp_path / "user" / "client.json"
    monkeypatch.setattr(local, "MACHINE_PATH", str(machine_json))
    monkeypatch.setattr(local, "PATH", str(user_json))
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "machine.dat"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))
    monkeypatch.setattr(hub_auth, "add_client", lambda name: "issued-once")
    monkeypatch.setattr(hub_auth, "ensure_certificate",
                        lambda path: ("hub.pem", "SHA256:pinned"))
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())

    assert hub._client_command(["hub.exe", "client", "pair", "--local"]) == 0

    assert machine_json.exists(), "the machine-wide pairing was not written"
    assert not user_json.exists(), "wrote it into the installing account's profile"
    settings = local.load()
    assert settings.hub_url.startswith("https://")
    assert settings.hub_fingerprint == "SHA256:pinned"
    # And the token is readable by a user who has none of their own.
    assert local.token(settings.hub_url) == "issued-once"
    assert secrets.get(local._token_ref(settings.hub_url),
                       path=str(tmp_path / "user.dat")) == ""


# ---------------------------------------------------------------------------
# the port
# ---------------------------------------------------------------------------
def test_the_port_can_be_read_and_set(tmp_path, monkeypatch, capsys):
    """The installer has to be able to choose the port without parsing JSON, and a person
    has to be able to ask what it is without opening a file."""
    from core import config as cfg_mod

    path = str(tmp_path / "services.json")
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", path)

    assert hub._port_command(["hub.exe", "port"]) == 0
    assert capsys.readouterr().out.strip() == "8797"      # the default

    assert hub._port_command(["hub.exe", "port", "9100"]) == 0
    assert "9100" in capsys.readouterr().out
    assert cfg_mod.load().hub.port == 9100

    assert hub._port_command(["hub.exe", "port"]) == 0
    assert capsys.readouterr().out.strip() == "9100"


def test_a_port_out_of_range_is_refused_where_somebody_can_see_it(tmp_path,
                                                                 monkeypatch, capsys):
    """Silently clamping would give a hub nobody can find on the port they chose."""
    from core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(tmp_path / "services.json"))

    for bad in ("0", "70000", "-1", "eight"):
        assert hub._port_command(["hub.exe", "port", bad]) == 1, bad
        assert capsys.readouterr().out.strip() != ""
    assert cfg_mod.load().hub.port == 8797


def test_the_port_command_never_reaches_the_service_framework(routes, tmp_path,
                                                              monkeypatch):
    """It is a console command like `client add`, and it has to work on a machine where
    the service framework is not importable."""
    from core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(tmp_path / "services.json"))

    took = run(routes, "port", "9000")

    assert "commandline" not in took and "dispatch" not in took
