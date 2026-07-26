"""The Linux transport, against output captured from a real SUSE box.

Every fixture string here was pasted from `hanadev` (systemd 254). That is the
point: the systemd rules are tested against what systemd actually said, not
against what I assumed it would say — three of these tests exist because the real
output disagreed with the assumption.
"""

import pytest

from core import connectors, ssh_linux
from core import state as st

# --- captured verbatim -----------------------------------------------------
SHOW_TWO_UNITS = """MainPID=13254
ExecMainStatus=0
Id=sapb1servertools.service
ActiveState=active
SubState=running
UnitFileState=enabled

MainPID=1765
ExecMainStatus=0
Id=b1s50000.service
ActiveState=active
SubState=running
UnitFileState=enabled
"""

SHOW_SSHD = """Type=notify
MainPID=1715
Result=success
ExecMainStatus=0
Id=sshd.service
LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
ActiveEnterTimestamp=@1784916754
"""

LIST_JSON = (
    '[{"unit":"acpid.service","load":"not-found","active":"inactive",'
    '"sub":"dead","description":"acpid.service"},'
    '{"unit":"apparmor.service","load":"loaded","active":"active",'
    '"sub":"exited","description":"Load AppArmor profiles"},'
    '{"unit":"b1s50000.service","load":"loaded","active":"active",'
    '"sub":"running","description":"SAP Business One Service Layer"}]'
)

UNIT_FILES_JSON = (
    '[{"unit_file":"b1s50000.service","state":"enabled"},'
    '{"unit_file":"b1s50001.service","state":"disabled"},'
    '{"unit_file":"sshd.service","state":"enabled"}]'
)


def runner_for(answers):
    """A fake target: matches a command by substring, returns (code, text)."""
    seen = []

    def run(command, timeout=15.0):
        seen.append(command)
        for needle, reply in answers.items():
            if needle in command:
                return reply
        return 1, f"unexpected command: {command}"

    run.seen = seen
    return run


class Machine:
    name = "hanadev"
    address = "192.168.230.2"
    port = 22
    username = "devadm"
    key_path = ""
    host_fingerprint = ""


# --- parsing ---------------------------------------------------------------
def test_properties_are_read_by_name_not_by_position():
    """Real output came back with MainPID first and Id fifth, though the request
    asked for Id first. Indexing into that would mismatch every field."""
    blocks = ssh_linux.parse_show(SHOW_TWO_UNITS)

    assert len(blocks) == 2
    assert blocks[0]["Id"] == "sapb1servertools.service"
    assert blocks[1]["MainPID"] == "1765"


def test_an_active_unit_reads_as_running():
    status = ssh_linux.status_from(ssh_linux.parse_show(SHOW_SSHD)[0])

    assert status.state == st.RUNNING
    assert status.sub_state == "running"
    assert status.pid == 1715
    assert status.exit_code == 0
    assert status.installed is True


def test_a_disabled_unit_is_manual_not_disabled():
    """systemd's disabled means "not at boot"; the unit still starts on demand.
    Windows' Disabled means "cannot start at all", which is why the UI greys
    Start. Mapping the matching word would grey out a button that works."""
    manual = ssh_linux.status_from({"LoadState": "loaded",
                                    "ActiveState": "inactive",
                                    "UnitFileState": "disabled"})
    refused = ssh_linux.status_from({"LoadState": "loaded",
                                     "ActiveState": "inactive",
                                     "UnitFileState": "masked"})

    assert manual.start_type == "Manual"
    assert refused.start_type == "Disabled"


def test_a_unit_systemd_cannot_load_is_not_merely_stopped():
    """That box has four b1s5000x units in state "bad", left by an uninstall.
    Calling them Stopped would have the watchdog start what can never start."""
    for load in ("not-found", "bad", "error"):
        status = ssh_linux.status_from({"LoadState": load,
                                        "ActiveState": "inactive"})
        assert status.installed is False, load
        assert status.state == st.NOT_FOUND
        assert status.detail, "nothing to tell the user why"


def test_a_failed_unit_carries_its_exit_code():
    """So a crash reads as a crash, like a Windows service exit code."""
    status = ssh_linux.status_from({
        "LoadState": "loaded", "ActiveState": "failed", "SubState": "failed",
        "Result": "exit-code", "ExecMainStatus": "1", "MainPID": "0"})

    assert status.state == st.STOPPED
    assert status.exit_code == 1
    assert "exit-code" in status.detail


def test_a_healthy_unit_reports_no_exit_code():
    """ExecMainStatus is 0 on success — it must not read as a failure."""
    status = ssh_linux.status_from(ssh_linux.parse_show(SHOW_SSHD)[0])
    assert status.exit_code == 0 and status.detail == ""


