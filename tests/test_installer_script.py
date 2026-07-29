"""Reading installer.iss for the mistakes Inno only reports while it is running.

The wizard's Pascal cannot be unit tested — there is no Inno to run it in, and the real
check is a person clicking through four installs. But two whole classes of failure are
visible in the text, and both of them cost a round trip through "build, hand it over, watch
it fail" before this file existed:

* **Expanding a constant before it exists.** `{app}` is not initialized until after the
  directory page, so touching it in `InitializeWizard` is *Runtime error (at 30:156):
  "an attempt was made to expand the app constant before it was initialized"*. It compiles
  perfectly.
* **A line that starts with `[`.** An array literal at the start of a line is read as a
  section tag, and the only thing said about it is "Invalid section tag".

Neither is a matter of taste, and neither is caught by ISCC.
"""

import pathlib
import re

import pytest

ISS = pathlib.Path(__file__).resolve().parent.parent / "installer.iss"

#: Constants Inno has not initialized while InitializeWizard runs. {app} is the one that
#: bit; the others are here because they fail for the same reason and the next person
#: should not have to find that out from a dialog box.
LATE_CONSTANTS = ("{app}", "{group}", "{uninstallexe}")

#: Every routine in [Code], as name -> body. Deliberately crude — the file is one script,
#: not a language this needs to parse — and good enough to follow who calls what.
ROUTINE = re.compile(
    r"^(?:function|procedure)\s+([A-Za-z_]\w*)[^;]*;\s*\n(.*?)^end;",
    re.S | re.M)


@pytest.fixture(scope="module")
def script() -> str:
    assert ISS.exists(), ISS
    return ISS.read_text(encoding="utf-8-sig")


def test_the_script_is_utf8_with_a_bom(script):
    """Inno reads a .iss as ANSI unless it starts with one, and this file has Turkish
    wizard messages in it — without the BOM they reach the screen as mojibake."""
    assert ISS.read_bytes().startswith(b"\xef\xbb\xbf")


def _routines(script: str) -> dict:
    bodies = {}
    for name, body in ROUTINE.findall(script):
        # A `forward;` declaration has no body; the real one comes later and wins.
        if body.strip():
            bodies.setdefault(name, "")
            bodies[name] += "\n" + body
    return bodies


def _reachable_from(start: str, routines: dict) -> set:
    """Which routines that one can end up in, however many hops away.

    The whole point: the bug was not `{app}` inside InitializeWizard, it was
    `PortAlreadyHere()` — one call away — and a test that only read the one body passed
    while the installer was broken.
    """
    seen, pending = set(), [start]
    while pending:
        name = pending.pop()
        if name in seen or name not in routines:
            continue
        seen.add(name)
        for other in routines:
            if other != name and re.search(rf"\b{re.escape(other)}\s*[(;]",
                                           routines[name]):
                pending.append(other)
    return seen


def test_initialize_wizard_expands_no_constant_that_does_not_exist_yet(script):
    routines = _routines(script)
    assert "InitializeWizard" in routines, "InitializeWizard is not in installer.iss"
    guilty = []
    for name in sorted(_reachable_from("InitializeWizard", routines)):
        for constant in LATE_CONSTANTS:
            if constant in routines[name]:
                guilty.append(f"{name} touches {constant}")
    assert guilty == [], (
        "reachable from InitializeWizard, which runs before Inno initializes these: "
        f"{guilty}. It compiles and then fails at run time with \"an attempt was made "
        "to expand the app constant before it was initialized\". Look it up when "
        "something needs it instead — see EnsureExistingPort.")


def test_no_line_in_the_code_starts_with_a_bracket(script):
    """A Pascal array literal at the start of a line is read as a section header."""
    offenders = []
    inside_code = False
    for number, line in enumerate(script.splitlines(), 1):
        stripped = line.strip()
        if stripped.lower() == "[code]":
            inside_code = True
            continue
        if inside_code and stripped.startswith("[") and stripped.endswith("]") \
                and " " not in stripped:
            inside_code = False        # a real section after [Code]
            continue
        if inside_code and stripped.startswith("["):
            offenders.append(f"{number}: {stripped[:60]}")
    assert offenders == [], (
        "these lines start with '[' inside [Code], which Inno reads as a section tag "
        f"and reports only as \"Invalid section tag\": {offenders}")


