"""What any store has to provide, whether it holds the data or fetches it.

This exists because the coming remote store is not a subclass of Store: it will
answer the same questions by asking a hub over HTTP instead of reading a dict. A
method the interface calls but the remote store forgot is an AttributeError at the
moment somebody hovers a row — so the surface is written down here, checked against
the real Store, and the remote one will be checked against the same list.

The list is grown from `grep -rhoE "(store|_store)\.[a-z_]+" ui/ app.py`, not from
memory: an earlier draft of the plan guessed `all_states` and `state_of`, and the
grep proved the interface uses `snapshot`, `counts` and `any_pending` instead.
"""

from core import state as st


def test_the_read_api_is_declared():
    assert isinstance(st.READ_API, tuple) and st.READ_API


def test_the_write_api_is_declared():
    assert isinstance(st.WRITE_API, tuple) and st.WRITE_API


def test_the_local_store_satisfies_the_read_api():
    store = st.Store()
    missing = [name for name in st.READ_API
               if not callable(getattr(store, name, None))]
    assert missing == [], f"Store is missing {missing}"


def test_the_local_store_satisfies_the_write_api():
    store = st.Store()
    missing = [name for name in st.WRITE_API
               if not callable(getattr(store, name, None))]
    assert missing == [], f"Store is missing {missing}"


def test_the_read_api_covers_what_the_interface_actually_calls():
    """The names the grep produced from ui/ and app.py. If the interface starts
    using another reader, it is added here — and the remote store then fails its own
    version of this test until it grows one too."""
    for name in ("status_of", "health_of", "health_detail", "is_disabled",
                 "start_type", "machine_state", "health_timing", "subscribe",
                 "snapshot", "counts", "any_pending"):
        assert name in st.READ_API, f"{name} is called by the UI but not declared"


def test_reads_and_writes_do_not_overlap():
    """A method is either a question or a change. `update` is not a reader and
    `status_of` is not a writer, and mixing them is how a remote store would try to
    apply a write it should have sent to the hub."""
    assert set(st.READ_API).isdisjoint(st.WRITE_API)


# ---------------------------------------------------------------------------
# the same answers, not merely the same method names
# ---------------------------------------------------------------------------
# Existence was never enough, and it cost a crash on startup to learn it:
# `RemoteStore.counts()` returned three values where `Store.counts()` returns two, so
# `running, total = self._store.counts()` in the tray raised ValueError the moment the
# app was launched as a client — on a machine that had just been installed, which is the
# worst possible moment. Every test above passed, because the method was *there*.
#
# So this compares what the two stores actually answer, given the same facts.
def _both_stores():
    """A local store and a remote one, told the same things, ready to be asked."""
    from core import hub_client, wire
    from core import config as cfg_mod

    services = [cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                cfg_mod.Service(name="WMSServer"),
                cfg_mod.Service(name="webclient.service", machine="sd")]
    local = st.Store()
    local.update("AppEngine", st.RUNNING)
    local.update("WMSServer", st.STOPPED)
    local.update("webclient.service", st.RUNNING, machine="sd")
    local.set_health("AppEngine", st.UNHEALTHY, "connection refused")
    local.set_start_type("WMSServer", "Disabled")
    local.note_machine("sd", True, "")

    remote = hub_client.RemoteStore()
    remote.apply_snapshot({
        "services": [wire.service_row(s, local) for s in services],
        "machines": [wire.machine_row(m, local) for m in
                     (cfg_mod.Machine(), cfg_mod.Machine(name="sd", kind="linux"))],
    })
    return local, remote


def test_every_reader_answers_with_the_same_shape():
    """Type for type, and length for length where it is a tuple. A different shape is a
    crash in whichever line of the interface unpacks it."""
    local, remote = _both_stores()
    asked = {
        "status_of": ("AppEngine", ),
        "health_of": ("AppEngine", ),
        "health_detail": ("AppEngine", ),
        "is_disabled": ("WMSServer", ),
        "start_type": ("WMSServer", ),
        "health_timing": ("AppEngine", ),
        "machine_state": ("sd", ),
        "counts": (),
        "any_pending": (),
        "snapshot": (),
    }
    for name, args in asked.items():
        mine = getattr(local, name)(*args)
        theirs = getattr(remote, name)(*args)
        assert type(mine) is type(theirs), \
            f"{name}: local gives {type(mine).__name__}, remote gives {type(theirs).__name__}"
        if isinstance(mine, tuple):
            assert len(mine) == len(theirs), \
                f"{name}: local gives {len(mine)} values, remote gives {len(theirs)}"
        if isinstance(mine, dict) and mine and theirs:
            assert set(mine) == set(theirs), \
                f"{name}: local keys {sorted(mine)}, remote keys {sorted(theirs)}"


def test_counts_means_the_same_thing_on_both():
    """(running, total). The tray unpacks exactly two and colours the icon from them."""
    local, remote = _both_stores()

    assert local.counts() == (2, 3)
    assert remote.counts() == (2, 3)


def test_get_returns_something_the_interface_can_read(): 
    """Both answer with a ServiceState, and both answer None for a service nobody has
    heard of — the callers test for None."""
    local, remote = _both_stores()

    for store in (local, remote):
        found = store.get("AppEngine")
        assert isinstance(found, st.ServiceState), store
        assert found.status == st.RUNNING
        assert store.get("NoSuchService") is None, store
