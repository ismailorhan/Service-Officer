"""The engine has to run with nothing on screen.

If it does not, none of the hub can live in a service — so the test builds one in a
process with no QApplication and drives it through its callbacks, the way the hub
will. Everything here is the behaviour that used to live inside the Application
object, checked now that it lives on its own.
"""

import sys

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import state as st


def _quiet_control(monkeypatch, status=st.RUNNING):
    """The service manager, replaced: the point is the engine's wiring, not a real
    restart, and a test that touches a real service cannot run on a build agent."""
    monkeypatch.setattr(engine_mod.control, "query_status",
                        lambda name, machine="": status)
    monkeypatch.setattr(engine_mod.control, "start_type",
                        lambda name, machine="": "Automatic")
    for verb in ("start_service", "stop_service", "restart_service"):
        monkeypatch.setattr(engine_mod.control, verb, lambda name, machine="": None)


def test_the_engine_imports_without_qt():
    """No PySide6 anywhere in it. A service has no display, and an import of Qt would
    pull one in."""
    source = engine_mod.__file__
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "PySide6" not in text
    assert "import Qt" not in text


def test_it_builds_and_exposes_the_core(monkeypatch):
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    built = engine_mod.Engine(lambda: cfg, store=st.Store())

    for part in ("poller", "health", "watchdog", "scheduler", "runner", "watcher"):
        assert getattr(built, part, None) is not None, f"no {part}"
    assert callable(built.store.status_of)


def test_an_action_is_accepted_and_reported(monkeypatch):
    """The engine takes an action by name and answers with an id, so a caller in
    another process can be told what happened to the one it asked for."""
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    done = []
    built = engine_mod.Engine(lambda: cfg, store=st.Store(),
                              on_action_done=lambda **facts: done.append(facts))
    _quiet_control(monkeypatch)

    action_id = built.act("restart", "Dnscache", actor="tests")

    assert isinstance(action_id, str) and action_id
    assert built.wait_for_actions(timeout=5)
    assert done and done[0]["id"] == action_id
    assert done[0]["error"] is None and done[0]["status"] == st.RUNNING
    assert done[0]["actor"] == "tests"


def test_the_store_it_exposes_satisfies_the_read_api():
    cfg = cfg_mod.Config()
    built = engine_mod.Engine(lambda: cfg, store=st.Store())
    missing = [n for n in st.READ_API
               if not callable(getattr(built.store, n, None))]
    assert missing == []


def test_two_people_cannot_act_on_one_service_at_once(monkeypatch):
    """Five clients, one landscape. Two restarts interleaving on one service is a
    service stopped twice and started once, and the second person has no idea."""
    import threading

    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    gate = threading.Event()
    monkeypatch.setattr(engine_mod.control, "restart_service",
                        lambda name, machine="": gate.wait(5))
    monkeypatch.setattr(engine_mod.control, "query_status",
                        lambda name, machine="": st.RUNNING)
    built = engine_mod.Engine(lambda: cfg, store=st.Store())

    built.act("restart", "Dnscache", actor="ismail")
    with pytest.raises(engine_mod.Busy) as raised:
        built.act("restart", "Dnscache", actor="ayse")
    assert "ismail" in str(raised.value)

    gate.set()
    assert built.wait_for_actions(timeout=10)
    # Once it is over, the next person may act.
    built.act("restart", "Dnscache", actor="ayse")
    assert built.wait_for_actions(timeout=5)


def test_a_listener_that_raises_does_not_take_the_engine_down(monkeypatch):
    """A callback belongs to whoever is watching; the engine has services to look
    after and must survive a bad one."""
    cfg = cfg_mod.Config(services=[cfg_mod.Service(name="Dnscache")])
    _quiet_control(monkeypatch)
    built = engine_mod.Engine(
        lambda: cfg, store=st.Store(),
        on_action_done=lambda **_f: (_ for _ in ()).throw(ValueError("boom")))

    built.act("restart", "Dnscache", actor="tests")
    assert built.wait_for_actions(timeout=5)     # did not raise out of the thread


def test_paramikos_tracebacks_do_not_fill_the_log():
    """A SUSE box that is switched off makes paramiko log "Error reading SSH protocol
    banner" at ERROR with a full stack, on every retry. The poller already reports that
    machine as not answering, in one sentence, on its row — and the log has to stay
    readable enough to answer "why did it restart at 04:12"."""
    import logging

    from core import applog

    applog.setup()

    quiet = logging.getLogger("paramiko.transport")
    assert quiet.level >= logging.CRITICAL
    assert quiet.propagate is False
