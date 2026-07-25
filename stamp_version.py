"""Write the commit and build date into core/version.py before packaging.

Run from build.bat. A frozen app has no git repository to ask at runtime, so the
answer to "which build is this" has to be baked in. Restores the file afterwards
unless --keep is passed, so a build never leaves the working tree dirty.
"""

from __future__ import annotations

import datetime
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
    return commit, built


def main() -> int:
    if "--restore" in sys.argv:
        if BACKUP.exists():
            shutil.move(str(BACKUP), str(TARGET))
            print("version.py restored")
        return 0

    commit, built = stamp()
    text = TARGET.read_text(encoding="utf-8")
    shutil.copy2(str(TARGET), str(BACKUP))
    text = re.sub(r'^COMMIT = ".*"$', f'COMMIT = "{commit}"', text,
                  count=1, flags=re.M)
    text = re.sub(r'^BUILT = ".*"$', f'BUILT = "{built}"', text,
                  count=1, flags=re.M)
    TARGET.write_text(text, encoding="utf-8")

    version = re.search(r'^VERSION = "(.*)"$', text, flags=re.M)
    print(f"stamped {version.group(1) if version else '?'} "
          f"commit={commit} built={built}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
