"""What build this is.

Two questions get asked about a tool running on somebody else's server: which
version, and is it the one I think I installed. A release number alone answers the
first; it does not answer the second, because two builds of "2.0.0" from different
commits look identical. So the build stamp carries the commit and the build date
too, written in by the build script.

Nothing here is computed at runtime: a frozen app has no git repository to ask.
"""

from __future__ import annotations

import os
import sys

#: bumped by hand for a release, and matched by installer.iss
VERSION = "2.0.0"

#: Commits since that release tag, stamped by build.bat. Zero means this *is* the
#: release; anything else is an internal build, and shows as 2.0.0.7 — three parts
#: for what customers get, a fourth for what we build in between.
BUILD = 0
#: filled in by build.bat — short commit, and "dirty" if the tree had edits
COMMIT = "dev"
#: filled in by build.bat, ISO date of the build
BUILT = ""


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def short() -> str:
    """"2.0.0" for the release itself, "2.0.0.7" for an internal build."""
    return VERSION if not BUILD else f"{VERSION}.{BUILD}"


def full() -> str:
    """Everything, for an about line and for a support request. The commit lives
    here rather than in the version, because it answers a different question:
    which code, not which build number."""
    parts = [f"Version {short()}"]
    if COMMIT not in ("", "dev"):
        parts.append(f"commit {COMMIT}")
    if BUILT:
        parts.append(BUILT)
    if not is_frozen():
        parts.append("running from source")
    return "  ·  ".join(parts)


def install_dir() -> str:
    """Where this build lives, which is the other half of "which one is running"
    when someone has two copies on a machine."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
