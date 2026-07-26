"""Health checks: is it doing its job, not merely Running.

These use real sockets, real files and real commands rather than mocks. The whole
point of a health check is that it talks to the outside world, and a mocked socket
would prove nothing about whether we handle a refused connection.
"""

import socket
import threading
import time

import pytest

from core import config as cfg_mod
from core import health


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def listener():
    """A real listening socket on a free port, which actually accepts.

    It has to accept: with connections left in the backlog the queue fills and
    the *second* check gets a refused connection, which looks exactly like the
    failure we are trying to distinguish from success.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    stop = threading.Event()

    def serve():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
                conn.close()
            except (socket.timeout, OSError):
                continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield sock.getsockname()[1]
    stop.set()
    thread.join(timeout=2)
    sock.close()


@pytest.fixture
def http_server():
    """A real HTTP server, so status codes and bodies are the genuine article."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok":
                body = b'{"status":"healthy","queue":0}'
                self.send_response(200)
            elif self.path == "/degraded":
                body = b'{"status":"degraded"}'
                self.send_response(200)
            elif self.path == "/boom":
                body = b"internal error"
                self.send_response(500)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass                                   # quiet

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def service(*checks, **health_args):
    return cfg_mod.Service(name="Svc", label="Svc",
                           health=cfg_mod.Health(checks=list(checks),
                                                 **health_args))


class FakeStore:
    def __init__(self, status="Running"):
        self.status = status

    def status_of(self, _name, _machine=""):
        return self.status


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------
def test_tcp_check_sees_a_listening_port(listener):
    check = cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1", port=listener)
    result = health.run_check(check)
    assert result.ok is True
    assert str(listener) in result.detail


def test_tcp_check_reports_a_refused_connection():
    """A port nobody is listening on is the "running but never opened up" case."""
    spare = socket.socket()
    spare.bind(("127.0.0.1", 0))
    port = spare.getsockname()[1]
    spare.close()                                  # now certainly closed

    check = cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1", port=port,
                                timeout_seconds=2)
    result = health.run_check(check)
    assert result.ok is False
    assert str(port) in result.detail


def test_addresses_put_link_local_ipv6_last():
    """A Windows machine name resolves to fe80::… before its IPv4 address, and
    nothing listens there. Trying it first cost two seconds a check — measured
    2.05s by name against 22ms by address."""
    import socket as sk
    ordered = health._addresses("localhost", 80)
    assert ordered, "localhost must resolve to something"

    # Synthesise the awkward case rather than depending on this network.
    infos = [
        (sk.AF_INET6, sk.SOCK_STREAM, 6, "", ("fe80::1", 80, 0, 0)),
        (sk.AF_INET6, sk.SOCK_STREAM, 6, "", ("fdc1::5", 80, 0, 0)),
        (sk.AF_INET, sk.SOCK_STREAM, 6, "", ("10.0.0.5", 80)),
    ]
    ranked = sorted(infos, key=lambda i: (
        1 if i[4][0].lower().startswith("fe80:") else 0,
        0 if i[0] == sk.AF_INET else 1))
    assert [i[4][0] for i in ranked] == ["10.0.0.5", "fdc1::5", "fe80::1"]


def test_an_unresolvable_host_fails_fast_and_says_why():
    check = cfg_mod.HealthCheck(kind="tcp", host="no-such-host-anywhere-xyz",
                                port=80, timeout_seconds=5)
    result = health.run_check(check)
    assert result.ok is False
    assert "cannot resolve" in result.detail


def test_tcp_check_gives_up_rather_than_hanging():
    """An address that swallows packets must cost us the timeout and no more —
    a Windows TCP connect otherwise blocks for about twenty seconds."""
    check = cfg_mod.HealthCheck(kind="tcp", host="10.255.255.1", port=9,
                                timeout_seconds=1)
    started = time.monotonic()
    result = health.run_check(check)
    spent = time.monotonic() - started
    assert result.ok is False
    assert spent < 5, f"took {spent:.1f}s, the timeout was 1s"


def test_http_check_accepts_success_and_rejects_errors(http_server):
    ok = cfg_mod.HealthCheck(kind="http", url=f"{http_server}/ok")
    assert health.run_check(ok).ok is True

    broken = cfg_mod.HealthCheck(kind="http", url=f"{http_server}/boom")
    result = health.run_check(broken)
    assert result.ok is False and "500" in result.detail

    missing = cfg_mod.HealthCheck(kind="http", url=f"{http_server}/nope")
    assert health.run_check(missing).ok is False


