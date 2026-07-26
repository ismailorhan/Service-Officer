"""Linux services over SSH: one of the transports behind `core/control.py`.

Whatever you can do to a service in a terminal — list them, read a status, start,
stop, restart, kill, read its log — this does, through `systemctl` and
`journalctl`. Nothing here is specific to any application: a unit is a unit.

Everything systemd-shaped is confined to this file. Above `control.py` a Linux
target is indistinguishable from a Windows one.

**The parsing is separate from the connection on purpose.** `run` is injected, so
every rule below is tested against output captured from a real SUSE box
(systemd 254) without a network in sight. SSH is just one way to get that output.
"""

from __future__ import annotations

import json
import shlex
import threading
import time

from . import connectors
from . import state as st

#: What we ask `systemctl show` for. `--timestamp=unix` matters: the pretty form
#: is "Fri 2026-07-24 20:12:34 CEST", and parsing a timezone *abbreviation* is
#: exactly how a daylight-saving bug gets in. Unix time is UTC by definition.
SHOW_PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
                   "Type", "RemainAfterExit", "MainPID", "ExecMainStatus",
                   "Result", "ActiveEnterTimestamp")

#: systemd's ActiveState, in our vocabulary.
_ACTIVE_STATE = {
    "active": st.RUNNING,
    "reloading": st.RUNNING,        # still serving, just re-reading its config
    "inactive": st.STOPPED,
    "failed": st.STOPPED,           # with an exit code, so a crash reads as one
    "activating": "Starting",
    "deactivating": "Stopping",
}

#: UnitFileState, in the vocabulary the rest of the app already uses.
#:
#: The trap: systemd's `disabled` is **not** Windows' Disabled. On Windows a
#: disabled service cannot be started at all, which is why the UI greys Start.
#: A disabled unit starts perfectly well with `systemctl start` — it just won't
#: come up at boot, which is Windows' "Manual". The real equivalent of Disabled is
#: `masked`, where systemd refuses to start it. Mapping the matching *word*
#: instead of the matching *behaviour* would grey out a button that works.
_UNIT_FILE_STATE = {
    "enabled": "Automatic",
    "enabled-runtime": "Automatic",
    "linked": "Automatic",
    "linked-runtime": "Automatic",
    "static": "Static",             # cannot be enabled; pulled in by something
    "indirect": "Static",
    "generated": "Static",
    "transient": "Static",
    "disabled": "Manual",
    "masked": "Disabled",           # start is refused — this is the real one
    "masked-runtime": "Disabled",
}

#: A unit systemd knows of but cannot load. Not "stopped": there is nothing to
#: start, so the watchdog must leave it alone instead of fighting it. A real box
#: had four of these left behind by an uninstall.
_UNLOADABLE = ("not-found", "bad", "masked", "error")