def test_setup_asks_nothing_about_language(script):
    """One language in [Languages], so Inno shows no language dialog before Setup starts.

    The *application's* language is its own setting, changed under Settings ▸ General. Asking
    twice about one thing is how the two end up disagreeing: a machine installed by an
    administrator in one language and used by somebody in another.
    """
    declared = [line for line in script.splitlines()
                if line.startswith("Name: ") and "MessagesFile" in line]

    assert len(declared) == 1, (
        "more than one language brings back Setup's language prompt: " + str(declared))
    assert "english" in declared[0]


def test_no_message_is_left_for_a_language_that_is_gone(script):
    """A `turkish.` line with no `turkish` language is dead weight, and reads as if Setup
    still speaks it."""
    orphans = sorted({line.split("=", 1)[0] for line in script.splitlines()
                      if line.startswith("turkish.")})

    assert orphans == [], f"messages for a language Setup no longer has: {orphans}"


def test_every_custom_message_is_used(script):
    """A message nobody shows is a sentence somebody wrote and a reader will never see.

    Kept because the [CustomMessages] block is where a wizard's words live, and it outlived
    two page rewrites today: a name left behind is invisible until somebody greps for it.
    """
    names = {line.split("=", 1)[0][len("english."):]
             for line in script.splitlines() if line.startswith("english.")}
    unused = sorted(n for n in names if f"cm:{n}" not in script)

    assert unused == [], f"nothing shows these: {unused}"


def test_no_custom_message_is_defined_twice(script):
    """The second definition wins silently, and the first is what somebody edited."""
    seen, twice = set(), []
    for line in script.splitlines():
        if line.startswith(("english.", "turkish.")) and "=" in line:
            name = line.split("=", 1)[0]
            if name in seen:
                twice.append(name)
            seen.add(name)
    assert twice == [], f"defined twice: {twice}"


def test_every_cm_reference_has_a_message(script):
    """`{cm:Something}` with no message shows the word "Something" to the user."""
    defined = {line.split("=", 1)[0].split(".", 1)[1]
               for line in script.splitlines()
               if line.startswith(("english.", "turkish.")) and "=" in line}
    used = set(re.findall(r"\{cm:([A-Za-z0-9_]+)", script))
    # Inno's own messages (LaunchProgram, AdditionalIcons, ...) come from its language
    # files, so only the ones that look like this script's are checked.
    ours = {name for name in used if name in defined or name[0].isupper()}
    missing = sorted(name for name in ours
                     if name not in defined
                     and name not in ("LaunchProgram", "AdditionalIcons"))
    assert missing == [], f"used but never defined: {missing}"


def test_the_version_matches_the_application(script):
    """stamp_version.py fails a release build when these disagree, which is late. The
    same check here fails in a second."""
    from core import version

    declared = re.search(r'#define MyAppVersion\s+"([^"]+)"', script)
    assert declared, "MyAppVersion is not defined"
    assert declared.group(1) == version.VERSION, (
        f"installer.iss says {declared.group(1)}, core/version.py says {version.VERSION}")


def test_the_data_folder_is_locked_down(script):
    """The hub reads services.json as LocalSystem and a `command` health check is a shell
    command line, so a data folder the built-in Users group can write is a way to run code
    as SYSTEM. By SID, because this installs on Turkish Windows where the group is called
    something else."""
    command = [line for line in script.splitlines() if "icacls" in line and "/grant" in line]
    assert command, "nothing sets the ACL on the data folder"
    granted = command[0]
    assert "/inheritance:r" in granted, "the inherited Users:(W) is not removed"
    for sid in ("*S-1-5-18", "*S-1-5-32-544", "*S-1-5-11"):
        assert sid in granted, f"{sid} is not granted anything"
    # Names only in the comments, never in the command: on Turkish Windows the built-in
    # groups have Turkish names and an English one silently matches nothing.
    for name in ("BUILTIN\\Users", "Authenticated Users", "Administrators"):
        assert name not in granted, f"{name} is named in the icacls command; use its SID"


