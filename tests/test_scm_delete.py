"""Letting go of a service somebody has deleted.

`DeleteService` only *marks* a service for deletion. The database entry survives until every
open handle to it has been closed, and `core/scm.py`'s Watcher held one per watched service
from the moment the hub service started. So an uninstall left a ghost entry, the reinstall's
`CreateService` got ERROR_SERVICE_MARKED_FOR_DELETE, and what came out the other side was a
service in a half-made state — disabled or stopped — that only a reboot put right, because a
reboot is what rebuilds the database.

Reported from the field: uninstall AppEngine, install it again, and it does not come up until
Windows restarts. `SERVICE_NOTIFY_DELETE_PENDING` was in the notification mask all along and
nothing ever read `dwNotificationTriggered`, so the SCM was asking us to let go and we were
not listening.

These tests are about the letting go. What Windows does with a held handle is Windows'
documented behaviour, and tools/prove_delete.py demonstrates it end to end against a real
throwaway service — which needs an administrator, so it is not run here.
"""

import pytest

from core import scm as scm_mod


class FakeHandleTable:
    """Stands in for advapi32, and counts what matters: which handles are still open."""

    def __init__(self):
        self.open = set()
        self.next = 1000
        self.closed = []

    def hand_out(self):
        self.next += 1
        self.open.add(self.next)
        return self.next

    def close(self, handle):
        self.closed.append(handle)
        self.open.discard(handle)
        return 1


@pytest.fixture
def table(monkeypatch):
    """A Watcher whose handles are ours to count."""
    t = FakeHandleTable()
    monkeypatch.setattr(scm_mod._advapi, "CloseServiceHandle", t.close,
                        raising=False)
    return t


def _watch(table, name="AppEngine"):
    return scm_mod._Watch(name, table.hand_out())


# ── hearing it ─────────────────────────────────────────────────────────────
def test_the_delete_notification_is_read_at_all(table):
    """`dwNotificationTriggered` was declared in the struct and read nowhere."""
    w = _watch(table)
    w.buf.dwNotificationStatus = 0
    w.buf.dwNotificationTriggered = scm_mod.SERVICE_NOTIFY_DELETE_PENDING
    w._fired(None)
    assert w.gone is True
    assert w.hits == [], "a deletion is not a state to report as one"


def test_a_delete_that_arrives_as_the_status_counts_too(table):
    """The SCM reports it either as the triggering reason or — if the service was already
    marked by the time the registration was serviced — as the status of the registration."""
    w = _watch(table)
    w.buf.dwNotificationStatus = scm_mod.ERROR_SERVICE_MARKED_FOR_DELETE
    w._fired(None)
    assert w.gone is True


def test_an_ordinary_state_still_gets_through(table):
    """The fix must not eat the notifications this class exists for."""
    w = _watch(table)
    w.buf.dwNotificationStatus = 0
    w.buf.dwNotificationTriggered = 0x0008          # RUNNING
    w.buf.ServiceStatus.dwCurrentState = 4
    w.buf.ServiceStatus.dwProcessId = 4242
    w._fired(None)
    assert w.gone is False
    assert w.hits == [("Running", 0, 4242)]


# ── acting on it ───────────────────────────────────────────────────────────
def _watcher(table, names=("AppEngine",), safety=None):
    w = scm_mod.Watcher(lambda: list(names), lambda *a: None, safety_query=safety)
    return w


def test_the_handle_is_closed_and_the_service_reported_gone(table):
    seen = []
    watcher = scm_mod.Watcher(lambda: ["AppEngine"],
                              lambda name, status, *_: seen.append((name, status)))
    w = _watch(table)
    watcher._watches["AppEngine"] = w
    w.gone = True

    watcher._reap()

    assert w.handle in table.closed, "the handle is what holds the entry open"
    assert not table.open, "something is still holding it"
    assert "AppEngine" not in watcher._watches
    # Not silently: the store would otherwise keep whatever it last heard, so a service
    # somebody has just uninstalled would go on reading as Running.
    assert seen == [("AppEngine", "Not Found")]


def test_a_live_service_is_left_alone(table):
    watcher = scm_mod.Watcher(lambda: ["AppEngine"], lambda *a: None)
    w = _watch(table)
    watcher._watches["AppEngine"] = w
    watcher._reap()
    assert watcher._watches == {"AppEngine": w}
    assert table.closed == []


def test_a_refused_registration_is_not_retried_for_ever(table):
    """Only 1294 was acted on. Every other failure fell through to "try again", so the loop
    re-armed a handle to a deleted service every 400ms and held the entry open the whole time —
    which is the bug, not a detail of it.
    """
    watcher = scm_mod.Watcher(lambda: ["AppEngine"], lambda *a: None)
    w = _watch(table)
    watcher._watches["AppEngine"] = w
    w.arm = lambda: (False, scm_mod.ERROR_SERVICE_MARKED_FOR_DELETE)

    watcher._arm_all()
    assert w.gone is True, "still trying to register against a deleted service"

    watcher._reap()
    assert not table.open


def test_arming_is_not_attempted_on_a_service_already_gone(table):
    watcher = scm_mod.Watcher(lambda: ["AppEngine"], lambda *a: None)
    w = _watch(table)
    w.gone = True
    tries = []
    w.arm = lambda: tries.append(1) or (True, 0)
    watcher._watches["AppEngine"] = w
    watcher._arm_all()
    assert tries == []


def test_the_slow_sweep_lets_go_when_the_notification_never_came(table):
    """One net is not enough for this: the whole failure was a signal we did not act on, so a
    signal cannot be the only thing that makes us let go.

    The first version of this test drove `_loop` with the stop flag already set — which runs the
    body zero times, so the handle was closed by the shutdown path and the test passed without
    the sweep ever running. Hence `_sweep` being a method: the thing under test has to be the
    thing called.
    """
    seen = []
    watcher = scm_mod.Watcher(
        lambda: ["AppEngine"],
        lambda name, status, *_: seen.append((name, status)),
        safety_query=lambda _name: "Not Found")
    w = _watch(table)
    watcher._watches["AppEngine"] = w

    watcher._sweep()

    assert w.handle in table.closed
    assert "AppEngine" not in watcher._watches
    assert seen == [("AppEngine", "Not Found")]


def test_the_sweep_keeps_a_service_that_is_merely_stopped(table):
    """Stopped is not gone, and letting go of a stopped service's handle would mean missing the
    moment it comes back — which is the one thing this class exists to see."""
    watcher = scm_mod.Watcher(lambda: ["AppEngine"], lambda *a: None,
                              safety_query=lambda _name: "Stopped")
    w = _watch(table)
    watcher._watches["AppEngine"] = w
    watcher._sweep()
    assert watcher._watches == {"AppEngine": w}
    assert table.closed == []


def test_stopping_still_closes_everything(table):
    """The old guarantee, unchanged: nothing outlives the loop."""
    watcher = scm_mod.Watcher(lambda: [], lambda *a: None)
    first, second = _watch(table, "A"), _watch(table, "B")
    watcher._watches.update({"A": first, "B": second})
    watcher._stop.set()
    watcher._loop()
    assert not table.open, f"still open: {table.open}"
