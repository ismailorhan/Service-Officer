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
