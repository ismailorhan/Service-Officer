"""A client and its hub have to be the same release.

The hub has reported its version since the first day and nothing ever compared it except a
Test button somebody had to press. So a client left on an old release joined a newer hub, read
a wire it did not fully understand, and showed whatever that produced — silently, and
confidently. A panel that refuses to connect is a question; a panel showing the wrong state is
not.

This is also the precondition for any update story: "update the hub, then the clients" is not
an order anything can enforce until a mismatch is something the product notices.
"""

import pytest

from core import hub_client, version


# ── the rule, in one place ────────────────────────────────────────────────
def test_the_build_number_is_not_part_of_the_contract():
    """2.2.7.16 and 2.2.7.17 are the same release built twice. Refusing between them would
    make every local build a migration."""
    assert version.compatible("2.2.7.16", "2.2.7.17")
    assert version.compatible("2.2.7", "2.2.7.99")
    assert version.compatible("2.2.7.4", "2.2.7")


def test_a_different_release_is_refused():
    assert not version.compatible("2.3.0", "2.2.7")
    assert not version.compatible("2.2.8", "2.2.7")
    assert not version.compatible("3.0.0", "2.2.7")


def test_a_version_nobody_can_read_is_let_through():
    """This decides whether a client connects at all. Refusing everything because a field
    arrived in a shape nobody anticipated would take the product down over a string."""
    assert version.compatible("", "2.2.7")
    assert version.compatible("dev", "2.2.7")
    assert version.compatible(None, "2.2.7")
    assert version.compatible("2.2", "2.2.7")


def test_the_panel_and_the_client_cannot_disagree():
    """The Test button spelled the comparison out for itself. Two copies of a rule are two
    rules, and "Test says they match" beside "the panel will not connect" is worse than
    either answer alone."""
    import inspect
    from ui.pages import hub as hub_page
    source = inspect.getsource(hub_page.HubPage._test_hub)
    assert "version.compatible" in source
    assert 'split(".")[:3]' not in source, "the rule was spelled out again"


# ── the handshake ─────────────────────────────────────────────────────────
class _Hub:
    """Answers /ping and nothing else — which is all the handshake needs, and is the point:
    the check happens before the token is used, so a client on the wrong release is told that
    rather than told its token is the problem."""

    def __init__(self, says):
        self.says = says
        self.asked = 0

    def ping(self):
        self.asked += 1
        return {"name": "CTL052", "version": self.says}


def _client(says):
    client = hub_client.HubClient.__new__(hub_client.HubClient)
    client.ping = _Hub(says).ping
    return client


def test_a_matching_hub_is_accepted(monkeypatch):
    monkeypatch.setattr(version, "VERSION", "2.2.7")
    monkeypatch.setattr(version, "BUILD", 16)
    assert _client("2.2.7.17").check_version() == "2.2.7.17"


def test_a_mismatched_hub_is_refused_by_class_not_by_text(monkeypatch):
    """Its own class so the reader loop can word it. "Cannot reach the hub" is exactly what
    this is not — the hub answered, promptly and correctly."""
    monkeypatch.setattr(version, "VERSION", "2.2.7")
    monkeypatch.setattr(version, "BUILD", 0)
    with pytest.raises(hub_client.WrongVersion) as caught:
        _client("2.3.0").check_version()
    said = str(caught.value)
    assert "2.2.7" in said and "2.3.0" in said, said
    assert "reach" not in said.lower(), "worded as if the hub were unreachable"


def test_the_reader_loop_asks_before_it_opens_the_stream():
    """Order is the whole point: a mismatched client must never read a wire it may not
    understand, and the stream is where the wire starts."""
    import inspect
    source = inspect.getsource(hub_client.HubClient._listen)
    assert "check_version()" in source, "the handshake is not in the loop"
    assert source.index("check_version()") < source.index("_open_stream"), \
        "the stream is opened before the versions are compared"


def test_a_refusal_is_treated_as_the_hub_being_down():
    """Every button in the app guards on HUB_IS_DOWN. A new failure class outside it is not a
    message on a row, it is "Failed to execute script"."""
    import app
    assert hub_client.WrongVersion in app.HUB_IS_DOWN


def test_the_reason_reaches_the_screen():
    """The tray and the panel said "cannot reach the hub" for every failure. For this one the
    hub answers instantly, and that wording sends somebody to look at a firewall for an hour."""
    import inspect
    from ui import dashboard, flyout
    for where in (dashboard.DashboardPage.apply_states, flyout.Flyout.apply_states):
        source = inspect.getsource(where)
        assert '"why"' in source, f"{where.__qualname__} ignores the reason"
    assert "why" in inspect.getsource(hub_client.HubClient._listen)
