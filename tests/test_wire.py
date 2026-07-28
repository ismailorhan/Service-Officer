"""The wire format, on its own, with no server anywhere near it.

Written before the server so the server has nothing to invent, and so the browser UI
that comes later has one file to read rather than a protocol to infer from handler
code. Everything here is a dict in and a dict out: no sockets, no state, no threads.
"""

import json

from core import config as cfg_mod
from core import engine as engine_mod
from core import state as st
from core import wire


def _cfg():
    return cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="hanadev", label="hanadev", kind="linux",
                                  address="hanadev", username="root")],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine",
                                  category="SAP"),
                  cfg_mod.Service(name="webclient.service", machine="hanadev")])


def test_a_service_row_carries_what_a_row_draws():
    cfg = _cfg()
    store = st.Store()
    store.update("webclient.service", st.RUNNING, machine="hanadev")
    store.set_health("webclient.service", "starting", "started just now",
                     machine="hanadev")

    row = wire.service_row(cfg.services[1], store)

    assert row["name"] == "webclient.service"
    assert row["machine"] == "hanadev"
    assert row["status"] == st.RUNNING
    assert row["health"] == "starting"
    assert row["health_detail"] == "started just now"
    # It has to survive the journey unchanged — no dataclasses, no tuples, no sets.
    assert json.loads(json.dumps(row)) == row


def test_a_machine_row_says_whether_it_answered():
    cfg = _cfg()
    store = st.Store()
    store.note_machine("hanadev", False, "TimeoutError: timed out")

    row = wire.machine_row(cfg.machines[1], store)

    assert row["name"] == "hanadev"
    assert row["kind"] == "linux"
    assert row["reachable"] is False
    assert row["detail"] == "TimeoutError: timed out"
    assert isinstance(row["at"], (int, float))
    assert json.loads(json.dumps(row)) == row


def test_a_machine_never_asked_says_so_rather_than_guessing():
    """"not asked yet" is a state of its own — it was the one in play the evening
    every SUSE service read Unknown."""
    cfg = _cfg()
    row = wire.machine_row(cfg.machines[1], st.Store())

    assert row["reachable"] is None
    assert row["at"] == 0


def test_a_snapshot_declares_its_protocol_and_version():
    """A client from a different release has to be able to say so out loud rather
    than misreading fields it does not know about."""
    cfg = _cfg()
    built = engine_mod.Engine(lambda: cfg, store=st.Store())
    shot = wire.snapshot(built)

    assert shot["protocol"] == wire.PROTOCOL
    assert shot["version"]
    assert {r["name"] for r in shot["services"]} == {"AppEngine",
                                                    "webclient.service"}
    assert len(shot["machines"]) == 2
    assert shot["config_etag"] == wire.etag(cfg)
    assert json.loads(json.dumps(shot)) == shot


def test_the_config_survives_a_round_trip_with_its_etag():
    cfg = _cfg()
    payload = wire.config_payload(cfg)
    back, tag = wire.config_from_payload(json.loads(json.dumps(payload)))

    assert tag == payload["etag"] == wire.etag(cfg)
    assert [s.name for s in back.services] == [s.name for s in cfg.services]
    assert back.machine("hanadev").username == "root"


def test_the_etag_changes_when_anything_does():
    """Two clients editing at once is what it is for: the second save is refused
    rather than silently winning, which is how a machine someone added disappears."""
    cfg = _cfg()
    before = wire.etag(cfg)
    cfg.services[0].label = "Something else"

    assert wire.etag(cfg) != before


def test_the_etag_survives_the_round_trip_it_will_actually_make():
    """A client is handed a config and hands it back. If the etag changed on the way,
    every save would be told it had conflicted with itself.

    It changed: `from_dict` repairs as well as parses — a service with no label gets
    its name, a category a service names is created — so the sent and received
    documents differ. The etag is taken over the repaired form for exactly this
    reason.
    """
    cfg = _cfg()
    same = cfg_mod.from_dict(json.loads(json.dumps(cfg_mod.to_dict(cfg))))
    assert wire.etag(same) == wire.etag(cfg)

    # And the repair reaches a fixed point, so a second trip cannot move it either.
    twice = cfg_mod.from_dict(json.loads(json.dumps(cfg_mod.to_dict(same))))
    assert wire.etag(twice) == wire.etag(cfg)
    assert wire.normalised(twice) == wire.normalised(cfg)


def test_the_etag_ignores_the_order_of_keys_but_not_of_services():
    """Key order is a spelling of the same document; the order of services is what
    the flyout shows, so it is part of the document."""
    cfg = _cfg()
    swapped = cfg_mod.from_dict(cfg_mod.to_dict(cfg))
    swapped.services.reverse()
    assert wire.etag(swapped) != wire.etag(cfg)


def test_a_secret_is_never_on_the_wire():
    """services.json holds the *name* of a secret store entry, and that is all the
    hub may hand out. A client has no business receiving a password."""
    cfg = _cfg()
    cfg.machines[1].auth = "password"
    cfg.machines[1].secret_ref = "machine/hanadev"

    text = json.dumps(wire.config_payload(cfg))

    assert "machine/hanadev" in text          # the reference is fine
    for forbidden in ("password=", "secret_value", "CTsa"):
        assert forbidden not in text


def test_an_event_says_what_kind_it_is():
    made = wire.event("status", service="AppEngine", machine="", status=st.RUNNING)

    assert made["kind"] == "status"
    assert made["protocol"] == wire.PROTOCOL
    assert made["service"] == "AppEngine"
    assert isinstance(made["at"], float)
    assert json.loads(json.dumps(made)) == made


def test_a_state_event_becomes_a_wire_event_and_back():
    """The client turns these back into st.Event objects so the existing handlers
    work unchanged, so the trip has to be lossless in both directions."""
    store = st.Store()
    caught = []
    store.subscribe(caught.append)
    store.update("AppEngine", st.RUNNING, exit_code=0, pid=4242)

    made = wire.event_from_state(caught[0])
    assert made["kind"] == "status"
    assert made["service"] == "AppEngine"
    assert made["status"] == st.RUNNING
    assert made["pid"] == 4242

    back = wire.state_from_event(json.loads(json.dumps(made)))
    assert back.name == "AppEngine"
    assert back.status == st.RUNNING
    assert back.state.pid == 4242
    assert back.previous == caught[0].previous


def test_a_row_carries_the_verdict_rather_than_the_raw_status_alone():
    """A service that is Running and failing its checks is *not responding*, and every
    surface has to say so identically. `st.effective()` exists because three of them
    once worked it out separately and disagreed at the same moment about the same
    service; a browser reading this API would have been the fourth, so the answer goes
    on the wire."""
    from core import config as cfg_mod

    store = st.Store()
    store.update("AppEngine", st.RUNNING)
    store.set_health("AppEngine", st.UNHEALTHY, "connection refused")

    row = wire.service_row(cfg_mod.Service(name="AppEngine"), store)

    assert row["status"] == st.RUNNING           # what the SCM says, unchanged
    assert row["state_label"] == st.LABEL_UNHEALTHY
    assert row["state_category"] == "stopped"    # so it is not drawn green
