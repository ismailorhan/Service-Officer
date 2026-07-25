"""Is the service actually doing its job?

The SCM answers a narrower question than anyone wants. It reports Running when a
process exists and hasn't asked to stop — not that the port is open, not that the
API answers, not that the integration is still writing to its log. "Running but
dead" is the failure people actually get paged for, and it is invisible from the
service list.

So the answer has to come from outside the SCM: connect to the port, fetch the
URL, look at the file, run the command. Five kinds, none of which need to know
what product the service is.

Two rules run through all of it:

*Every check has a timeout, and none of them run on the GUI thread.* A TCP
connect to a machine that has gone away blocks for about twenty seconds on
Windows; a health check that freezes the panel is worse than no health check.

*One bad answer is not a verdict.* Services drop a connection under load, a URL
returns 503 while a pool refills. Acting takes `failures_before_acting`
consecutive failures, and nothing is judged at all until `grace_seconds` after it
reached Running — a service that has just started has not opened its port yet.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass

from . import applog
from . import config as cfg_mod

log = applog.get("health")

#: what we know about a service's health
UNKNOWN, HEALTHY, UNHEALTHY = "unknown", "healthy", "unhealthy"


@dataclass
class Result:
    """The outcome of one check, in a form fit to show and to log."""
    ok: bool
    detail: str = ""
    seconds: float = 0.0
    check: object = None

    def line(self) -> str:
        where = self.check.describe() if self.check is not None else "check"
        return f"{'ok' if self.ok else 'failed'}: {where}" + (
            f" — {self.detail}" if self.detail else "")


# ---------------------------------------------------------------------------
# The individual checks
# ---------------------------------------------------------------------------
def _addresses(host: str, port: int) -> list:
    """Where to try, best first.

    A Windows machine name usually resolves to a link-local IPv6 address
    (fe80::…) *before* its IPv4 one, and nothing listens there.
    socket.create_connection walks the list in order, so every check against a
    hostname spent two seconds failing at IPv6 before succeeding on IPv4 —
    measured: 2.05s by name, 22ms by address. With a five-second timeout that is
    most of the budget wasted, and on a slower service it is a false alarm.

    So: link-local last, IPv4 before IPv6, and each address gets a share of the
    time rather than the first one being allowed to eat all of it.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError(f"cannot resolve {host} — {exc}") from exc

    def rank(info):
        family, _t, _p, _c, sockaddr = info
        address = sockaddr[0]
        link_local = address.lower().startswith("fe80:")
        return (1 if link_local else 0, 0 if family == socket.AF_INET else 1)

    seen, ordered = set(), []
    for info in sorted(infos, key=rank):
        if info[4][:2] not in seen:
            seen.add(info[4][:2])
            ordered.append(info)
    return ordered


def _tcp(check, machine: str) -> Result:
    host = check.host or machine or "127.0.0.1"
    try:
        candidates = _addresses(host, check.port)
    except OSError as exc:
        return Result(False, str(exc))

    deadline = time.monotonic() + check.timeout_seconds
    last = ""
    for index, (family, socktype, proto, _c, sockaddr) in enumerate(candidates):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        # Share what's left, so one dead address can't consume the whole budget.
        share = max(0.5, left / (len(candidates) - index))
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(min(share, left))
            sock.connect(sockaddr)
            return Result(True, f"{host}:{check.port} accepted a connection"
                                + (f" ({sockaddr[0]})" if sockaddr[0] != host
                                   else ""))
        except socket.timeout:
            last = f"{sockaddr[0]} did not answer"
        except OSError as exc:
            # Connection refused is the interesting one: something is listening
            # nowhere, which for a Running service means it never opened up.
            last = f"{sockaddr[0]} — {getattr(exc, 'strerror', None) or exc}"
        finally:
            sock.close()
    return Result(False, f"{host}:{check.port} — {last or 'no answer'}")


