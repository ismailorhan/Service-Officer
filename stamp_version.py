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


def git(*args) -> str:
    try:
        out = subprocess.run(("git",) + args, capture_output=True, text=True,
                             timeout=15, cwd=TARGET.parent.parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def stamp() -> tuple:
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    # A build from an edited tree is not the commit it claims to be, and that is
    # exactly the confusion this stamp exists to prevent.
    if git("status", "--porcelain"):
        commit += "-dirty"
    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Commits since the last release tag: 0 means this is the release, anything
    # else is an internal build and becomes the fourth part of the version.
    # Counted rather than kept in a file, so it can't drift and needs no state.
    build = 0
    tag = git("describe", "--tags", "--abbrev=0")
    if tag:
        counted = git("rev-list", "--count", f"{tag}..HEAD")
        build = int(counted) if counted.isdigit() else 0
    return commit, built, build


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
        match = re.search(r'^#define MyAppVersion\s+"(.*)"$',
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

    commit, built, build = stamp()
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
    print(f"stamped {shown} commit={commit} built={built}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
