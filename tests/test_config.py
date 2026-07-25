import json

from core import config as cfg


def test_v1_file_loads_and_gains_defaults(tmp_path):
    """An existing install must keep working: v1 had no recovery/stacks, and
    early versions even stored bare service names."""
    p = tmp_path / "services.json"
    p.write_text(json.dumps({
        "services": ["Spooler", {"name": "AppEngine", "label": "CompuTec AppEngine"}],
        "auto_start": False,
    }), encoding="utf-8")

    c = cfg.load(str(p))

    assert [s.name for s in c.services] == ["Spooler", "AppEngine"]
    assert c.service("Spooler").label == "Spooler"          # promoted from string
    assert c.service("AppEngine").label == "CompuTec AppEngine"
    assert c.service("AppEngine").recovery.enabled is False  # default
    assert c.service("AppEngine").recovery.max_attempts == 3
    assert c.auto_start is False
    assert c.stacks == []
    assert c.history.enabled is True


def test_round_trip_preserves_everything(tmp_path):
    p = tmp_path / "services.json"
    c = cfg.Config(
        services=[cfg.Service(name="AppEngine", label="CompuTec AppEngine",
                              recovery=cfg.Recovery(enabled=True, max_attempts=5,
                                                    delay_seconds=7, backoff=1.5,
                                                    restart_on_clean_stop=True))],
        stacks=[cfg.Stack(name="SAP B1", steps=[
            cfg.Step(service="MSSQLSERVER", wait="running", timeout_seconds=120),
            cfg.Step(service="AppEngine", wait="delay", delay_seconds=15),
        ])],
        history=cfg.History(enabled=False, retention_days=7),
        auto_start=False,
    )
    cfg.save(c, str(p))
    back = cfg.load(str(p))

    assert back.version == cfg.CURRENT_VERSION
    r = back.service("AppEngine").recovery
    assert (r.enabled, r.max_attempts, r.delay_seconds, r.backoff) == (True, 5, 7, 1.5)
    assert r.restart_on_clean_stop is True
    st = back.stack("SAP B1")
    assert [s.service for s in st.steps] == ["MSSQLSERVER", "AppEngine"]
    assert st.steps[1].wait == "delay" and st.steps[1].delay_seconds == 15
    assert back.history.retention_days == 7
    assert back.auto_start is False


def test_corrupt_file_does_not_stop_the_app(tmp_path):
    p = tmp_path / "services.json"
    p.write_text("{ this is not json", encoding="utf-8")
    c = cfg.load(str(p))
    assert c.services == []          # empty, not an exception


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "services.json"
    cfg.save(cfg.Config(services=[cfg.Service(name="A")]), str(p))
    assert p.exists()
    assert [f.name for f in tmp_path.iterdir()] == ["services.json"]


def test_nonsense_values_are_clamped(tmp_path):
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"services": [{
        "name": "A",
        "recovery": {"max_attempts": "lots", "delay_seconds": -5, "backoff": 0.1,
                     "flap_threshold": 1, "flap_window_minutes": 0},
    }]}), encoding="utf-8")

    r = cfg.load(str(p)).service("A").recovery
    assert r.max_attempts == 3        # unparseable -> default
    assert r.delay_seconds == 0       # negative -> floor
    assert r.backoff >= 1.0           # a backoff below 1 would shrink the wait
    assert r.flap_threshold >= 2
    assert r.flap_window_minutes >= 1


def test_backoff_schedule_and_cap():
    r = cfg.Recovery(delay_seconds=10, backoff=2.0)
    assert [r.delay_for(n) for n in (1, 2, 3, 4)] == [10.0, 20.0, 40.0, 80.0]
    assert r.delay_for(20, cap_seconds=300) == 300.0
