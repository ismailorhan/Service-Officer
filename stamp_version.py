"""Write the commit and build date into core/version.py before packaging.

Run from build.bat. A frozen app has no git repository to ask at runtime, so the
answer to "which build is this" has to be baked in. Restores the file afterwards
unless --keep is passed, so a build never leaves the working tree dirty.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys

TARGET = pathlib.Path(__file__).with_name("core") / "version.py"
BACKUP = TARGET.with_suffix(".py.orig")
#: The build counter. Not committed: it counts builds *on this machine*, which is
#: what "which build is this" means while iterating. The commit in About is what
#: identifies the code absolutely.
COUNTER = pathlib.Path(__file__).with_name(".build-number")
#: The version the *installer* stamps on itself, written here because the .iss cannot work it
#: out: the build number is counted at build time and `version.py` is restored before ISCC
#: runs. Without it the installer said "2.2.7 will be upgraded to 2.2.7" — true about the
#: release and useless about the two things that actually differ.
FOR_INSTALLER = pathlib.Path(__file__).with_name("installer-version.txt")


def git(*args) -> str:
    try:
        out = subprocess.run(("git",) + args, capture_output=True, text=True,
                             timeout=15, cwd=TARGET.parent.parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def next_build(version: str, release: bool) -> int:
    """The build number for this build.

    Counts builds, not commits: several builds can come off one commit while
    something is being tried, and "which build is this" has to tell them apart.
    Restarts at 1 when the release version changes, so 2.1.0's builds don't carry
    on from 2.0.0's. A tagged release build is 0 — the release has no fourth part.
    """
    if release:
        return 0
    previous_version, count = "", 0
    if COUNTER.exists():
        try:
            previous_version, _, text = COUNTER.read_text(
                encoding="utf-8").strip().partition(" ")
            count = int(text or 0)
        except (OSError, ValueError):
            previous_version, count = "", 0
    count = count + 1 if previous_version == version else 1
    try:
        COUNTER.write_text(f"{version} {count}\n", encoding="utf-8")
    except OSError:
        pass                       # a read-only checkout still builds
    return count


def stamp(version: str, release: bool) -> tuple:
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    # A build from an edited tree is not the commit it claims to be, and that is
    # exactly the confusion this stamp exists to prevent.
    if git("status", "--porcelain"):
        commit += "-dirty"
    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return commit, built, next_build(version, release)


def declared_versions() -> dict:
    """The version as each place spells it. Three copies exist — core/version.py,
    installer.iss, and the git tag — and a release that disagrees with itself is
    worse than no version at all."""
    found = {}
    text = TARGET.read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "(.*)"$', text, flags=re.M)
    if match:
        found["core/version.py"] = match.group(1)

    iss = TARGET.parent.parent / "installer.iss"
    if iss.exists():
        # `MyRelease`, not `MyAppVersion`. MyAppVersion became *computed* — read from the file
        # stamp_version leaves, so the installer can show a build number — and this regex went
        # on looking for a literal that no longer exists. It matched nothing from that day on,
        # so the "three copies" this docstring promises to compare were two, silently. A test
        # catches the same disagreement, but the release-time guard had quietly stopped being
        # one.
        match = re.search(r'^#define MyRelease\s+"(.*)"$',
                          iss.read_text(encoding="utf-8"), flags=re.M)
        if match:
            found["installer.iss"] = match.group(1)

    # On a tag build, GitHub tells us the tag; locally there may be none.
    ref = os.environ.get("GITHUB_REF_NAME", "")
    if re.fullmatch(r"v\d+\.\d+\.\d+", ref):
        found["git tag"] = ref[1:]
    return found


def main() -> int:
    if "--restore" in sys.argv:
        if BACKUP.exists():
            shutil.move(str(BACKUP), str(TARGET))
            print("version.py restored")
        return 0

    declared = declared_versions()
    if len(set(declared.values())) > 1:
        print("[ERROR] the version is spelled differently in each place:")
        for where, value in declared.items():
            print(f"          {where}: {value}")
        return 1

    version = declared.get("core/version.py", "0.0.0")
    # A build from a version tag is the release itself; anything else is an
    # internal build and gets a build number.
    release = "git tag" in declared
    commit, built, build = stamp(version, release)
    text = TARGET.read_text(encoding="utf-8")
    shutil.copy2(str(TARGET), str(BACKUP))
    text = re.sub(r'^COMMIT = ".*"$', f'COMMIT = "{commit}"', text,
                  count=1, flags=re.M)
    text = re.sub(r'^BUILT = ".*"$', f'BUILT = "{built}"', text,
                  count=1, flags=re.M)
    text = re.sub(r'^BUILD = \d+$', f'BUILD = {build}', text, count=1, flags=re.M)
    TARGET.write_text(text, encoding="utf-8")

    version = re.search(r'^VERSION = "(.*)"$', text, flags=re.M)
    shown = version.group(1) if version else "?"
    if build:
        shown += f".{build}"
    # Left in place by --restore on purpose: ISCC runs after the build, by which time
    # version.py is back to what it was in git.
    FOR_INSTALLER.write_text(shown + "\n", encoding="utf-8")
    print(f"stamped {shown} commit={commit} built={built}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