def _http(check, machine: str) -> Result:
    import urllib.error
    import urllib.request

    context = None
    if check.url.lower().startswith("https"):
        import ssl
        if check.insecure:
            context = ssl._create_unverified_context()
    request = urllib.request.Request(check.url, method="GET",
                                     headers={"User-Agent": "ServiceOfficer"})
    try:
        with urllib.request.urlopen(request, timeout=check.timeout_seconds,
                                    context=context) as answer:
            status = answer.status
            body = ""
            if check.expect_text:
                # Only read what we need to; a health endpoint that streams a
                # log would otherwise be downloaded in full.
                body = answer.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, ""
        if check.expect_status and status == check.expect_status:
            return Result(True, f"HTTP {status} as expected")
        return Result(False, f"HTTP {status}")
    except Exception as exc:
        return Result(False, f"{type(exc).__name__}: {exc}")

    if check.expect_status:
        if status != check.expect_status:
            return Result(False, f"HTTP {status}, expected "
                                 f"{check.expect_status}")
    elif not 200 <= status < 400:
        return Result(False, f"HTTP {status}")
    if check.expect_text and check.expect_text not in body:
        return Result(False, f"HTTP {status}, but “{check.expect_text}” "
                             f"was not in the response")
    return Result(True, f"HTTP {status}")


def _process(check, machine: str, control=None, service: str = "") -> Result:
    """Running with no process behind it. Rare, and unmistakable when it happens."""
    if control is None:
        return Result(False, "no way to look up the process")
    try:
        pid = control.process_id(service, machine)
    except Exception as exc:
        return Result(False, f"could not read the process id: {exc}")
    if pid:
        return Result(True, f"process {pid}")
    return Result(False, "the SCM reports it as running, but there is no process")


def _file(check, machine: str) -> Result:
    try:
        age = time.time() - os.path.getmtime(check.path)
    except OSError as exc:
        return Result(False, f"{check.path} — {getattr(exc, 'strerror', exc)}")
    if age <= check.max_age_seconds:
        return Result(True, f"written {int(age)}s ago")
    return Result(False, f"last written {int(age)}s ago, expected within "
                         f"{check.max_age_seconds}s")