def test_transitions_map_to_the_shared_vocabulary():
    def state(active):
        return ssh_linux.status_from({"LoadState": "loaded",
                                      "ActiveState": active}).state

    assert state("activating") == "Starting"
    assert state("deactivating") == "Stopping"
    assert state("reloading") == st.RUNNING      # still serving
    assert state("inactive") == st.STOPPED


def test_the_timestamp_is_unix_so_no_timezone_is_guessed():
    """--timestamp=unix gives @1784916754, which is 2026-07-24 18:12:34Z — the
    same instant systemd prints as 20:12:34 CEST. Parsing "CEST" is how a
    daylight-saving bug gets in."""
    from datetime import datetime, timezone

    seconds = ssh_linux._timestamp("@1784916754")

    assert datetime.fromtimestamp(seconds, timezone.utc).isoformat() == \
        "2026-07-24T18:12:34+00:00"
    assert ssh_linux._timestamp("") == 0.0
    assert ssh_linux._timestamp("@0") == 0.0


# --- the connector ---------------------------------------------------------
def test_the_picker_marks_units_that_are_not_installed():
    run = runner_for({"list-units": (0, LIST_JSON),
                      "list-unit-files": (0, UNIT_FILES_JSON)})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    found = {s.name: s for s in conn.list_services()}

    assert found["acpid.service"].installed is False
    assert found["b1s50000.service"].status == st.RUNNING
    assert found["b1s50000.service"].display == "SAP Business One Service Layer"
    # Installed but never loaded still has to be offerable.
    assert found["b1s50001.service"].start_type == "Manual"


def test_several_units_cost_one_round_trip():
    """Polling a host must not be one connection per service."""
    run = runner_for({"systemctl show": (0, SHOW_TWO_UNITS)})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    got = conn.statuses(["sapb1servertools.service", "b1s.service"])

    assert len(run.seen) == 1, run.seen
    assert got["sapb1servertools.service"].pid == 13254
    # Asked for by its alias, answered under its real name — the caller still
    # gets its answer under the name it used.
    assert got["b1s.service"].pid == 1765


def test_a_mismatched_answer_falls_back_to_asking_one_at_a_time():
    """Rather than guess which block belongs to which unit."""
    run = runner_for({"systemctl show": (0, SHOW_SSHD)})   # one block, two asked
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    got = conn.statuses(["a.service", "b.service"])

    assert set(got) == {"a.service", "b.service"}
    assert len(run.seen) == 3            # the batch, then one each


def test_no_sudo_means_watch_but_do_not_touch():
    """Measured on the real box: devadm has no passwordless sudo. That is a
    monitoring target, not a broken one."""
    run = runner_for({"sudo -n true": (1, "sudo: a password is required"),
                      "journalctl -n 0": (0, ""),
                      "systemctl show": (0, SHOW_SSHD)})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    can = conn.abilities()

    assert can.control is False and can.kill is False
    assert "sudo" in can.why
    assert conn.status("sshd.service").state == st.RUNNING     # reading is fine
    with pytest.raises(RuntimeError, match="sudo"):
        conn.start("sshd.service")


def test_no_journal_means_no_logs_and_says_so():
    """Without the systemd-journal group the journal is unreadable, so the log view
    has nothing to show."""
    run = runner_for({"sudo -n true": (0, ""),
                      "journalctl -n 0": (1, "No journal files were opened due "
                                             "to insufficient permissions.")})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    can = conn.abilities()

    assert can.control is True
    assert can.push is False and can.logs is False
    assert "journal" in can.why


def test_a_readable_journal_is_still_not_a_doorbell():
    """The bug this exists to prevent, seen on a real machine.

    `push` was set from whether the journal could be *read*, but nothing follows it —
    there is no `journalctl -f` anywhere in this app. Claiming push told the poller
    to leave the machine alone, and root can always read the journal, so all four
    SAP services on the SUSE box sat at "Unknown" for as long as the app ran. Reading
    logs and being told about changes are different abilities.
    """
    run = runner_for({"sudo -n true": (0, ""), "journalctl -n 0": (0, "")})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    can = conn.abilities()

    assert can.logs is True
    assert can.push is False, "said it would tell us, and nothing is listening"


def test_control_verbs_go_through_sudo_and_report_why_they_failed():
    run = runner_for({"sudo -n true": (0, ""), "journalctl -n 0": (0, ""),
                      "systemctl restart": (1, "Job for x.service failed."),
                      "systemctl start": (0, "")})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    conn.start("x.service")

    assert any(c.startswith("sudo -n systemctl start") for c in run.seen)
    with pytest.raises(RuntimeError, match="Job for x.service failed"):
        conn.restart("x.service")


