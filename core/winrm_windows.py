"""The three things SMB cannot do on another Windows machine: run a command, kill a
process, read an event log.

Why a second transport exists at all, measured on 2026-07-26 and again on 2026-07-28:

  `sc`, `schtasks` and the admin shares ride the IPC$ session core/win_session.py
  establishes, so they cross a forest boundary. `taskkill`, `tasklist` and WMI authenticate
  themselves, and across a boundary their own attempt fails. WinRM authenticates itself
  too — but it accepts an explicit user name and password, which is the one thing the
  others do not.

What it costs, said plainly because the alternative was "nothing installed on the target":

| Where | What is needed |
|---|---|
| a Windows **Server** target | nothing. 5985 is open by default; measured OK on sc-sap-sql |
| a Windows **client** target | `winrm quickconfig` on that machine |
| **here** (the hub) | the WinRM service running, and the target in TrustedHosts |

That last row is because Kerberos needs a forest trust we do not have (`0x80090311`, no
authenticating authority) so it falls back to NTLM, and NTLM over HTTP requires the target
to be trusted by name. `ensure_client_can_reach` does it, one machine at a time, never `*`
— and it needs administrator rights, which the hub has as LocalSystem.

**PowerShell, not a library.** pywinrm would be a dependency to license-check, ship inside
a PyInstaller bundle and patch on somebody's server, for something `powershell.exe` already
does. The cost is a process per call, ~200-400 ms, against a health check interval measured
in tens of seconds.

**A script file with a BOM, not `-EncodedCommand` and not `-Command`.** The base64 form
is immune to quoting and code pages, which is why it was the first choice — three failures
in one afternoon came from a `.ps1` read as ANSI, a here-string, and an em-dash. But base64
PowerShell is a malware indicator to most EDR products, and a management tool that trips the
customer's security team is a management tool the customer blocks. A file written as UTF-8
*with a BOM* is read correctly by PowerShell 5.1 and looks like what it is.

The password is in neither: it arrives in the environment, which Task Manager does not show
and which is gone when the process ends.
"""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import tempfile
import time

from . import applog

log = applog.get("winrm")

#: How long a call may take. A command health check has its own timeout on top; this is the
#: backstop for a machine that accepts a connection and then says nothing.
TIMEOUT = 45.0
#: Long enough to set up a session and run something small. Measured: an Invoke-Command
#: round trip to sc-sap-sql is well under a second once the session is up.
CONNECT_TIMEOUT = 20.0
#: What Windows says when Kerberos has no authority to ask — i.e. no forest trust. Named
#: because the message that comes with it is a paragraph and the code is the fact.
NO_AUTHORITY = "0x80090311"

_probe_cache: dict = {}


#: How an embedded script reports a failure. On *stdout*, deliberately: PowerShell run as a
#: child process wraps stderr in CLIXML — "Preparing modules for first use" and all — and
#: the message inside that is not worth parsing. Measured the hard way on 2026-07-28.
ERROR_MARKER = "SO-ERR "


def _clean(text: str) -> str:
    """Whatever is usable out of a PowerShell stderr stream.

    It arrives as CLIXML when PowerShell is a child process. This pulls the human sentences
    out of it rather than showing somebody an XML document, and leaves plain text alone.
    """
    text = (text or "").strip()
    if not text.startswith("#< CLIXML"):
        return text
    said = re.findall(r"<S S=\"(?:Error|Warning)\">(.*?)</S>", text)
    if not said:
        return ""
    joined = " ".join(said)
    joined = joined.replace("_x000D__x000A_", " ").replace("_x000A_", " ")
    return re.sub(r"\s+", " ", joined).strip()


def _powershell(script: str, environment: dict = None,
                timeout: float = TIMEOUT) -> tuple:
    """Run a script and return (exit code, stdout, stderr).

    The script is written to a temporary file as UTF-8 *with a BOM* — see the module
    docstring for why that and not `-EncodedCommand`.
    """
    handle, path = tempfile.mkstemp(suffix=".ps1", prefix="service-officer-")
    try:
        with os.fdopen(handle, "wb") as fh:
            # The BOM is the whole point: without it PowerShell 5.1 reads the file in the
            # local code page, and one non-ASCII character breaks the string it sits in.
            fh.write(codecs.BOM_UTF8 + script.encode("utf-8"))
        # Bypass because this file is generated and unsigned, and a machine set to
        # AllSigned would otherwise refuse it. It is a file we just wrote, in our own
        # temporary directory, run by us.
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", path],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(environment or {})},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (done.returncode, (done.stdout or "").strip(),
                _clean(done.stderr))
    except subprocess.TimeoutExpired:
        return 124, "", f"WinRM did not answer within {timeout:.0f}s"
    except OSError as exc:
        return 125, "", f"could not run PowerShell: {exc}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _credential_prologue(user: str) -> str:
    """The PowerShell that turns SO_WINRM_PW into a credential, or nothing.

    With no user name the session goes as whoever the process is, which is right for a
    machine in the same domain as the hub.
    """
    if not user:
        return "$cred = $null\n"
    return (
        "$secure = ConvertTo-SecureString $env:SO_WINRM_PW -AsPlainText -Force\n"
        "$cred = New-Object System.Management.Automation.PSCredential("
        "$env:SO_WINRM_USER, $secure)\n")


