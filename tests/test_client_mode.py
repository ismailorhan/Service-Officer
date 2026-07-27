"""Embedded or connected, the interface must not be able to tell.

There is no *mode* to choose in the panel — there is an address. If the hub is installed
on this computer you point at this computer; if it is elsewhere you point there. What this
file checks is that the two ways of getting a store are wired to the same interface, and
that the connected one runs no engine of its own: a client that also polled would be a
second thing controlling the same services.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

import app as app_mod                               # noqa: E402
from core import config as cfg_mod                   # noqa: E402
from core import state as st                         # noqa: E402


class FakeHub:
    """A hub client that connects to nothing. What matters here is which object the
    application ends up reading, not whether a socket works — that is tested against a
    real server in test_hub_client and test_hub_roundtrip."""

    def __init__(self, url, token, fingerprint="", on_event=None,
                 on_connected=None):
        self.url, self.token, self.fingerprint = url, token, fingerprint
        self.store = st.Store()
        self.started = False
        self.connected = True
        self.checked = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def check_identity(self):
        self.checked = True
        return self.fingerprint or "SHA256:pinned-now"

    def ping(self):
        return {"protocol": 1, "version": "2.1.0", "name": "hub"}

    def refresh_now(self):
        return {"services": [], "machines": []}


@pytest.fixture
def qapp(monkeypatch):
    existing = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_mod, "QApplication", lambda _argv: existing)
    return existing


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    """This client's own file, somewhere harmless."""
    from core import local, secrets
    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))


def test_without_a_hub_it_runs_its_own_engine(qapp, monkeypatch):
    """What everybody has today, and what a single-machine install keeps."""
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    built = app_mod.Application([])

    assert built.engine is not None
    assert built.hub is None
    assert built.store is st.store


def test_with_a_hub_it_runs_none(qapp, monkeypatch):
    """A client that also ran an engine would be a second thing polling and restarting
    the same services — which is the failure the split exists to prevent."""
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)

    built = app_mod.Application(["--connect", "https://hub:8797",
                                 "--token", "given-once"])

    assert built.engine is None, "started an engine as well as connecting to one"
    assert built.hub.started is True
    assert built.store is built.hub.store


def test_the_token_is_stored_and_the_flag_is_not_needed_again(qapp, monkeypatch):
    """A token on a command line is visible in Task Manager while the process starts,
    so it is accepted once and kept in the DPAPI store."""
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)

    app_mod.Application(["--connect", "https://hub:8797", "--token", "abc123"])

    assert local.token("https://hub:8797") == "abc123"
    assert local.load().hub_url == "https://hub:8797"

    # Second launch, no flags at all: it reads client.json and connects.
    again = app_mod.Application([])
    assert again.hub is not None
    assert again.hub.token == "abc123"


def test_the_certificate_is_pinned_on_the_first_connection(qapp, monkeypatch):
    """And stored, so a change later is refused rather than accepted quietly."""
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)

    built = app_mod.Application(["--connect", "https://hub:8797",
                                 "--token", "abc123"])

    assert built.hub.checked is True
    assert local.load().hub_fingerprint == "SHA256:pinned-now"


def test_store_only_pairs_and_leaves(qapp, monkeypatch):
    """The installer's path: pair the client and exit, so no tray icon appears in the
    middle of an install and the first real launch is already connected."""
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)

    code = app_mod.pair_only(["--connect", "https://hub:8797", "--token", "xyz",
                              "--store-only"])

    assert code == 0
    assert local.token("https://hub:8797") == "xyz"
    assert local.load().hub_url == "https://hub:8797"


def test_an_action_goes_to_whichever_is_there(qapp, monkeypatch):
    """One method knows whether the work happens here or over there; nothing else
    does."""
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)
    asked = []

    connected = app_mod.Application(["--connect", "https://hub:8797",
                                     "--token", "abc"])
    connected.hub.act = lambda action, service, machine="", actor="": \
        asked.append(("hub", action, service)) or "id-1"
    connected.tray.action_started = lambda: None
    connected.do_action("restart", "AppEngine")
    assert asked == [("hub", "restart", "AppEngine")]

    # Unpaired, because pairing is remembered: a bare launch after the one above would
    # connect again, which is the point of remembering it and is tested elsewhere.
    settings = local.load()
    settings.hub_url = ""
    local.save(settings)

    embedded = app_mod.Application([])
    assert embedded.hub is None and embedded.engine is not None
    embedded.engine.act = lambda action, service, machine="", actor="", bulk=False: \
        asked.append(("engine", action, service)) or "id-2"
    embedded.tray.action_started = lambda: None
    embedded.do_action("restart", "AppEngine")
    assert asked[-1] == ("engine", "restart", "AppEngine")