def test_a_port_read_from_outside_is_validated_before_it_is_used(script):
    """The third round trip through "build, hand it over, watch it fail", and the reason
    this one is a test: `ServiceOfficerHub.exe port` was run and its first line used as the
    port. During an upgrade the exe still on disk is the *previous* release, and one that
    predates that command answers "Unknown command - 'port'" — which went straight into the
    address field as `CTL052:Unknown command - 'port'`. Everything after it followed
    honestly from a nonsense address, including asking for a token for this computer's own
    hub.

    An external command's output is input. Every path that produces a port has to go
    through the one function that will only ever return a port or nothing.
    """
    routines = _routines(script)
    assert "AsPort" in routines, "there is no validator any more"
    for name in ("PortAlreadyHere", "GetHubPort"):
        assert name in routines, f"{name} is gone"
        assert "AsPort" in routines[name], (
            f"{name} does not validate what it returns; a command that prints an error "
            "message would have that message used as a port")
    # The raw output must not be assigned anywhere without going through it.
    for name, listed in routines.items():
        if "LoadStringsFromFile" in listed:
            assert "AsPort" in listed or "Result := Trim(Lines[0]);" not in listed, \
                f"{name} uses a line of external output unvalidated"


def test_an_installed_hubs_port_is_shown_but_not_editable(script):
    """A port page appeared on a machine that had been running a hub for two days, because
    it was skipped only when the port could be *read* — and there neither source could
    answer (an exe predating the `port` command, a config written before there was a "hub"
    section in it). Two unknowns, taken for "nothing is installed here".

    Host and port are now one page, so the question is not whether to show it but whether
    the port may be changed. That is decided by the service registry — Windows' own record,
    the only one that cannot be out of date — and never by whether a port could be read.
    """
    routines = _routines(script)
    changed = routines.get("CurPageChanged", "")
    assert changed, "CurPageChanged is gone"
    assert "HubInstalledHere()" in changed, (
        "nothing consults the service registry when deciding whether the port may be "
        "changed")
    assert "ReadOnly := True" in changed, (
        "an installed hub's port is editable, and moving it leaves every client that has "
        "it stored looking at nothing")


def test_the_token_field_is_hidden_for_a_hub_on_this_computer(script):
    """It was shown with "not needed for a hub on this computer" written underneath, which
    is a field and an instruction to ignore it. Asked for and reported as confusing."""
    routines = _routines(script)
    assert "ShowTokenOnlyIfNeeded" in routines, "nothing decides whether to show it"
    body = routines["ShowTokenOnlyIfNeeded"]
    assert "LooksLikeThisComputer" in body
    assert "TokenEdit.Visible" in body and "TokenCaption.Visible" in body
    # And it has to be re-decided as the address is typed, not only once.
    assert "ShowTokenOnlyIfNeeded" in routines.get("AddressTyped", ""),         "typing a different address does not bring the token field back"


def test_the_firewall_rule_is_replaced_rather_than_appended(script):
    """`netsh advfirewall firewall add rule` appends. Two identically named rules were on
    the machine this was measured on — one per installation."""
    lines = [line for line in script.splitlines() if "advfirewall firewall" in line]
    adds = [line for line in lines if "add rule" in line]
    deletes = [line for line in lines if "delete rule" in line]
    assert len(adds) == 1, f"{len(adds)} add-rule lines"
    # One delete before the install's add, one in [UninstallRun].
    assert len(deletes) >= 2, (
        "nothing deletes the rule before adding it, so every installation leaves another "
        "copy behind")
    assert script.index(deletes[0]) < script.index(adds[0]), \
        "the delete has to come before the add"


def test_a_locked_port_looks_locked(script):
    """`ReadOnly` on a TNewEdit is invisible: it still takes focus, still lets its text be
    selected, and keeps a white background. So a field with "this cannot be changed here"
    written under it looked exactly like one that could — reported from a screenshot with
    the text highlighted in it.

    Whatever makes it unwritable has to also make it *look* unwritable.
    """
    routines = _routines(script)
    changed = routines.get("CurPageChanged", "")
    assert "ReadOnly := True" in changed
    assert "clBtnFace" in changed, (
        "the locked port field keeps a white background, so nothing on screen agrees "
        "with the note that says it cannot be changed")
    assert "clWindow" in changed, (
        "nothing puts the background back when the field is editable again — stepping "
        "back and forward would leave it grey and typable")
