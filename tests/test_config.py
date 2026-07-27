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
            cfg.Step(service="MSSQLSERVER", wait="applied", timeout_seconds=120),
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


def test_the_data_directory_is_machine_wide_and_named_as_the_product(real_paths):
    """It describes the machine's services, so it cannot live in one profile.

    real_paths, not cfg.APP_DIR: conftest redirects those constants so the suite
    can't write into the installed app's directory, which is also what stops this
    test from reading them.
    """
    app_dir = real_paths["app_dir"]
    assert "ProgramData" in app_dir
    assert app_dir.endswith("Service Officer")
    # Both earlier homes are still read, newest first.
    legacy = real_paths["legacy_dirs"]
    assert any("ProgramData" in d and d.endswith("ServiceOfficer") for d in legacy)
    assert any("AppData" in d for d in legacy)


def test_a_per_user_install_is_carried_over_once(tmp_path):
    """An upgrade must never be the reason a service list disappears."""
    legacy = tmp_path / "appdata" / "ServiceOfficer"
    new = tmp_path / "programdata" / "ServiceOfficer"
    legacy.mkdir(parents=True)
    (legacy / "services.json").write_text('{"services": ["AppEngine"]}',
                                          encoding="utf-8")
    (legacy / "history.jsonl").write_text('{"ts": "x", "service": "AppEngine"}\n',
                                          encoding="utf-8")

    brought = cfg.migrate_from_legacy(str(new), [str(legacy)])
    assert sorted(brought) == ["history.jsonl", "services.json"]
    assert cfg.load(str(new / "services.json")).service("AppEngine")
    # The old copy is left where it was, so a rollback still has its data.
    assert (legacy / "services.json").exists()

    # A second run does nothing — and so cannot overwrite newer settings.
    (new / "services.json").write_text('{"services": ["WMSServer"]}',
                                       encoding="utf-8")
    assert cfg.migrate_from_legacy(str(new), [str(legacy)]) == []
    assert cfg.load(str(new / "services.json")).service("WMSServer")


def test_migration_never_overwrites_what_is_already_there(tmp_path):
    legacy = tmp_path / "old"
    new = tmp_path / "new"
    legacy.mkdir()
    new.mkdir()
    (legacy / "services.json").write_text('{"services": ["Old"]}', encoding="utf-8")
    (new / "services.json").write_text('{"services": ["New"]}', encoding="utf-8")

    assert cfg.migrate_from_legacy(str(new), [str(legacy)]) == []
    assert cfg.load(str(new / "services.json")).service("New")


def test_the_newest_of_several_old_homes_wins(tmp_path):
    """A machine that has been through both moves must end up with its most
    recent data, not whichever directory happened to be checked first."""
    newer = tmp_path / "programdata-old"      # v2.0.0, no space
    older = tmp_path / "appdata"              # v1.x, per user
    target = tmp_path / "programdata-current"
    for d in (newer, older):
        d.mkdir()
    (newer / "services.json").write_text('{"services": ["Recent"]}',
                                         encoding="utf-8")
    (older / "services.json").write_text('{"services": ["Ancient"]}',
                                         encoding="utf-8")
    (older / "history.jsonl").write_text("{}\n", encoding="utf-8")

    brought = cfg.migrate_from_legacy(str(target), [str(newer), str(older)])
    assert cfg.load(str(target / "services.json")).service("Recent")
    # The older home still contributes what the newer one didn't have.
    assert "history.jsonl" in brought


def test_the_build_number_counts_builds_not_commits(tmp_path, monkeypatch):
    """Several builds can come off one commit while something is being tried, and
    "which build is this" has to tell them apart."""
    import stamp_version
    monkeypatch.setattr(stamp_version, "COUNTER", tmp_path / ".build-number")

    assert stamp_version.next_build("2.0.0", release=False) == 1
    assert stamp_version.next_build("2.0.0", release=False) == 2
    assert stamp_version.next_build("2.0.0", release=False) == 3

    # A tagged release has no fourth part, and must not disturb the count.
    assert stamp_version.next_build("2.0.0", release=True) == 0
    assert stamp_version.next_build("2.0.0", release=False) == 4

    # A new release version restarts the numbering rather than carrying on.
    assert stamp_version.next_build("2.1.0", release=False) == 1
    assert stamp_version.next_build("2.1.0", release=False) == 2


def test_the_build_identifies_itself():
    """Three parts for a release, a fourth for the internal builds in between —
    2.0.0 is what a customer has, 2.0.0.7 is the seventh commit after it."""
    from core import version
    assert version.VERSION.count(".") == 2
    # An unstamped source checkout: the release number, and it says as much.
    assert version.short() == version.VERSION
    assert "running from source" in version.full()

    # Stamped, the way build.bat leaves it.
    version.COMMIT, version.BUILT, version.BUILD = "a1b2c3d", "2026-07-25 21:00", 7
    try:
        assert version.short() == f"{version.VERSION}.7"
        # The commit is in the about line, not in the version: it answers which
        # code, not which build.
        assert "a1b2c3d" in version.full() and "2026-07-25" in version.full()
        assert "a1b2c3d" not in version.short()

        version.BUILD = 0                    # the release itself
        assert version.short() == version.VERSION
    finally:
        version.COMMIT, version.BUILT, version.BUILD = "dev", "", 0


def test_the_hub_section_defaults_and_round_trips():
    """A config written before the hub existed has to load, and default to off —
    installing an update must never open a port on its own."""
    loaded = cfg.from_dict({"services": [{"name": "AppEngine"}]})
    assert loaded.hub.enabled is False
    assert loaded.hub.port == 8797
    assert loaded.hub.bind == ""

    loaded.hub.enabled = True
    loaded.hub.port = 9000
    loaded.hub.bind = "10.77.3.50"
    back = cfg.from_dict(cfg.to_dict(loaded))
    assert back.hub.enabled is True
    assert back.hub.port == 9000
    assert back.hub.bind == "10.77.3.50"


def test_a_silly_hub_port_is_refused():
    """Clamped the way poll_seconds is: a hand-edited file must not leave the service
    unable to start with nothing to say about why."""
    assert cfg.from_dict({"hub": {"port": 70000}}).hub.port == 8797
    assert cfg.from_dict({"hub": {"port": 0}}).hub.port == 8797
    assert cfg.from_dict({"hub": {"port": "nonsense"}}).hub.port == 8797
    assert cfg.from_dict({"hub": {"port": 443}}).hub.port == 443


def test_in_app_dir_puts_a_file_beside_services_json():
    """The hub's certificate belongs to the installation, not to the build — a build
    replaces its own directory."""
    where = cfg.in_app_dir("hub.pem")
    assert where.endswith("hub.pem")
    assert where.startswith(cfg.APP_DIR)