def _invoke(host: str, user: str, password: str, body: str,
            timeout: float = TIMEOUT) -> tuple:
    """`body` on the target, as a PowerShell script block. Returns (ok, output, why)."""
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        + _credential_prologue(user) +
        "$a = @{ ComputerName = $env:SO_WINRM_HOST; ScriptBlock = {" + body + "} }\n"
        "if ($cred) { $a['Credential'] = $cred }\n"
        "try { Invoke-Command @a | Out-String -Width 4096 }\n"
        "catch { Write-Output ('SO-ERR ' + $_.Exception.Message); exit 3 }\n")
    environment = {"SO_WINRM_HOST": host, "SO_WINRM_USER": user or "",
                   "SO_WINRM_PW": password or ""}
    code, out, err = _powershell(script, environment, timeout)
    said, complaint = _split_error(out)
    if code == 0 and not complaint:
        return True, said, ""
    return False, said, _explain(host, complaint or err or said)


# ---------------------------------------------------------------------------
# what went wrong, and what to do about it
# ---------------------------------------------------------------------------
def _split_error(out: str) -> tuple:
    """(what the script printed, what it complained about) — see ERROR_MARKER."""
    kept, complaints = [], []
    for line in (out or "").splitlines():
        if line.startswith(ERROR_MARKER):
            complaints.append(line[len(ERROR_MARKER):].strip())
        else:
            kept.append(line)
    return "\n".join(kept).strip(), " ".join(complaints).strip()


def _explain(host: str, raw: str) -> str:
    """A refusal turned into an instruction.

    Every one of these was met while probing this on 2026-07-28, and each has exactly one
    fix. A person reading it should not have to know what WinRM is to act on it.
    """
    text = re.sub(r"\s+", " ", raw or "").strip()
    lowered = text.lower()
    if "trustedhosts" in lowered:
        return (f"This computer will not send credentials to {host} over WinRM until it "
                f"trusts it. As an administrator here:  winrm set winrm/config/client "
                f'@{{TrustedHosts="{host}"}}')
    if NO_AUTHORITY in text or "no authenticating authority" in lowered:
        return (f"There is no domain trust between this computer and {host}, so Kerberos "
                "has nothing to ask. Give that machine a user name and password on the "
                "Machines page — WinRM will then use those.")
    if "cannot be used when the destination is an ip address" in lowered:
        return (f"WinRM will not use Windows authentication to an IP address. Use {host}'s "
                "name on the Machines page, or give it a user name and password.")
    if "winrm cannot complete the operation" in lowered or "cannot connect" in lowered:
        return (f"{host} is not answering on WinRM. On that machine, as an administrator:  "
                "winrm quickconfig   (Windows Server has it on already; Windows 10 and 11 "
                "do not.)")
    if "access is denied" in lowered:
        return (f"{host} refused the account. WinRM needs an account in its "
                "Administrators group, or one granted access to its session "
                "configuration.")
    if "did not answer within" in lowered:
        return text
    return text[:400] or f"{host} refused a WinRM request and said nothing about why"


# ---------------------------------------------------------------------------
# the client side of this computer
# ---------------------------------------------------------------------------
def client_state() -> dict:
    """What this computer's WinRM client is able to do right now.

    Read rather than assumed: the service is stopped on a fresh Windows 11 install, and
    TrustedHosts cannot even be read while it is.
    """
    code, out, _err = _powershell(
        "$s = Get-Service WinRM -ErrorAction SilentlyContinue\n"
        "$running = if ($s -and $s.Status -eq 'Running') { 'yes' } else { 'no' }\n"
        "$trusted = ''\n"
        "try { $trusted = (Get-Item WSMan:\\localhost\\Client\\TrustedHosts"
        " -ErrorAction Stop).Value } catch { $trusted = '?' }\n"
        "'running=' + $running + '|trusted=' + $trusted\n", timeout=30)
    running, trusted = False, ""
    for part in (out or "").split("|"):
        if part.startswith("running="):
            running = part[len("running="):].strip() == "yes"
        elif part.startswith("trusted="):
            trusted = part[len("trusted="):].strip()
    return {"running": running,
            "trusted": "" if trusted == "?" else trusted,
            "readable": trusted != "?"}


