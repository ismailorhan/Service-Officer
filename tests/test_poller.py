"""Machines that cannot tell us themselves get asked — and only those."""

from core import config as cfg_mod
from core import connectors, poller
from core import state as st


class FakeConn:
    def __init__(self, push=False, answers=None, fail=None):
        self._push = push
        self._answers = answers or {}
        self._fail = fail
        self.batches = []

    def abilities(self):
        return connectors.Abilities(push=self._push)

    def statuses(self, names):
        if self._fail:
            raise ConnectionError(self._fail)
        self.batches.append(list(names))
        return {n: connectors.Status(state=self._answers.get(n, st.RUNNING))
                for n in names}

    def status(self, name):
        return connectors.Status(state=self._answers.get(name, st.RUNNING))


def config_with(*machines, services=()):
    return cfg_mod.Config(
        machines=[cfg_mod.Machine()] + list(machines),
        services=[cfg_mod.Service(name=n, machine=m) for n, m in services])


def use(monkeypatch, conns):
    monkeypatch.setattr(connectors, "for_machine",
                        lambda m="", record=None: conns[m or ""])
    monkeypatch.setattr(connectors, "machine_record",
                        lambda m="": cfg_mod.Machine(name=m, poll_seconds=5))


def test_the_local_machine_is_never_polled(monkeypatch):
    """The SCM pushes changes within ~32 ms. Asking as well is pure waste."""
    conns = {"": FakeConn(push=True), "hanadev": FakeConn()}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("Dnscache", ""), ("b1s50000.service", "hanadev")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None)

    assert p.machines_to_poll() == {"hanadev": ["b1s50000.service"]}


def test_a_machine_that_pushes_is_not_polled_either(monkeypatch):
    """If a transport can tell us, asking is noise — remote Windows may yet be
    able to, and this must not need changing when it can."""
    conns = {"": FakeConn(push=True), "CTL052": FakeConn(push=True)}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="CTL052"),
                      services=[("AppEngine", "CTL052")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None)

    assert p.machines_to_poll() == {}


def test_a_machine_with_nothing_watched_on_it_is_not_asked(monkeypatch):
    """An unused entry in the machines list is a note to the user, not work."""
    conns = {"": FakeConn(push=True), "spare": FakeConn()}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="spare", kind="linux"))
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None)

    assert p.machines_to_poll() == {}


def test_every_service_on_a_host_is_one_round_trip(monkeypatch):
    conn = FakeConn(answers={"a.service": st.STOPPED})
    conns = {"": FakeConn(push=True), "hanadev": conn}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("a.service", "hanadev"),
                                ("b.service", "hanadev")])
    seen = []
    p = poller.Poller(lambda: cfg, on_status=lambda *a: seen.append(a))

    p.poll_once("hanadev", ["a.service", "b.service"])

    assert conn.batches == [["a.service", "b.service"]], "asked one at a time"
    assert [(n, s.state) for n, _m, s in seen] == [("a.service", st.STOPPED),
                                                   ("b.service", st.RUNNING)]


def test_an_unreachable_machine_reports_nothing_rather_than_stopped(monkeypatch):
    """A green row for a server that is down is a lie; so is a red one. We do not
    know, and the store has a word for that."""
    conns = {"": FakeConn(push=True),
             "hanadev": FakeConn(fail="No route to host")}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("a.service", "hanadev")])
    seen, outages = [], []
    p = poller.Poller(lambda: cfg, on_status=lambda *a: seen.append(a),
                      on_unreachable=lambda m, why: outages.append((m, why)))

    p.poll_once("hanadev", ["a.service"])

    assert seen == [("a.service", "hanadev", None)]
    assert outages and "No route to host" in outages[0][1]


