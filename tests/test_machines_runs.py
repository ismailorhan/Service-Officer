"""Machines, execution records, and the disabled-service case."""

import json

from core import config as cfg_mod
from core import history
from core import state as st


# ── machines ───────────────────────────────────────────────────────────────
def test_this_computer_always_exists(tmp_path):
    """Every service belongs to a machine, so the local one can't be missing."""
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"services": [{"name": "AppEngine"}]}), encoding="utf-8")

    cfg = cfg_mod.load(str(p))
    assert [m.name for m in cfg.machines] == [cfg_mod.LOCAL_MACHINE]
    assert cfg.machines[0].is_local
    assert cfg.service("AppEngine").machine == cfg_mod.LOCAL_MACHINE
    assert cfg.machine_label("") == "This computer"


def test_a_machine_named_by_a_service_is_adopted(tmp_path):
    """A service pointing at a machine nobody listed must not dangle."""
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"services": [
        {"name": "AppEngine", "machine": "SQLBOX"}]}), encoding="utf-8")

    cfg = cfg_mod.load(str(p))
    assert [m.name for m in cfg.machines] == ["", "SQLBOX"]
    assert cfg.machine_label("SQLBOX") == "SQLBOX"


def test_machines_round_trip(tmp_path):
    p = str(tmp_path / "services.json")
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(), cfg_mod.Machine(name="SQLBOX", label="SQL box")],
        services=[cfg_mod.Service(name="MSSQLSERVER", machine="SQLBOX")])
    cfg_mod.save(cfg, p)
    back = cfg_mod.load(p)
    assert back.machine_label("SQLBOX") == "SQL box"
    assert back.service("MSSQLSERVER", "SQLBOX") is not None


# ── stack visibility ───────────────────────────────────────────────────────
def test_stack_visibility_round_trips(tmp_path):
    p = str(tmp_path / "services.json")
    cfg_mod.save(cfg_mod.Config(stacks=[
        cfg_mod.Stack(name="shown"),
        cfg_mod.Stack(name="hidden", show_in_flyout=False)]), p)
    back = cfg_mod.load(p)
    assert back.stack("shown").show_in_flyout is True
    assert back.stack("hidden").show_in_flyout is False


# ── executions ─────────────────────────────────────────────────────────────
def test_runs_are_recorded_with_outcome_and_duration(tmp_path):
    p = str(tmp_path / "h.jsonl")
    history.record_run("stack", "SAP B1", "success", seconds=31.4,
                       detail="2 steps", source=st.SRC_STACK, path=p)
    history.record_run("trigger", "nightly", "skipped",
                       detail="AppEngine was already running",
                       source=st.SRC_SCHEDULE, path=p)

    rows = history.runs(path=p)
    assert [r["outcome"] for r in rows] == ["skipped", "success"]   # newest first
    assert rows[1]["seconds"] == 31.4
    assert history.runs(path=p, kind="trigger")[0]["name"] == "nightly"


def test_runs_show_up_in_the_timeline_with_a_readable_source(tmp_path):
    p = str(tmp_path / "h.jsonl")
    history.record_run("trigger", "nightly", "failed", seconds=5,
                       detail="could not start", source=st.SRC_SCHEDULE, path=p)
    row = next(r for r in history.query(service_names=[], path=p)
               if r["kind"] == "run")
    assert row["event"] == "trigger failed"
    assert row["source"] == "scheduled trigger"
    assert row["level"] == "Error"
    assert "could not start" in row["detail"] and "5" in row["detail"]


def test_a_skipped_run_is_a_warning_not_an_error(tmp_path):
    p = str(tmp_path / "h.jsonl")
    history.record_run("trigger", "t", "skipped", source=st.SRC_SCHEDULE, path=p)
    row = next(r for r in history.query(service_names=[], path=p)
               if r["kind"] == "run")
    assert row["level"] == "Warning"


# ── disabled services ──────────────────────────────────────────────────────
def test_store_tracks_start_type(tmp_path):
    store = st.Store()
    store.update("AppEngine", st.STOPPED)
    assert store.is_disabled("AppEngine") is False

    store.set_start_type("AppEngine", "Disabled")
    assert store.is_disabled("AppEngine") is True
    assert store.start_type("AppEngine") == "Disabled"

    store.set_start_type("AppEngine", "Automatic")
    assert store.is_disabled("AppEngine") is False


def test_windows_refusing_a_no_op_is_not_a_failure():
    """Stopping a stopped service raises "The service has not been started".
    Reporting that as an error is what made a bulk stop pop a warning about a
    service that was already where it was asked to be."""
    from core import control

    class Refusal(Exception):
        def __init__(self, code):
            self.winerror = code

    assert control.nothing_to_do(Refusal(1062)) == "it is already stopped"
    assert control.nothing_to_do(Refusal(1056)) == "it is already running"
    assert control.nothing_to_do(Refusal(1058)) == "it is disabled in Windows"
    assert control.nothing_to_do(Refusal(5)) == ""          # access denied is real
    assert control.nothing_to_do(RuntimeError("boom")) == ""


def test_reading_a_real_start_type():
    """Against the live SCM: a well-known service reports a sensible type."""
    from core import control
    assert control.start_type("Spooler") in (
        "Automatic", "Manual", "Disabled", "Boot", "System", "")
    assert control.start_type("NoSuchServiceHere") == ""