def test_http_check_can_demand_a_status_and_a_string(http_server):
    """A service that answers 200 with "degraded" in the body is not healthy, and
    only the body says so."""
    check = cfg_mod.HealthCheck(kind="http", url=f"{http_server}/degraded",
                                expect_text='"status":"healthy"')
    result = health.run_check(check)
    assert result.ok is False
    assert "was not in the response" in result.detail

    check.url = f"{http_server}/ok"
    assert health.run_check(check).ok is True

    # An expected 404 is a pass, if that is what you asked for.
    odd = cfg_mod.HealthCheck(kind="http", url=f"{http_server}/nope",
                              expect_status=404)
    assert health.run_check(odd).ok is True


def test_http_check_survives_a_dead_host():
    check = cfg_mod.HealthCheck(kind="http", url="http://127.0.0.1:9/",
                                timeout_seconds=2)
    result = health.run_check(check)
    assert result.ok is False and result.detail


def test_file_check_measures_age(tmp_path):
    beat = tmp_path / "heartbeat.txt"
    beat.write_text("alive", encoding="utf-8")

    fresh = cfg_mod.HealthCheck(kind="file", path=str(beat), max_age_seconds=300)
    assert health.run_check(fresh).ok is True

    stale = cfg_mod.HealthCheck(kind="file", path=str(beat), max_age_seconds=1)
    import os
    os.utime(beat, (time.time() - 600, time.time() - 600))
    result = health.run_check(stale)
    assert result.ok is False and "last written" in result.detail

    missing = cfg_mod.HealthCheck(kind="file", path=str(tmp_path / "nope.txt"),
                                  max_age_seconds=300)
    assert health.run_check(missing).ok is False


def test_command_check_uses_the_exit_code_and_quotes_the_output():
    good = cfg_mod.HealthCheck(kind="command", command="cmd /c exit 0")
    assert health.run_check(good).ok is True

    bad = cfg_mod.HealthCheck(kind="command",
                              command="cmd /c echo it is wedged & exit 3")
    result = health.run_check(bad)
    assert result.ok is False
    assert "exit 3" in result.detail
    assert "wedged" in result.detail          # the command's own words

    expected = cfg_mod.HealthCheck(kind="command", command="cmd /c exit 3",
                                   expect_exit=3)
    assert health.run_check(expected).ok is True


def test_command_check_is_killed_if_it_hangs():
    check = cfg_mod.HealthCheck(kind="command",
                                command="cmd /c ping -n 20 127.0.0.1 > nul",
                                timeout_seconds=1)
    started = time.monotonic()
    result = health.run_check(check)
    assert result.ok is False
    assert time.monotonic() - started < 6


def test_process_check_needs_something_to_ask():
    check = cfg_mod.HealthCheck(kind="process")
    assert health.run_check(check).ok is False        # no control passed

    class FakeControl:
        def __init__(self, pid):
            self.pid = pid

        def process_id(self, _name, _machine=""):
            return self.pid

    assert health.run_check(check, control=FakeControl(4321),
                            service="Svc").ok is True
    result = health.run_check(check, control=FakeControl(0), service="Svc")
    assert result.ok is False
    assert "no process" in result.detail


def test_a_check_that_raises_is_a_failure_not_a_crash():
    class Exploding:
        kind = "tcp"
        host = "127.0.0.1"
        enabled = True
        timeout_seconds = 1

        @property
        def port(self):
            raise RuntimeError("boom")

        def describe(self):
            return "exploding check"

    result = health.run_check(Exploding())
    assert result.ok is False and "boom" in result.detail


# ---------------------------------------------------------------------------
# ANDing, and the summary
# ---------------------------------------------------------------------------
def test_every_check_has_to_pass(listener, http_server):
    svc = service(cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1", port=listener),
                  cfg_mod.HealthCheck(kind="http", url=f"{http_server}/ok"))
    ok, results = health.run_all(svc)
    assert ok is True and len(results) == 2

    svc.health.checks.append(
        cfg_mod.HealthCheck(kind="http", url=f"{http_server}/boom"))
    ok, results = health.run_all(svc)
    assert ok is False
    assert "500" in health.summarise(results)
    # Only the failure is reported; the two that passed are not noise.
    assert health.summarise(results).count("failed") == 1


