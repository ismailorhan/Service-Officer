"""The timeline."""

from core import history


# ---------------------------------------------------------------------------
# another machine's event log
# ---------------------------------------------------------------------------
def _record(ts, service, level="Error", event_id=7011, message="it timed out",
            source="Service Control Manager", summary="timed out"):
    """The shape both eventlog.read and winrm_windows.log_records produce."""
    return {"ts": ts, "service": service, "level": level, "event_id": event_id,
            "message": message, "source": source, "summary": summary}


def test_a_remote_machines_events_are_merged_and_say_whose_they_are(tmp_path):
    """`Abilities.logs` said yes for a WinRM machine and nothing ever asked, so the switch on
    the Machines page promised an event log the product never read. Merged now — and the row
    names the machine, because without that it is indistinguishable from one written here,
    which is the confusion that kept remote logs out of this timeline."""
    path = str(tmp_path / "h.db")
    history.record_action("B1ServerTools64", "stop", "panel", machine="sc-sql",
                          path=path)

    rows = history.query(
        service_names=["B1ServerTools64"], labels=["SAP Business One Server Tools"],
        include_windows=True, local_services=[], path=path,
        remote_events={"sc-sql": [_record("2026-07-16T10:55:38", "B1ServerTools64")]})

    windows = [r for r in rows if r["kind"] == "windows"]
    assert len(windows) == 1, "the machine's own event never arrived"
    assert "sc-sql" in windows[0]["source"], "nothing says which machine logged it"
    assert windows[0]["level"] == "Error"
    assert windows[0]["event"] == "timed out"
    # And it sits in the same timeline as what we asked for, in time order.
    assert [r["kind"] for r in rows].count("action") == 1


def test_a_remote_record_for_a_service_not_asked_about_is_dropped(tmp_path):
    """A machine answers for the service it was asked about. If a query is filtered to one
    service, another one's rows arriving would be a machine's log leaking into a page that
    said it was showing one thing."""
    path = str(tmp_path / "h.db")
    rows = history.query(
        service_names=["A"], service="A", include_windows=True, local_services=[],
        path=path,
        remote_events={"sc-sql": [_record("2026-07-16T10:00:00", "A"),
                                  _record("2026-07-16T10:00:01", "B")]})

    assert [r["service"] for r in rows if r["kind"] == "windows"] == ["A"]


def test_remote_events_are_ignored_when_windows_events_are_not_wanted(tmp_path):
    """The checkbox means the whole event log, not just this computer's half of it."""
    path = str(tmp_path / "h.db")
    rows = history.query(service_names=["A"], include_windows=False, local_services=[],
                         path=path,
                         remote_events={"sc-sql": [_record("2026-07-16T10:00:00", "A")]})

    assert not [r for r in rows if r["kind"] == "windows"]
