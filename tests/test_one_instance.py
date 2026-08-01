"""One tray app per session.

Six were running at once, because every double-click started another. Not a cosmetic problem: a
panel that watches this computer itself runs an engine, so six copies are six SCM watchers
holding six handles to every service, six recovery timers racing to restart the same one, and
six schedulers firing the same job at 03:00.

These tests use the real kernel objects — there is nothing to fake usefully here, since the
whole mechanism *is* the kernel's, and a test against a stub would prove only that the stub
works. They take the claim, check a second one is refused, release it, and check the next
launch is let through.
"""

import threading

import pytest

from core import one_instance


@pytest.fixture(autouse=True)
def own_names(monkeypatch):
    """Names of this test's own, so a run cannot fight the app the developer has open.

    Learned the hard way in principle: a test that took the product's own mutex would report
    "already running" on any machine where Service Officer was in the tray, and pass or fail by
    what happened to be running.
    """
    monkeypatch.setattr(one_instance, "MUTEX_NAME",
                        "ServiceOfficer.Test.Mutex.7f3a")
    monkeypatch.setattr(one_instance, "EVENT_NAME",
                        "ServiceOfficer.Test.Event.7f3a")


def test_the_first_launch_gets_it_and_the_second_does_not():
    first = one_instance.claim()
    assert first is not None, "the first copy has to be allowed to run"
    try:
        assert one_instance.claim() is None, "a second copy started anyway"
    finally:
        first.release()


def test_the_claim_comes_free_when_the_holder_lets_go():
    """A named mutex rather than a lock file, for exactly this: a copy that is killed or
    crashes releases it immediately, and there is no stale file to leave the app unable to ever
    start again — which would be a worse failure than the one being fixed."""
    first = one_instance.claim()
    first.release()
    second = one_instance.claim()
    assert second is not None, "the app can no longer start at all"
    second.release()


def test_releasing_twice_is_not_an_error():
    claim = one_instance.claim()
    claim.release()
    claim.release()


# ── the second launch is not silently dropped ──────────────────────────────
def test_a_poke_reaches_the_running_copy():
    """Somebody who double-clicks the icon is asking to see the app. Exiting quietly would look
    like a double-click that did nothing at all."""
    woken = threading.Event()
    listener = one_instance.listen(woken.set)
    assert listener is not None, "nothing is listening"
    try:
        assert one_instance.poke() is True
        assert woken.wait(3.0), "the running copy was never told"
    finally:
        listener.stop()


def test_a_poke_with_nobody_listening_says_so():
    """The claim can be held by a copy that has not finished starting. Then there is nobody to
    ask, and the honest answer is False rather than a crash on a null handle.

    This one caught the listener having no way to stop: it found the *previous* test's thread
    still holding the event and reported somebody was there. Which is the real failure's shape,
    one process along — so the fix was to the product, not to the test.
    """
    assert one_instance.poke() is False


def test_the_listener_keeps_answering():
    """Manual-reset event, reset by the listener: a second double-click a minute later has to
    work too. An event left signalled would fire once and then spin."""
    hits = []
    ready = threading.Event()

    def seen():
        hits.append(1)
        ready.set()

    listener = one_instance.listen(seen)
    assert listener is not None
    try:
        for round_number in (1, 2, 3):
            ready.clear()
            assert one_instance.poke() is True
            assert ready.wait(3.0), f"no answer on double-click {round_number}"
    finally:
        listener.stop()
    assert len(hits) >= 3