def test_file_age_is_measured_by_the_targets_clock():
    """Two machines are never quite in step, so the age of a file over there has
    to be computed over there."""
    run = runner_for({"stat -c": (0, "1784916700\n1784916754")})
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    exists, age = conn.stat("/var/log/b1s.log")

    assert exists is True and age == 54.0


def test_a_missing_file_is_not_an_error():
    run = runner_for({"stat -c": (0, "1784916754")})    # only `date` answered
    conn = ssh_linux.LinuxConnector(Machine(), runner=run)
    assert conn.stat("/nope") == (False, 0.0)


def test_an_unknown_host_key_is_refused_rather_than_trusted():
    """An SSH client that accepts any key it is handed has no security property
    left — the first connection is the one an attacker wants."""
    class Key:
        def asbytes(self):
            return b"pretend key"

    policy = ssh_linux._PinnedHostKey(Machine())
    with pytest.raises(ConnectionError, match="not been trusted"):
        policy.missing_host_key(None, "hanadev", Key())

    class Pinned(Machine):
        host_fingerprint = "SHA256:definitely-not-this-one"

    policy = ssh_linux._PinnedHostKey(Pinned())
    with pytest.raises(ConnectionError, match="Refusing to connect"):
        policy.missing_host_key(None, "hanadev", Key())


def test_it_satisfies_the_connector_protocol():
    """Whatever else changes, the seam is the contract."""
    conn = ssh_linux.LinuxConnector(Machine(), runner=runner_for({}))
    for verb in ("abilities", "reachable", "list_services", "status", "start",
                 "stop", "restart", "kill", "logs", "run", "stat"):
        assert callable(getattr(conn, verb)), verb


def test_fetching_a_fingerprint_needs_no_credentials(monkeypatch):
    """A server offers its host key before authentication, which is why this can
    fill the field in without a password — and why it is discovery, not proof."""
    import base64
    import hashlib

    class Key:
        def asbytes(self):
            return b"the host key bytes"

    class Transport:
        def __init__(self, sock):
            self.closed = False

        def start_client(self, timeout=None):
            pass

        def get_remote_server_key(self):
            return Key()

        def close(self):
            self.closed = True

    class Sock:
        def close(self):
            pass

    fake = type("paramiko", (), {"Transport": Transport})
    monkeypatch.setitem(__import__("sys").modules, "paramiko", fake)
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: Sock())

    got = ssh_linux.fingerprint_of("192.168.230.2")

    expected = "SHA256:" + base64.b64encode(
        hashlib.sha256(b"the host key bytes").digest()).decode().rstrip("=")
    assert got == expected
    assert got.startswith("SHA256:") and "=" not in got   # ssh's own formatting


SHOW_WEBCLIENT = """Id=webclient.service
LoadState=loaded
ActiveState=active
SubState=exited
UnitFileState=enabled
Type=oneshot
RemainAfterExit=yes
MainPID=0
ExecMainStatus=0
Result=success
ActiveEnterTimestamp=@1784916754
"""


def test_a_oneshot_that_stays_active_is_a_running_service():
    """SAP's web client unit: Type=oneshot with RemainAfterExit, because its start
    script launches an application server and exits. systemd calls that
    active (exited), and it means the service is up."""
    status = ssh_linux.status_from(ssh_linux.parse_show(SHOW_WEBCLIENT)[0])

    assert status.state == st.RUNNING
    assert status.sub_state == "exited"
    assert status.pid == 0, "a oneshot has no main process to track"
    assert "one-off job" not in status.detail


def test_a_oneshot_that_does_not_stay_active_is_a_job():
    """And "inactive" is how a finished job looks, not a service that stopped —
    getting this the other way round would have the watchdog re-running jobs."""
    status = ssh_linux.status_from({
        "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
        "Type": "oneshot", "RemainAfterExit": "no"})

    assert status.state == st.STOPPED
    assert "one-off job" in status.detail


def test_a_slow_start_is_not_reported_as_a_lost_connection():
    """A Type=oneshot start blocks until its script finishes, and a script that
    brings up a Java server can outlive any timeout. "Connection failed" would
    send someone to look at the network."""
    def run(command, timeout=15.0):
        if "sudo -n true" in command or "journalctl -n 0" in command:
            return 0, ""
        raise ConnectionError("timed out")

    conn = ssh_linux.LinuxConnector(Machine(), runner=run)

    with pytest.raises(RuntimeError, match="still running after"):
        conn.start("webclient.service")


def test_control_waits_long_enough_for_a_real_start():
    seen = {}

    def run(command, timeout=15.0):
        seen[command] = timeout
        return 0, ""

    conn = ssh_linux.LinuxConnector(Machine(), runner=run)
    conn.start("webclient.service")

    started = next(c for c in seen if "systemctl start" in c)
    assert seen[started] >= 300, "a Java application server needs longer than 90s"
