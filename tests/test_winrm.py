"""The WinRM transport: what it says when it cannot work, and what it sends when it can.

There is no WinRM to talk to in a test, and mocking one would only test the mock. What is
worth checking is everything *around* the call — because every case below was a real failure
while this was being built, and each one cost a round trip through "run it against a real
machine and read the error".
"""

import pytest

from core import winrm_windows as winrm


@pytest.fixture(autouse=True)
def _cold():
    winrm.forget()
    yield
    winrm.forget()


# ---------------------------------------------------------------------------
# a refusal is an instruction
# ---------------------------------------------------------------------------
# Windows says these; a person has to be able to act on them. Every string on the left was
# collected from a real attempt on 2026-07-28.
def test_trustedhosts_becomes_the_command_that_fixes_it():
    said = winrm._explain("10.77.3.112", (
        "The WinRM client cannot process the request. Default authentication may be used "
        "with an IP address under the following conditions: the transport is HTTPS or the "
        "destination is in the TrustedHosts list..."))

    assert "TrustedHosts" in said
    assert '@{TrustedHosts="10.77.3.112"}' in said, \
        "it has to contain the command, with the machine already in it"


def test_no_forest_trust_points_at_the_credentials_field():
    """0x80090311 - no authenticating authority. Kerberos has nowhere to ask, which is what
    a missing forest trust looks like, and the answer is a user name and password."""
    said = winrm._explain("sc-sap-sql", "... errorcode 0x80090311 occurred ...")

    assert "no domain trust" in said.lower()
    assert "machines page" in said.lower()


def test_an_ip_address_and_windows_authentication_explains_itself():
    said = winrm._explain("10.77.3.112",
                          "Kerberos authentication cannot be used when the destination "
                          "is an IP address.")

    assert "name" in said.lower()


def test_a_machine_with_winrm_switched_off_gets_quickconfig():
    """Windows Server has it on; Windows 10 and 11 do not. Measured on both."""
    said = winrm._explain("10.77.3.110", (
        "WinRM cannot complete the operation. Verify that the specified computer name is "
        "valid, that the computer is accessible over the network..."))

    assert "winrm quickconfig" in said
    assert "that machine" in said.lower()


def test_a_refusal_with_nothing_in_it_still_says_something():
    said = winrm._explain("sc-sql", "")

    assert "sc-sql" in said and said.strip()


# ---------------------------------------------------------------------------
# PowerShell's own noise
# ---------------------------------------------------------------------------
def test_clixml_is_turned_back_into_a_sentence():
    """PowerShell run as a child process wraps stderr in CLIXML - the first probe reported
    an XML document where an error message belonged."""
    clixml = ('#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/'
              'powershell/2004/04"><S S="Error">Connecting to remote server failed_x000D_'
              '_x000A_</S><S S="Error">because it said no</S></Objs>')

    assert winrm._clean(clixml) == "Connecting to remote server failed because it said no"


def test_plain_text_on_stderr_is_left_alone():
    assert winrm._clean("  just words  ") == "just words"


def test_a_script_reports_its_failure_on_stdout():
    """Because stderr is CLIXML. The marker is how the two are told apart."""
    said, complaint = winrm._split_error(
        "first line\n" + winrm.ERROR_MARKER + "it went wrong\nsecond line")

    assert said == "first line\nsecond line"
    assert complaint == "it went wrong"


# ---------------------------------------------------------------------------
# TrustedHosts arithmetic
# ---------------------------------------------------------------------------
def test_what_counts_as_already_trusted():
    assert winrm.trusts("sc-sql", "sc-sql") is True
    assert winrm.trusts("SC-SQL", "sc-sql, other") is True, "case does not matter"
    assert winrm.trusts("sc-sql", "*") is True
    assert winrm.trusts("sc-sql.ct.corp", "*.ct.corp") is True
    assert winrm.trusts("sc-sql", "other") is False
    assert winrm.trusts("sc-sql", "") is False
    assert winrm.trusts("", "*") is False, "no machine is not every machine"


def test_setting_trustedhosts_needs_administrator_and_says_so(monkeypatch):
    """The tray application does not run elevated any more, so this is the ordinary case
    there - and it has to produce a command, not a failure."""
    monkeypatch.setattr(winrm, "client_state",
                        lambda: {"running": True, "trusted": "", "readable": True})
    monkeypatch.setattr(winrm, "_powershell",
                        lambda *a, **k: (4, "", "Access is denied"))

    ok, why = winrm.ensure_client_can_reach("10.77.3.112")

    assert ok is False
    assert "administrator" in why.lower()
    assert '@{TrustedHosts="10.77.3.112"}' in why