def test_a_machine_that_is_down_is_not_hammered(monkeypatch):
    """One failed connection a minute, not one per interval: a dead server must
    not cost a connection attempt every five seconds for a week."""
    clock = [1000.0]
    conns = {"": FakeConn(push=True), "hanadev": FakeConn(fail="timed out")}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("a.service", "hanadev")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None,
                      now=lambda: clock[0])

    assert [m for m, _s in p.due_now()] == ["hanadev"]
    p.poll_once("hanadev", ["a.service"])          # fails
    clock[0] += 10
    assert p.due_now() == [], "tried again while still marked down"
    clock[0] += poller.Poller.RETRY_SECONDS
    assert [m for m, _s in p.due_now()] == ["hanadev"]


def test_the_interval_comes_from_the_machine(monkeypatch):
    clock = [1000.0]
    conns = {"": FakeConn(push=True), "hanadev": FakeConn()}
    monkeypatch.setattr(connectors, "for_machine",
                        lambda m="", record=None: conns[m or ""])
    monkeypatch.setattr(connectors, "machine_record",
                        lambda m="": cfg_mod.Machine(name=m, poll_seconds=30))
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux",
                                      poll_seconds=30),
                      services=[("a.service", "hanadev")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None, now=lambda: clock[0])

    assert p.due_now()
    clock[0] += 10
    assert p.due_now() == []
    clock[0] += 21
    assert p.due_now()


def test_a_machine_we_cannot_even_ask_about_is_polled_anyway(monkeypatch):
    """If probing what a target can do fails, poll it — the alternative is
    dropping it silently, which is how a service stops being watched."""
    class Broken(FakeConn):
        def abilities(self):
            raise ConnectionError("nope")

    conns = {"": FakeConn(push=True), "hanadev": Broken()}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("a.service", "hanadev")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None)

    assert "hanadev" in p.machines_to_poll()


def test_a_slow_machine_does_not_starve_a_fast_one(monkeypatch):
    """What the panel showed: four SUSE services reading "Unknown" while nothing was
    wrong with them.

    They were behind a remote Windows machine in one queue, and that machine took a
    minute to answer — measured, 63 seconds for a single service — so the SSH
    machine's turn never came round. Each machine is asked on its own thread now.
    """
    import threading
    import time

    slow_started = threading.Event()

    class Slow(FakeConn):
        def statuses(self, names):
            slow_started.set()
            time.sleep(3)
            return super().statuses(names)

    conns = {"": FakeConn(push=True), "sc-sql": Slow(), "hanadev": FakeConn()}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="sc-sql"),
                      cfg_mod.Machine(name="hanadev", kind="linux"),
                      services=[("B1ServerTools64", "sc-sql"),
                                ("webclient.service", "hanadev")])
    answered = []
    p = poller.Poller(lambda: cfg,
                      on_status=lambda name, machine, status:
                          answered.append(machine))

    for machine, services in p.due_now():
        p._poll_in_background(machine, services)
    assert slow_started.wait(2), "the slow machine was never asked"

    # The fast machine has answered while the slow one is still thinking.
    deadline = time.perf_counter() + 2
    while time.perf_counter() < deadline and "hanadev" not in answered:
        time.sleep(0.02)
    assert "hanadev" in answered, "waited behind the slow machine"
    assert "sc-sql" not in answered


def test_a_machine_slower_than_its_interval_is_not_asked_twice_at_once(monkeypatch):
    """Otherwise a machine that takes a minute at a five-second interval accumulates
    twelve threads all asking it the same question."""
    import threading
    import time

    calls = []

    class Slow(FakeConn):
        def statuses(self, names):
            calls.append(time.perf_counter())
            time.sleep(1)
            return super().statuses(names)

    conns = {"": FakeConn(push=True), "sc-sql": Slow()}
    use(monkeypatch, conns)
    cfg = config_with(cfg_mod.Machine(name="sc-sql"),
                      services=[("B1ServerTools64", "sc-sql")])
    p = poller.Poller(lambda: cfg, on_status=lambda *a: None)

    for _ in range(5):
        p._poll_in_background("sc-sql", ["B1ServerTools64"])
        time.sleep(0.05)

    assert len(calls) == 1, f"asked {len(calls)} times over each other"
