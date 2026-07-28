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
from core import hub_client as hub_client_mod        # noqa: E402
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
    middle of an install and the first real launch is already connected.

    The certificate is pinned here too — at install time the address came from whoever is
    deploying, and on first launch it comes from whatever answers. Both are
    trust-on-first-use; only one is under the administrator's control. Found missing by
    running the *built* exe against a real TLS hub, which is the only place it shows.
    """
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    monkeypatch.setattr(app_mod.hub_client, "HubClient", FakeHub)

    code = app_mod.pair_only(["--connect", "https://hub:8797", "--token", "xyz",
                              "--store-only"])

    assert code == 0
    assert local.token("https://hub:8797") == "xyz"
    assert local.load().hub_url == "https://hub:8797"
    assert local.load().hub_fingerprint == "SHA256:pinned-now"


def test_store_only_survives_a_hub_that_is_not_up_yet(qapp, monkeypatch):
    """A workstation can be imaged before the server exists. The token is kept and the
    install finishes; the certificate is pinned by the first launch that can reach it."""
    from core import local
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())

    class Unreachable(FakeHub):
        def check_identity(self):
            raise hub_client_mod.Unreachable("no route to host")

    monkeypatch.setattr(app_mod.hub_client, "HubClient", Unreachable)

    code = app_mod.pair_only(["--connect", "https://hub:8797", "--token", "xyz",
                              "--store-only"])

    assert code == 0, "an unreachable hub failed the install"
    assert local.token("https://hub:8797") == "xyz"
    assert local.load().hub_fingerprint == ""


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


# ---------------------------------------------------------------------------
# administrator rights, asked for only when they are needed
# ---------------------------------------------------------------------------
def test_a_client_of_a_hub_never_asks_for_administrator(monkeypatch):
    """The UAC prompt on every launch was the most visible cost this app charged, and a
    client that only reads a hub has no reason to charge it: the hub does the work, and
    the hub runs as LocalSystem."""
    from core import local

    settings = local.load()
    settings.hub_url = "https://hub:8797"
    local.save(settings)
    monkeypatch.setattr(app_mod, "_is_elevated", lambda: False)

    assert app_mod.needs_elevation(["ServiceOfficer.exe"]) is False


def test_an_embedded_install_still_asks_for_it(monkeypatch):
    """Because it drives this computer's service manager itself, and without the rights
    every button would fail with access denied — which is worse than a prompt.

    This is the deviation from the plan, which had the manifest simply moved to the hub:
    that is right for a client and wrong for the single-machine install everybody has
    today, so the answer is asked at run time instead of baked into a manifest.
    """
    monkeypatch.setattr(app_mod, "_is_elevated", lambda: False)

    assert app_mod.needs_elevation(["ServiceOfficer.exe"]) is True


def test_already_elevated_is_not_asked_twice(monkeypatch):
    monkeypatch.setattr(app_mod, "_is_elevated", lambda: True)

    assert app_mod.needs_elevation(["ServiceOfficer.exe"]) is False


def test_pairing_on_the_command_line_counts_as_being_a_client(monkeypatch):
    """The installer's own call, before client.json exists: `--connect ... --store-only`
    must not raise a prompt in the middle of an unattended install."""
    monkeypatch.setattr(app_mod, "_is_elevated", lambda: False)

    assert app_mod.needs_elevation(
        ["ServiceOfficer.exe", "--connect", "https://hub:8797"]) is False


# ---------------------------------------------------------------------------
# the real interface, against a real RemoteStore
# ---------------------------------------------------------------------------
# The FakeHub above carries a *local* Store, which is what let a real crash through: the
# remote store answered counts() with three values where the local one answers two, and
# `running, total = self._store.counts()` in the tray raised ValueError the moment the app
# started as a client — on a machine that had just been installed.
#
# So this drives the actual widgets with the actual RemoteStore, filled the way the hub
# fills it, through the same calls startup makes.
class RemoteHub(FakeHub):
    """A hub client with the real RemoteStore behind it, holding real rows."""

    def __init__(self, url, token, fingerprint="", on_event=None, on_connected=None):
        super().__init__(url, token, fingerprint, on_event, on_connected)
        from core import hub_client, wire
        self.store = hub_client.RemoteStore()
        services = [cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                    cfg_mod.Service(name="WMSServer", label="CompuTec WMS"),
                    cfg_mod.Service(name="webclient.service", machine="sd",
                                    label="SAP Web Client")]
        source = st.Store()
        source.update("AppEngine", st.RUNNING)
        source.update("WMSServer", st.STOPPED)
        source.update("webclient.service", st.RUNNING, machine="sd")
        source.set_health("AppEngine", st.UNHEALTHY, "connection refused")
        source.note_machine("sd", True, "")
        self.store.apply_snapshot({
            "services": [wire.service_row(s, source) for s in services],
            "machines": [wire.machine_row(m, source) for m in
                         (cfg_mod.Machine(),
                          cfg_mod.Machine(name="sd", kind="linux"))],
        })


def _connected_app(monkeypatch):
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(), cfg_mod.Machine(name="sd", kind="linux")],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                  cfg_mod.Service(name="WMSServer", label="CompuTec WMS"),
                  cfg_mod.Service(name="webclient.service", machine="sd",
                                  label="SAP Web Client")])
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg)
    monkeypatch.setattr(app_mod.hub_client, "HubClient", RemoteHub)
    return app_mod.Application(["--connect", "https://hub:8797", "--token", "t"])


def test_the_tray_paints_from_a_remote_store(qapp, monkeypatch):
    """The exact line that crashed: the tray unpacks counts() and colours the icon."""
    built = _connected_app(monkeypatch)

    built.tray.apply_state()          # raised ValueError before counts() was fixed

    assert built.store.counts() == (2, 3)


def test_everything_that_shows_a_state_survives_a_remote_store(qapp, monkeypatch):
    """The whole of _refresh_lists, which is what every event calls — with the flyout and
    the dashboard made visible so none of them is skipped by an `isVisible()` guard."""
    built = _connected_app(monkeypatch)
    built.flyout.popup()
    built.open_panel()

    built._refresh_lists()

    assert built.flyout.isVisible()
    assert built.panel is not None
    built.panel.close()
    built.flyout.hide()


def test_the_hover_card_reads_a_remote_store(qapp, monkeypatch):
    """It asks per service rather than in bulk — health, start type, the machine's own
    reachability — so it touches the most of the read API."""
    built = _connected_app(monkeypatch)

    built.hover.refresh()
    built.hover._render()        # what request() gets to once the pointer settles

    assert built.store.health_of("AppEngine") == st.UNHEALTHY
    built.hover.hide()


def test_quitting_a_client_does_not_raise(qapp, monkeypatch):
    """`self.engine` is None on a client, and quit() stopped it unconditionally. It never
    got noticed because the app closed anyway — the reader is a daemon thread — but an
    AttributeError on the way out is still an AttributeError, and it skipped the hub's own
    clean close."""
    built = _connected_app(monkeypatch)
    built.tray.hide = lambda: None
    built.qt.quit = lambda: None

    built.quit()                      # raised AttributeError before

    assert built.hub.started is False, "the hub connection was left open"


def test_quitting_an_embedded_install_still_stops_the_engine(qapp, monkeypatch):
    monkeypatch.setattr(cfg_mod, "load", lambda path=None: cfg_mod.Config())
    built = app_mod.Application([])
    stopped = []
    built.engine.stop = lambda: stopped.append(True)
    built.tray.hide = lambda: None
    built.qt.quit = lambda: None

    built.quit()

    assert stopped == [True]