def test_a_machine_already_trusted_is_left_alone(monkeypatch):
    monkeypatch.setattr(winrm, "client_state",
                        lambda: {"running": True, "trusted": "a,10.77.3.112,b",
                                 "readable": True})

    def refuse(*a, **k):
        raise AssertionError("touched the configuration anyway")

    monkeypatch.setattr(winrm, "_powershell", refuse)

    ok, why = winrm.ensure_client_can_reach("10.77.3.112")

    assert ok is True and "already" in why


# ---------------------------------------------------------------------------
# what is actually sent
# ---------------------------------------------------------------------------
class _Done:
    def __init__(self, code=0, out="CTL052", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_the_password_never_reaches_a_command_line(monkeypatch):
    """A command line is visible in Task Manager for as long as the process lives. The
    password goes in the environment, which is not."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs.get("env") or {}
        return _Done()

    monkeypatch.setattr(winrm.subprocess, "run", fake_run)

    winrm.probe("sc-sql", "SC\\ismailorhan", "hunter2", fix_client=False)

    joined = " ".join(seen["args"])
    assert "hunter2" not in joined, "the password is on the command line"
    assert seen["env"].get("SO_WINRM_PW") == "hunter2"
    # And a file, not base64: base64 PowerShell is a malware indicator to most EDR
    # products, and a management tool that trips the customer's security team gets blocked.
    assert "-EncodedCommand" not in joined
    assert any(str(a).endswith(".ps1") for a in seen["args"])


def test_the_script_file_has_a_bom(monkeypatch):
    """PowerShell 5.1 reads a file without one in the local code page, and one non-ASCII
    character then breaks the string it sits in. Three failures in one afternoon."""
    written = {}
    real_fdopen = winrm.os.fdopen

    def capture(handle, mode="r", *args, **kwargs):
        handle_file = real_fdopen(handle, mode, *args, **kwargs)
        original = handle_file.write

        def note(data):
            written["data"] = data
            return original(data)

        handle_file.write = note
        return handle_file

    monkeypatch.setattr(winrm.os, "fdopen", capture)
    monkeypatch.setattr(winrm.subprocess, "run", lambda *a, **k: _Done(0, "x"))

    winrm._powershell("Write-Output 'hello'")

    assert written["data"].startswith(winrm.codecs.BOM_UTF8)


def test_a_probe_is_remembered_rather_than_repeated(monkeypatch):
    """Each attempt is a PowerShell process - 103 ms measured before it even connects - and
    abilities() is asked every time a row is drawn."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(1)
        return _Done(0, "SC-SQL")

    monkeypatch.setattr(winrm.subprocess, "run", fake_run)

    for _ in range(5):
        answer = winrm.probe("sc-sql", "", "", fix_client=False)

    assert answer["ok"] is True
    assert len(calls) == 1, "asked " + str(len(calls)) + " times"

    winrm.forget("sc-sql")
    winrm.probe("sc-sql", "", "", fix_client=False)
    assert len(calls) == 2, "forgetting did not make it ask again"


def test_a_kill_that_finds_no_process_counts_as_done(monkeypatch):
    """It is already gone, which is what was wanted. Reporting a failure would have somebody
    chasing a process that does not exist."""
    monkeypatch.setattr(
        winrm, "_powershell",
        lambda *a, **k: (3, winrm.ERROR_MARKER + "Cannot find a process with the process "
                            "identifier 4242.", ""))

    ok, why = winrm.kill("sc-sql", 4242)

    assert ok is True and why == ""


def test_killing_without_a_process_id_is_refused_before_anything_is_sent(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("sent a request with no pid")

    monkeypatch.setattr(winrm, "_powershell", refuse)

    ok, why = winrm.kill("sc-sql", 0)

    assert ok is False and "process id" in why


def test_a_command_reports_the_exit_code_the_target_gave(monkeypatch):
    """A Command health check passes on 0 and fails otherwise, so this number is the whole
    answer - and it comes from the far machine, not from PowerShell."""
    monkeypatch.setattr(winrm, "_powershell",
                        lambda *a, **k: (0, "CODE=3\nit did not like that", ""))

    code, out = winrm.run("sc-sql", "whatever")

    assert code == 3
    assert out == "it did not like that"


def test_a_command_that_could_not_be_sent_is_a_failure_with_a_reason(monkeypatch):
    monkeypatch.setattr(
        winrm, "_powershell",
        lambda *a, **k: (3, winrm.ERROR_MARKER + "WinRM cannot complete the operation.",
                         ""))

    code, out = winrm.run("sc-sql", "whatever")

    assert code == 1
    assert "winrm quickconfig" in out, "a failure nobody can act on is worse than none"