# ---------------------------------------------------------------------------
# Parsing — no connection involved
# ---------------------------------------------------------------------------
def parse_show(text: str) -> list[dict]:
    """`systemctl show` output as one dict per unit, in the order asked for.

    Blocks are separated by a blank line, and the properties inside a block come
    back in whatever order systemd likes — the measured output put `MainPID`
    first and `Id` fifth despite the request order. So: never index, always read
    keys.
    """
    blocks, current = [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, value = line.partition("=")
        if key:
            current[key] = value
    if current:
        blocks.append(current)
    return blocks


def _timestamp(value: str) -> float:
    """`@1784916754` as a float, 0.0 if systemd had nothing to say (a unit that
    has never started answers `@0` or an empty string)."""
    value = (value or "").strip().lstrip("@")
    try:
        return float(value)
    except ValueError:
        return 0.0


def status_from(props: dict) -> connectors.Status:
    """One `systemctl show` block as a Status."""
    load = (props.get("LoadState") or "").lower()
    installed = load not in _UNLOADABLE
    if not installed:
        return connectors.Status(
            state=st.NOT_FOUND, installed=False,
            sub_state=load,
            start_type=_UNIT_FILE_STATE.get(
                (props.get("UnitFileState") or "").lower(), ""),
            detail={"not-found": "no such unit on that machine",
                    "bad": "the unit file is broken",
                    "masked": "the unit is masked",
                    "error": "the unit file could not be read"}.get(load, load))

    active = (props.get("ActiveState") or "").lower()
    state = _ACTIVE_STATE.get(active, st.UNKNOWN)
    # An exit code only means something when the unit actually failed; a healthy
    # service reports ExecMainStatus=0 and Result=success.
    failed = active == "failed" or (props.get("Result") or "success") != "success"
    try:
        code = int(props.get("ExecMainStatus") or 0)
    except ValueError:
        code = 0
    try:
        pid = int(props.get("MainPID") or 0)
    except ValueError:
        pid = 0
    detail = ""
    if failed and (props.get("Result") or "") not in ("", "success"):
        detail = f"result: {props['Result']}"
    # A oneshot unit that does *not* stay active is a job, and "inactive" is how a
    # finished job looks — not a service that stopped. One that does stay active
    # (RemainAfterExit) is a service whose start script exits, which is a real and
    # common shape: SAP's web client unit is exactly that. So the distinction is
    # RemainAfterExit, not Type, and getting it the other way round would have the
    # watchdog re-running completed jobs.
    if ((props.get("Type") or "").lower() == "oneshot"
            and (props.get("RemainAfterExit") or "no").lower() != "yes"):
        detail = ("a one-off job, not a lasting service"
                  + (f" · {detail}" if detail else ""))
    return connectors.Status(
        state=state,
        sub_state=(props.get("SubState") or ""),
        start_type=_UNIT_FILE_STATE.get(
            (props.get("UnitFileState") or "").lower(),
            (props.get("UnitFileState") or "")),
        pid=pid,
        exit_code=code if failed else 0,
        installed=True,
        detail=detail,
    )


def parse_list(text: str) -> list[dict]:
    """`systemctl list-units --output=json`. systemd 246+ speaks JSON, which is
    worth using: the column format is localised and has been rearranged before."""
    try:
        rows = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_unit_files(text: str) -> dict:
    """`systemctl list-unit-files --output=json` → {unit: state}.

    A second call, because the `list-units` JSON has no UnitFileState — that is
    the field the "only the enabled ones" filter needs.
    """
    out = {}
    for row in parse_list(text):
        name = row.get("unit_file") or row.get("unit") or ""
        if name:
            out[name] = (row.get("state") or "")
    return out


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------
class SshRunner:
    """One long-lived SSH connection that runs commands.

    Persistent on purpose: OpenSSH on Windows has no ControlMaster, so shelling
    out to ssh.exe would pay a full handshake per command — hundreds of
    milliseconds each, on something the user expects to feel live.
    """

    def __init__(self, machine):
        self._m = machine
        self._client = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:                      # pragma: no cover
            raise RuntimeError(
                "Managing Linux services needs the paramiko package.") from exc

        client = paramiko.SSHClient()
        # Never AutoAddPolicy. An SSH client that accepts any key it is handed is
        # an SSH client with no security property left: the first connection is
        # the one an attacker in the middle wants. The fingerprint is confirmed by
        # a person once, stored on the machine record, and enforced from then on.
        client.set_missing_host_key_policy(_PinnedHostKey(self._m))
        client.connect(
            hostname=self._m.address or self._m.name,
            port=self._m.port or 22,
            username=self._m.username or None,
            # Password auth and key auth are separate choices, so only the
            # chosen one is offered. Handing paramiko both makes it try the key
            # first and report *that* failure, which sends you to the wrong field.
            key_filename=(self._m.key_path or None
                          if self._m.auth != "password" else None),
            password=(_secret_for(self._m) or None
                      if self._m.auth == "password" else None),
            timeout=10, auth_timeout=10, banner_timeout=10,
            look_for_keys=not self._m.key_path, allow_agent=False,
        )
        # A dropped connection must show as "unreachable" rather than as a stale
        # green row, so we want to find out promptly.
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
        return client

    def __call__(self, command: str, timeout: float = 15.0):
        """(exit status, output). Never raises for a command that merely failed —
        a non-zero exit is an answer, not an error."""
        with self._lock:
            if self._client is None:
                self._client = self._connect()
            client = self._client
        try:
            _in, out, err = client.exec_command(command, timeout=timeout)
            text = out.read().decode("utf-8", "replace")
            code = out.channel.recv_exit_status()
            if not text.strip():
                text = err.read().decode("utf-8", "replace")
            return code, text.strip()
        except Exception as exc:
            self.close()                # so the next call reconnects
            raise ConnectionError(f"{type(exc).__name__}: {exc}") from exc


def fingerprint_of(host: str, port: int = 22, timeout: float = 10.0) -> str:
    """The host key `host` presents, as "SHA256:…", or "" if it did not answer.

    This is what `ssh` shows the first time you connect, and it is *discovery, not
    verification*: an attacker in the middle would hand us exactly this. It saves
    someone running ssh-keygen on the box, and the UI has to say plainly that
    confirming it against the machine itself is the part that makes it mean
    anything.

    No credentials involved — the key is offered before authentication.
    """
    import base64
    import hashlib
    import socket

    import paramiko
    sock = socket.create_connection((host, port or 22), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
    finally:
        transport.close()
        try:
            sock.close()
        except OSError:
            pass
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class _PinnedHostKey:
    """Accept the host key the machine record names, and nothing else."""

    def __init__(self, machine):
        self._m = machine

    def missing_host_key(self, client, hostname, key):
        import base64
        import hashlib
        got = "SHA256:" + base64.b64encode(
            hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        want = (self._m.host_fingerprint or "").strip()
        if not want:
            raise ConnectionError(
                f"{hostname} has not been trusted yet. Its key is {got} — "
                f"confirm it on the machine itself, then save it.")
        if got != want:
            raise ConnectionError(
                f"{hostname} presented {got}, but {want} was expected. "
                f"Refusing to connect.")


def _secret_for(machine):
    """The stored password for this target, or "".

    Read from the secret store, never from the config file — `services.json` is a
    document people are invited to hand-edit.
    """
    from . import secrets
    ref = getattr(machine, "secret_ref", "") or ""
    return secrets.get(ref) if ref else ""


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------
class LinuxConnector:
    """systemd through a runner. Give it any callable that takes a command and
    returns (exit status, output) and it works — SSH, or a test."""

    def __init__(self, machine, runner=None):
        self.machine = machine
        self.name = getattr(machine, "name", "") or ""
        self._run = runner or SshRunner(machine)
        self._can = None

    # -- capability probing -------------------------------------------------
    def abilities(self) -> connectors.Abilities:
        """Asked once and remembered: what this account may actually do.

        Reading a status needs no privilege at all. Control needs `sudo` without a
        password, and the journal needs group membership. A target where only the
        first is true is a perfectly good monitoring target — the UI disables the
        rest and says why, rather than offering buttons that fail.
        """
        if self._can is not None:
            return self._can
        why = []
        try:
            sudo_ok = self._run("sudo -n true", timeout=10)[0] == 0
        except ConnectionError as exc:
            self._can = connectors.Abilities(control=False, kill=False, logs=False,
                                             push=False, why=str(exc))
            return self._can
        if not sudo_ok:
            why.append("that account has no passwordless sudo, so services can be "
                       "watched but not controlled")
        journal_ok = self._run("journalctl -n 0 --quiet", timeout=10)[0] == 0
        if not journal_ok:
            why.append("it cannot read the journal, so changes are polled instead "
                       "of arriving instantly — add the account to the "
                       "systemd-journal group")
        self._can = connectors.Abilities(
            control=sudo_ok, kill=sudo_ok, logs=journal_ok, push=journal_ok,
            why=" · ".join(why))
        return self._can

    def reachable(self) -> bool:
        try:
            return self._run("true", timeout=10)[0] == 0
        except ConnectionError:
            return False

    # -- reading ------------------------------------------------------------
    def list_services(self) -> list:
        """Every service unit, for the picker. Two calls: one for what is loaded
        and running, one for whether each is enabled."""
        code, text = self._run(
            "systemctl list-units --type=service --all --output=json --no-pager")
        rows = parse_list(text) if code == 0 else []
        code, text = self._run(
            "systemctl list-unit-files --type=service --output=json --no-pager")
        files = parse_unit_files(text) if code == 0 else {}

        out = []
        for row in rows:
            unit = row.get("unit") or ""
            if not unit:
                continue
            load = (row.get("load") or "").lower()
            # `--all` lists units that are merely referenced somewhere and not
            # installed at all. Offering those to pick from would be noise.
            installed = load not in _UNLOADABLE
            state = _ACTIVE_STATE.get((row.get("active") or "").lower(), st.UNKNOWN)
            out.append(connectors.ServiceInfo(
                name=unit,
                display=(row.get("description") or unit),
                status=state if installed else st.NOT_FOUND,
                start_type=_UNIT_FILE_STATE.get(files.get(unit, "").lower(),
                                                files.get(unit, "")),
                installed=installed))
        for unit, file_state in files.items():
            if not any(s.name == unit for s in out):
                # Installed but not loaded — a disabled unit that has never run.
                out.append(connectors.ServiceInfo(
                    name=unit, display=unit, status=st.STOPPED,
                    start_type=_UNIT_FILE_STATE.get(file_state.lower(),
                                                    file_state)))
        out.sort(key=lambda s: s.display.lower())
        return out

    def statuses(self, names) -> dict:
        """Several units in one round trip — what polling a host should cost.

        systemd prints one block per unit in the order asked, so the answers are
        matched back positionally; `Id` is kept as well, because a unit asked for
        by an alias (`b1s.service`) answers under its real name (`b1s50000`).
        """
        names = list(names)
        if not names:
            return {}
        args = " ".join(shlex.quote(n) for n in names)
        props = ",".join(SHOW_PROPERTIES)
        code, text = self._run(f"systemctl show {args} --timestamp=unix "
                               f"--property={props} --no-pager")
        blocks = parse_show(text) if code == 0 or text else []
        if len(blocks) != len(names):
            # Anything unexpected and we ask one at a time rather than guess which
            # answer belongs to which unit.
            return {n: self.status(n) for n in names}
        return {name: status_from(block) for name, block in zip(names, blocks)}

    def status(self, name: str) -> connectors.Status:
        props = ",".join(SHOW_PROPERTIES)
        code, text = self._run(f"systemctl show {shlex.quote(name)} "
                               f"--timestamp=unix --property={props} --no-pager")
        blocks = parse_show(text)
        if not blocks:
            return connectors.Status(state=st.UNKNOWN, installed=True,
                                     detail=text[:200])
        return status_from(blocks[0])

    def started_at(self, name: str) -> float:
        """Unix time this unit last entered `active`, or 0.0 — the input to an
        uptime figure."""
        code, text = self._run(f"systemctl show {shlex.quote(name)} "
                               f"--timestamp=unix --property=ActiveEnterTimestamp")
        blocks = parse_show(text)
        return _timestamp(blocks[0].get("ActiveEnterTimestamp", "")) if blocks else 0.0

    def logs(self, name: str, lines: int = 50) -> list:
        code, text = self._run(f"journalctl -u {shlex.quote(name)} -n {int(lines)} "
                               f"--no-pager --output=short-iso-precise")
        if code != 0:
            return [text] if text else []
        return text.splitlines()

    # -- acting -------------------------------------------------------------
    #: How long `systemctl start` may take. Generous on purpose: a Type=oneshot
    #: unit blocks until its ExecStart script *finishes*, and a real one — SAP's
    #: web client, say — is a shell script that brings up a Java application
    #: server. Ninety seconds looked plenty until you meet one of those.
    CONTROL_TIMEOUT = 300

    def _control(self, verb: str, name: str) -> None:
        can = self.abilities()
        if not can.control:
            raise RuntimeError(can.why or "this target cannot be controlled")
        try:
            code, text = self._run(f"sudo -n systemctl {verb} {shlex.quote(name)}",
                                   timeout=self.CONTROL_TIMEOUT)
        except ConnectionError as exc:
            # A command that outlived its timeout is not the same as a machine
            # that went away, and saying "connection failed" about a service that
            # is still coming up sends someone to look at the network.
            raise RuntimeError(
                f"{verb} is still running after {self.CONTROL_TIMEOUT}s — the "
                f"unit may yet come up. Check it on the machine: "
                f"systemctl status {name}. ({exc})") from exc
        if code != 0:
            raise RuntimeError(text or f"systemctl {verb} exited {code}")

    def start(self, name: str) -> None:
        self._control("start", name)

    def stop(self, name: str) -> None:
        self._control("stop", name)

    def restart(self, name: str) -> None:
        self._control("restart", name)

    def kill(self, name: str) -> int:
        """The last resort, for a unit wedged in `deactivating`. Returns the pid
        that was signalled, so the history can say which process died."""
        pid = self.status(name).pid
        can = self.abilities()
        if not can.kill:
            raise RuntimeError(can.why or "this target cannot be controlled")
        code, text = self._run(
            f"sudo -n systemctl kill --signal=SIGKILL {shlex.quote(name)}",
            timeout=30)
        if code != 0:
            raise RuntimeError(text or f"systemctl kill exited {code}")
        return pid

    # -- what health checks need on the other side --------------------------
    def run(self, command: str, timeout: float = 10.0):
        return self._run(command, timeout=timeout)

    def stat(self, path: str):
        """(exists, seconds since written). `stat -c %Y` is seconds since the
        epoch, so the age is computed against the *target's* clock, not ours —
        two machines are never quite in step."""
        code, text = self._run(
            f"stat -c '%Y' {shlex.quote(path)} 2>/dev/null; date +%s", timeout=10)
        if code != 0:
            return False, 0.0
        parts = [p for p in text.split() if p.strip()]
        if len(parts) < 2:
            return False, 0.0
        try:
            written, now = float(parts[0]), float(parts[-1])
        except ValueError:
            return False, 0.0
        return True, max(0.0, now - written)