def _kill_tree(pid: int) -> None:
    """Kill a process and everything it started.

    Terminating the shell is not enough: its children inherit our pipes and keep
    them open, so subprocess goes on waiting for output long after the timeout
    expired — measured at nineteen seconds for a one-second timeout.
    """
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _command(check, machine: str) -> Result:
    try:
        proc = subprocess.Popen(check.command, shell=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess,
                                                      "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        return Result(False, f"could not run it: {exc}")

    try:
        out, err = proc.communicate(timeout=check.timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return Result(False, f"did not finish within {check.timeout_seconds}s")

    if proc.returncode == check.expect_exit:
        return Result(True, f"exit {proc.returncode}")
    # The command's own words are more use than ours.
    said = (err or out or "").strip().splitlines()
    return Result(False, f"exit {proc.returncode}"
                         + (f" — {said[0][:160]}" if said else ""))


def run_check(check, machine: str = "", control=None, service: str = "") -> Result:
    """One check, timed, never raising."""
    started = time.monotonic()
    try:
        if check.kind == "tcp":
            result = _tcp(check, machine)
        elif check.kind == "http":
            result = _http(check, machine)
        elif check.kind == "process":
            result = _process(check, machine, control, service)
        elif check.kind == "file":
            result = _file(check, machine)
        elif check.kind == "command":
            result = _command(check, machine)
        else:
            result = Result(False, f"unknown check “{check.kind}”")
    except Exception as exc:                       # a check must never take us
        result = Result(False, f"{type(exc).__name__}: {exc}")   # down with it
    result.seconds = round(time.monotonic() - started, 3)
    result.check = check
    return result


def run_all(service, control=None) -> tuple:
    """(ok, [Result]) for one service. ANDed: any failure means unhealthy."""
    results = [run_check(c, service.machine, control, service.name)
               for c in service.health.checks if c.enabled]
    return (all(r.ok for r in results) if results else True), results


def summarise(results) -> str:
    """What to put in a notification or a history row."""
    bad = [r for r in results if not r.ok]
    if not bad:
        return "  ·  ".join(r.detail for r in results if r.detail) or "healthy"
    return "  ·  ".join(r.line() for r in bad)


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------
@dataclass
class _Watch:
    """What we remember between rounds for one service."""
    failures: int = 0
    verdict: str = UNKNOWN
    last_checked: float = 0.0
    running_since: float = 0.0
    acted_at: float = 0.0
    detail: str = ""


class Monitor:
    """Asks each service's checks on its own interval, on a worker thread.

    Deliberately not a thread per service: a handful of services on one machine
    is a handful of sockets, and one loop is far easier to reason about when
    something hangs. Each round is bounded by the checks' own timeouts.
    """

    #: don't act again on the same service for this long, so a service that
    #: cannot be fixed by restarting isn't restarted every minute for ever
    COOLDOWN_SECONDS = 300

    def __init__(self, config_getter, store, control, on_verdict=None,
                 on_action=None, now=None, in_maintenance=None):
        self._config = config_getter
        self._store = store
        self._control = control
        self._on_verdict = on_verdict or (lambda *a: None)
        self._on_action = on_action or (lambda *a: None)
        self._now = now or time.monotonic
        #: set by the app once maintenance windows exist; a window means "leave
        #: it alone", and checking during planned work only produces noise
        self._in_maintenance = in_maintenance or (lambda: False)
        self._watches: dict = {}
        self._thread = None
        self._stop = None

    # -- state -------------------------------------------------------------
    def _watch(self, key) -> _Watch:
        return self._watches.setdefault(key, _Watch())

    def verdict(self, name: str, machine: str = "") -> str:
        return self._watches.get((machine or "", name), _Watch()).verdict

    def detail(self, name: str, machine: str = "") -> str:
        return self._watches.get((machine or "", name), _Watch()).detail

    def note_running(self, name: str, machine: str = "") -> None:
        """Called when a service reaches Running: the grace period starts here,
        and any previous verdict is stale."""
        watch = self._watch((machine or "", name))
        watch.running_since = self._now()
        watch.failures = 0
        watch.verdict = UNKNOWN
        watch.detail = ""

    def note_stopped(self, name: str, machine: str = "") -> None:
        """A stopped service is not unhealthy — it is stopped. Saying otherwise
        would put a red chip on something that is doing as it was told."""
        watch = self._watch((machine or "", name))
        watch.verdict = UNKNOWN
        watch.failures = 0
        watch.detail = ""
        watch.running_since = 0.0

    # -- decisions ---------------------------------------------------------
    def due(self, service, now: float = None) -> bool:
        now = now if now is not None else self._now()
        health = service.health
        if not health.enabled:
            return False
        if self._store.status_of(service.name, service.machine) != "Running":
            return False
        watch = self._watch(service.key)
        if not watch.running_since:
            # We never saw it start — it was already up when we launched. Treat
            # now as the start of its grace period rather than judging blind.
            watch.running_since = now
            return False
        if now - watch.running_since < health.grace_seconds:
            return False
        return now - watch.last_checked >= health.interval_seconds

    def check_now(self, service) -> tuple:
        """Run one service's checks and record the verdict. Returns (ok, results)."""
        ok, results = run_all(service, self._control)
        watch = self._watch(service.key)
        watch.last_checked = self._now()
        watch.detail = summarise(results)
        before = watch.verdict

        if ok:
            watch.failures = 0
            watch.verdict = HEALTHY
        else:
            watch.failures += 1
            # Only unhealthy once it has failed enough times in a row; until
            # then the verdict stands, so one blip doesn't paint the row red.
            if watch.failures >= service.health.failures_before_acting:
                watch.verdict = UNHEALTHY

        if watch.verdict != before:
            log.info("%s is %s: %s", service.name, watch.verdict, watch.detail)
            self._on_verdict(service, watch.verdict, watch.detail, results)

        if (watch.verdict == UNHEALTHY
                and service.health.action == "restart"
                and not self._in_maintenance()
                and self._now() - watch.acted_at >= self.COOLDOWN_SECONDS):
            watch.acted_at = self._now()
            log.info("%s failed %d checks in a row; restarting it",
                     service.name, watch.failures)
            self._on_action(service, "restart", watch.detail)
        return ok, results

    # -- the loop ----------------------------------------------------------
    def start(self, tick_seconds: float = 5.0) -> None:
        import threading
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._tick = tick_seconds
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            import threading
            if thread is not threading.current_thread():
                thread.join(timeout=self._tick + 2)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._tick)
            if self._stop.is_set():
                break
            if self._in_maintenance():
                continue
            try:
                for service in list(self._config().services):
                    if self._stop.is_set():
                        break
                    if self.due(service):
                        self.check_now(service)
            except Exception as exc:               # a bad config must not end
                log.warning("health round failed: %s", exc)   # the monitoring