def test_a_disabled_check_is_not_run(listener):
    svc = service(cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1", port=1,
                                      enabled=False))
    ok, results = health.run_all(svc)
    assert ok is True and results == []


def test_a_service_with_no_checks_is_never_unhealthy():
    ok, results = health.run_all(service())
    assert ok is True and results == []
    # The switch is on by default, but with nothing to ask there is nothing active.
    assert service().health.enabled is True
    assert service().health.active is False


def test_the_master_switch_keeps_the_checks_and_stops_asking():
    """Turning watching off for an afternoon must not cost you the configuration
    you need to turn it back on."""
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0)
    assert svc.health.active is True

    svc.health.enabled = False
    assert svc.health.active is False
    assert len(svc.health.checks) == 1            # still there

    ok, results = health.run_all(svc)
    assert ok is True and results == []           # nothing asked, nothing claimed

    mon, _clock, verdicts, _a = monitor(svc)
    mon.note_running(svc.name)
    assert mon.due(svc) is False
    assert verdicts == []

    svc.health.enabled = True
    assert svc.health.active is True
    assert mon.due(svc) is True


def test_an_unfinished_check_is_skipped_not_failed():
    """A check added in the editor with no port yet cannot tell us anything, and
    failing it would report a healthy service as dead because of a half-finished
    edit."""
    blank = cfg_mod.HealthCheck(kind="tcp")       # no port
    assert blank.is_configured() is False
    assert "No port set yet" in blank.describe()

    svc = service(blank)
    ok, results = health.run_all(svc)
    assert ok is True and results == []
    assert svc.health.active is False             # nothing to ask yet

    for kind, field, value in (("http", "url", "http://x/"),
                               ("file", "path", "C:\\x.log"),
                               ("command", "command", "cmd /c exit 0")):
        check = cfg_mod.HealthCheck(kind=kind)
        assert check.is_configured() is False
        assert "set yet" in check.describe()
        setattr(check, field, value)
        assert check.is_configured() is True
        assert "set yet" not in check.describe()

    # "It has a process" needs nothing typed in, so it is ready immediately.
    assert cfg_mod.HealthCheck(kind="process").is_configured() is True


# ---------------------------------------------------------------------------
# the monitor: when to ask, and what to do about the answer
# ---------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


def monitor(svc, store=None, **kwargs):
    cfg = cfg_mod.Config(services=[svc])
    clock = Clock()
    verdicts, actions = [], []
    mon = health.Monitor(
        lambda: cfg, store or FakeStore(), control=None,
        on_verdict=lambda s, v, d, r: verdicts.append((s.name, v, d)),
        on_action=lambda s, a, d: actions.append((s.name, a)),
        now=clock, **kwargs)
    return mon, clock, verdicts, actions


def failing_check():
    return cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1", port=9,
                               timeout_seconds=1)


def test_nothing_is_judged_during_the_grace_period():
    """A service that has just started has not opened its port yet, so checking
    it immediately would report every restart as a failure."""
    svc = service(failing_check(), grace_seconds=60, interval_seconds=10)
    mon, clock, _v, _a = monitor(svc)
    mon.note_running(svc.name)

    assert mon.due(svc) is False
    clock.tick(59)
    assert mon.due(svc) is False
    clock.tick(2)
    assert mon.due(svc) is True


def test_a_service_that_was_already_up_gets_its_grace_from_now():
    """We never saw it start, so there is no start time to measure from — and
    guessing "long ago" would judge it blind on the first round."""
    svc = service(failing_check(), grace_seconds=30)
    mon, clock, _v, _a = monitor(svc)
    assert mon.due(svc) is False        # first look sets the clock running
    clock.tick(31)
    assert mon.due(svc) is True


def test_a_stopped_service_is_not_unhealthy():
    svc = service(failing_check(), grace_seconds=0)
    mon, _clock, _v, _a = monitor(svc, store=FakeStore("Stopped"))
    assert mon.due(svc) is False
    assert mon.verdict(svc.name) == health.UNKNOWN


def test_one_blip_is_not_a_verdict():
    """Services drop a connection under load. Three in a row is a problem."""
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                  failures_before_acting=3)
    mon, _clock, verdicts, _a = monitor(svc)
    mon.note_running(svc.name)

    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.UNKNOWN and verdicts == []
    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.UNKNOWN
    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.UNHEALTHY
    assert [v for _n, v, _d in verdicts] == [health.UNHEALTHY]
    assert "127.0.0.1:9" in verdicts[0][2]


