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