def trusts(host: str, trusted: str) -> bool:
    """Is `host` covered by that TrustedHosts value? `*` covers everything, and the list is
    comma separated with the odd space in it."""
    if not host or not trusted:
        return False
    wanted = host.strip().lower()
    for entry in trusted.split(","):
        entry = entry.strip().lower()
        if entry in ("*", wanted):
            return True
        if entry.startswith("*.") and wanted.endswith(entry[1:]):
            return True
    return False


def ensure_client_can_reach(host: str) -> tuple:
    """Make this computer able to send credentials to `host`. Returns (ok, what happened).

    Adds one name to TrustedHosts, keeping whatever is there — never `*`, which would mean
    "send credentials to anything that answers". Starts the WinRM service if it is stopped,
    because TrustedHosts cannot be read or written without it.

    Needs administrator rights. The hub has them as LocalSystem; an unelevated tray
    application does not, and gets told what to run instead.
    """
    if not host:
        return False, "no machine to trust"
    state = client_state()
    if state["readable"] and trusts(host, state["trusted"]):
        return True, "already trusted"

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $s = Get-Service WinRM\n"
        "  if ($s.StartType -eq 'Disabled') { Set-Service WinRM -StartupType Manual }\n"
        "  if ($s.Status -ne 'Running') { Start-Service WinRM }\n"
        "  $path = 'WSMan:\\localhost\\Client\\TrustedHosts'\n"
        "  $current = (Get-Item $path).Value\n"
        "  $target = $env:SO_WINRM_HOST\n"
        "  if ([string]::IsNullOrWhiteSpace($current)) { $new = $target }\n"
        "  elseif ($current -eq '*') { $new = $current }\n"
        "  else {\n"
        "    $parts = @($current -split ',' | ForEach-Object { $_.Trim() } |"
        " Where-Object { $_ })\n"
        "    if ($parts -contains $target) { $new = $current }\n"
        "    else { $new = (($parts + $target) -join ',') }\n"
        "  }\n"
        "  Set-Item $path -Value $new -Force\n"
        "  'ok ' + $new\n"
        "} catch { Write-Output ('SO-ERR ' + $_.Exception.Message); exit 4 }\n")
    code, out, err = _powershell(script, {"SO_WINRM_HOST": host}, timeout=60)
    if code == 0 and out.startswith("ok"):
        log.info("WinRM: this computer now trusts %s", host)
        return True, out[3:].strip()
    reason = re.sub(r"\s+", " ", err or out or "").strip()
    if "access is denied" in reason.lower() or code == 4:
        return False, (
            "This needs administrator rights on this computer. As an administrator:  "
            f'winrm set winrm/config/client @{{TrustedHosts="{host}"}}')
    return False, reason[:300] or "could not set TrustedHosts"


# ---------------------------------------------------------------------------
# the three operations
# ---------------------------------------------------------------------------
def probe(host: str, user: str = "", password: str = "",
          fix_client: bool = True) -> dict:
    """Can we run something on `host` right now? Cached per (host, user).

    `fix_client` adds the machine to TrustedHosts if that is what is missing — the one
    thing that can be fixed from here without touching the target.
    """
    key = (host.lower(), user.lower())
    cached = _probe_cache.get(key)
    if cached is not None:
        return cached

    began = time.monotonic()
    ok, out, why = _invoke(host, user, password, " $env:COMPUTERNAME ",
                           timeout=CONNECT_TIMEOUT + 15)
    if not ok and fix_client and "trustedhosts" in (why or "").lower():
        fixed, note = ensure_client_can_reach(host)
        log.info("WinRM: trusting %s: %s", host, note)
        if fixed:
            ok, out, why = _invoke(host, user, password, " $env:COMPUTERNAME ",
                                   timeout=CONNECT_TIMEOUT + 15)
        else:
            why = note
    answer = {"ok": ok, "name": out.strip().splitlines()[0] if ok and out else "",
              "why": "" if ok else why,
              "seconds": round(time.monotonic() - began, 2)}
    _probe_cache[key] = answer
    log.info("WinRM %s: %s (%.2fs)", host, "yes" if ok else f"no - {why}",
             answer["seconds"])
    return answer


def forget(host: str = "") -> None:
    """Drop what was learned, so the next question asks again. For tests, and for the
    moment a machine's credentials are edited."""
    if not host:
        _probe_cache.clear()
        return
    for key in [k for k in _probe_cache if k[0] == host.lower()]:
        _probe_cache.pop(key, None)