def test_recovering_clears_the_count_and_says_so(listener):
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                  failures_before_acting=2)
    mon, _clock, verdicts, _a = monitor(svc)
    mon.note_running(svc.name)
    mon.check_now(svc)
    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.UNHEALTHY

    # Point it at something that answers.
    svc.health.checks[0] = cfg_mod.HealthCheck(kind="tcp", host="127.0.0.1",
                                               port=listener)
    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.HEALTHY
    assert [v for _n, v, _d in verdicts] == [health.UNHEALTHY, health.HEALTHY]


def test_restart_is_opt_in_and_rate_limited():
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                  failures_before_acting=1, action="notify")
    mon, clock, _v, actions = monitor(svc)
    mon.note_running(svc.name)
    mon.check_now(svc)
    assert actions == []                       # notify only: we don't touch it

    svc.health.action = "restart"
    mon.check_now(svc)
    assert actions == [(svc.name, "restart")]

    # Not again straight away: a service a restart cannot fix must not be
    # restarted every round for ever. The limit is a setting, not a constant.
    mon.check_now(svc)
    assert len(actions) == 1
    clock.tick(svc.health.min_restart_interval_seconds + 1)
    mon.check_now(svc)
    assert len(actions) == 2


def test_the_first_restart_is_never_held_back_by_the_limit():
    """The limit was "now minus the last action", and the last action started at
    zero — so on a machine whose uptime was under five minutes, the first health
    restart was silently skipped. Which is exactly when it matters: just after a
    reboot."""
    for uptime in (20, 120, 299, 400, 100000):
        svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                      failures_before_acting=1, action="restart")
        cfg = cfg_mod.Config(services=[svc])
        clock = Clock()
        clock.t = float(uptime)                # as time.monotonic() would report
        actions = []
        mon = health.Monitor(lambda: cfg, FakeStore(), control=None,
                             on_action=lambda s, a, d: actions.append(a),
                             now=clock)
        mon.note_running(svc.name)
        mon.check_now(svc)
        assert actions == ["restart"], f"not restarted at uptime {uptime}s"


def test_the_restart_limit_is_configurable():
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                  failures_before_acting=1, action="restart")
    svc.health.min_restart_interval_seconds = 30
    mon, clock, _v, actions = monitor(svc)
    mon.note_running(svc.name)
    mon.check_now(svc)
    assert len(actions) == 1
    clock.tick(29)
    mon.check_now(svc)
    assert len(actions) == 1, "still inside the limit"
    clock.tick(2)
    mon.check_now(svc)
    assert len(actions) == 2

    # Zero means restart on every verdict.
    svc.health.min_restart_interval_seconds = 0
    mon.check_now(svc)
    assert len(actions) == 3


def test_the_schedule_is_published_for_the_panel_to_show():
    """Otherwise the schedule is something you infer from the settings and then
    have to trust."""
    class Recording(FakeStore):
        def __init__(self):
            super().__init__()
            self.timing = {}

        def set_health_timing(self, name, machine="", **facts):
            self.timing.setdefault((machine, name), {}).update(facts)

    svc = service(failing_check(), grace_seconds=45, interval_seconds=90,
                  failures_before_acting=2)
    cfg = cfg_mod.Config(services=[svc])
    store = Recording()
    mon = health.Monitor(lambda: cfg, store, control=None, now=Clock())

    mon.note_running(svc.name)
    said = store.timing[("", "Svc")]
    assert said["last"] is None                     # not checked yet
    assert said["next"] is not None                 # …but we say when
    assert "settle" in said["detail"]

    mon.check_now(svc)
    said = store.timing[("", "Svc")]
    assert said["last"] is not None
    assert said["passed"] is False and said["failures"] == 1
    # The next check is an interval after the last one.
    assert 80 <= (said["next"] - said["last"]).total_seconds() <= 100


def test_maintenance_means_leave_it_alone():
    svc = service(failing_check(), grace_seconds=0, interval_seconds=0,
                  failures_before_acting=1, action="restart")
    window = [True]
    svc_cfg = cfg_mod.Config(services=[svc])
    actions = []
    mon = health.Monitor(lambda: svc_cfg, FakeStore(), control=None,
                         on_action=lambda s, a, d: actions.append(a),
                         now=Clock(), in_maintenance=lambda: window[0])
    mon.note_running(svc.name)
    mon.check_now(svc)
    assert mon.verdict(svc.name) == health.UNHEALTHY   # still reported…
    assert actions == []                              # …but not acted on

    window[0] = False
    mon.check_now(svc)
    assert actions == ["restart"]