def run(host: str, command: str, user: str = "", password: str = "",
        timeout: float = TIMEOUT) -> tuple:
    """A command line on the target. Returns (exit code, output).

    Shaped like `control.run_command` so a Command health check does not care which
    transport answered it. `cmd /c` because the thing being run is a command line as a
    person would type it, not PowerShell.
    """
    body = (" $ErrorActionPreference='Continue';"
            " $out = & cmd.exe /c $using:command 2>&1 | Out-String;"
            " [PSCustomObject]@{ Code = $LASTEXITCODE; Out = $out } ")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        + _credential_prologue(user) +
        "$command = $env:SO_WINRM_CMD\n"
        "$a = @{ ComputerName = $env:SO_WINRM_HOST; ScriptBlock = {" + body + "} }\n"
        "if ($cred) { $a['Credential'] = $cred }\n"
        "try {\n"
        "  $r = Invoke-Command @a\n"
        "  'CODE=' + $r.Code\n"
        "  $r.Out\n"
        "} catch { Write-Output ('SO-ERR ' + $_.Exception.Message); exit 3 }\n")
    environment = {"SO_WINRM_HOST": host, "SO_WINRM_USER": user or "",
                   "SO_WINRM_PW": password or "", "SO_WINRM_CMD": command}
    code, out, err = _powershell(script, environment, timeout)
    if code != 0:
        return 1, _explain(host, err or out)
    lines = out.splitlines()
    exit_code = 0
    if lines and lines[0].startswith("CODE="):
        try:
            exit_code = int(lines[0][len("CODE="):].strip() or 0)
        except ValueError:
            exit_code = 0
        lines = lines[1:]
    return exit_code, "\n".join(lines).strip()


def kill(host: str, pid: int, user: str = "", password: str = "") -> tuple:
    """Terminate a process by its id. Returns (ok, why).

    By id and not by name: the service manager already told us which process this service
    is, and killing by name on a machine with two of them is a different, worse thing.
    """
    if not pid:
        return False, "no process id to kill"
    body = (" Stop-Process -Id $using:processId -Force -ErrorAction Stop; 'killed' ")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + _credential_prologue(user) +
        "$processId = [int]$env:SO_WINRM_PID\n"
        "$a = @{ ComputerName = $env:SO_WINRM_HOST; ScriptBlock = {" + body + "} }\n"
        "if ($cred) { $a['Credential'] = $cred }\n"
        "try { Invoke-Command @a }\n"
        "catch { Write-Output ('SO-ERR ' + $_.Exception.Message); exit 3 }\n")
    environment = {"SO_WINRM_HOST": host, "SO_WINRM_USER": user or "",
                   "SO_WINRM_PW": password or "", "SO_WINRM_PID": str(int(pid))}
    code, out, err = _powershell(script, environment, TIMEOUT)
    if code == 0 and "killed" in out:
        return True, ""
    reason = _explain(host, err or out)
    if "cannot find a process" in (err or out).lower():
        # It is already gone, which is what was wanted.
        return True, ""
    return False, reason


def logs(host: str, service: str, lines: int = 50, user: str = "",
         password: str = "") -> list:
    """That service's recent entries from the target's own event log.

    The System log filtered by provider name, which is where the service control manager
    and most services put theirs. Returns the lines, or one line saying why not — the
    caller shows this in a panel, and an empty list there is indistinguishable from "no
    events".
    """
    body = (
        " $ErrorActionPreference='Continue';"
        " $names = @($using:service, 'Service Control Manager');"
        " Get-WinEvent -LogName System -MaxEvents 400 -ErrorAction SilentlyContinue |"
        " Where-Object { $names -contains $_.ProviderName -or"
        " $_.Message -like ('*' + $using:service + '*') } |"
        " Select-Object -First $using:lines |"
        " ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + '  ' +"
        " $_.LevelDisplayName + '  ' + ($_.Message -split \"`n\")[0] } ")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + _credential_prologue(user) +
        "$service = $env:SO_WINRM_SERVICE\n"
        "$lines = [int]$env:SO_WINRM_LINES\n"
        "$a = @{ ComputerName = $env:SO_WINRM_HOST; ScriptBlock = {" + body + "} }\n"
        "if ($cred) { $a['Credential'] = $cred }\n"
        "try { Invoke-Command @a }\n"
        "catch { Write-Output ('SO-ERR ' + $_.Exception.Message); exit 3 }\n")
    environment = {"SO_WINRM_HOST": host, "SO_WINRM_USER": user or "",
                   "SO_WINRM_PW": password or "", "SO_WINRM_SERVICE": service,
                   "SO_WINRM_LINES": str(int(lines))}
    code, out, err = _powershell(script, environment, TIMEOUT)
    if code != 0:
        return [_explain(host, err or out)]
    return [line for line in out.splitlines() if line.strip()]