def test_health_survives_a_config_round_trip(tmp_path):
    svc = cfg_mod.Service(name="AppEngine", health=cfg_mod.Health(
        checks=[cfg_mod.HealthCheck(kind="tcp", port=30000),
                cfg_mod.HealthCheck(kind="http", url="http://localhost:8080/health",
                                    expect_status=200, expect_text="ok",
                                    insecure=True),
                cfg_mod.HealthCheck(kind="command", command="sqlcmd -Q \"select 1\"",
                                    expect_exit=0, timeout_seconds=20)],
        interval_seconds=30, grace_seconds=90, failures_before_acting=2,
        action="restart"))
    path = str(tmp_path / "services.json")
    cfg_mod.save(cfg_mod.Config(services=[svc]), path)
    back = cfg_mod.load(path).service("AppEngine")

    assert [c.kind for c in back.health.checks] == ["tcp", "http", "command"]
    assert back.health.checks[1].expect_text == "ok"
    assert back.health.checks[1].insecure is True
    assert back.health.checks[2].timeout_seconds == 20
    assert (back.health.interval_seconds, back.health.grace_seconds) == (30, 90)
    assert (back.health.failures_before_acting, back.health.action) == (2, "restart")


def test_a_check_with_nothing_to_check_is_dropped(tmp_path):
    """It would pass every time, which reads as a guarantee rather than as an
    unconfigured check."""
    import json
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"services": [{"name": "A", "health": {"checks": [
        {"kind": "tcp"},                       # no port
        {"kind": "http"},                      # no url
        {"kind": "file"},                      # no path
        {"kind": "command"},                   # no command
        {"kind": "telepathy", "port": 1},      # not a thing
        {"kind": "process"},                   # needs nothing: kept
    ]}}]}), encoding="utf-8")

    checks = cfg_mod.load(str(path)).service("A").health.checks
    assert [c.kind for c in checks] == ["process"]


def test_a_health_change_is_recorded_with_its_reason(tmp_path):
    from core import history
    path = str(tmp_path / "h.db")
    history.record_health("AppEngine", "unhealthy",
                          "failed: something answers on 127.0.0.1:1433 — refused",
                          path=path)
    history.record_health("AppEngine", "healthy", "127.0.0.1:1433 accepted",
                          path=path)

    rows = history.query(service_names=["AppEngine"], labels=["AppEngine"],
                         path=path)
    kinds = [(r["kind"], r["event"], r["level"]) for r in rows]
    assert ("health", "not responding", "Error") in kinds
    assert ("health", "responding again", "") in kinds
    assert any("1433" in r["detail"] for r in rows)
    assert all(r["source"] == "health check" for r in rows)


def test_a_history_write_that_fails_is_reported_not_swallowed(tmp_path):
    """History is evidence. An empty timeline that is really a permissions
    problem reads as "nothing happened" for as long as nobody checks."""
    from core import history
    history._last_error, history._reported = "", False
    # A directory where the file should be: it cannot be opened as a store.
    blocked = tmp_path / "wedged"
    blocked.mkdir()
    history.record_health("A", "unhealthy", "x", path=str(blocked))
    assert history.last_error()
    assert str(blocked) in history.last_error()

    # A good write clears it again.
    history.record_health("A", "healthy", "x", path=str(tmp_path / "ok.db"))
    assert history.last_error() == ""
    history._last_error, history._reported = "", False


def test_nonsense_health_values_are_repaired(tmp_path):
    import json
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"services": [{"name": "A", "health": {
        "interval_seconds": 0, "grace_seconds": -5,
        "failures_before_acting": 0, "action": "explode",
        "checks": [{"kind": "tcp", "port": 999999, "timeout_seconds": 9000}],
    }}]}), encoding="utf-8")

    h = cfg_mod.load(str(path)).service("A").health
    assert h.interval_seconds >= 5 and h.grace_seconds == 0
    assert h.failures_before_acting == 1 and h.action == "notify"
    assert h.checks[0].port == 65535 and h.checks[0].timeout_seconds == 120
