"""Write the `latest.json` that clients read to learn a release exists.

    python tools/make_feed.py                       from dist/, after ISCC
    python tools/make_feed.py --notes "What changed"
    python tools/make_feed.py --minimum 2.3.0       step over anything below this

The hash is computed here and never typed. A hash written by hand is a hash written wrong, and
the one thing it is for is deciding whether to run an installer with administrator rights —
`core/updates.py` refuses a download that does not match, so a wrong hash in the feed does not
ship a bad installer, it ships an update nobody can install.

The version comes from `installer-version.txt`, which `stamp_version.py` wrote during the
build, so the feed cannot disagree with the file it describes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "dist" / "ServiceOfficerSetup.exe"
STAMPED = ROOT / "installer-version.txt"
FEED = ROOT / "dist" / "latest.json"
#: Where a release's assets live. The tag is `v` + the version, which is what
#: .github/workflows/release.yml triggers on.
URL = ("https://github.com/ismailorhan/Service-Officer/releases/download/"
       "v{version}/ServiceOfficerSetup.exe")


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(version: str, setup: pathlib.Path, notes: str = "",
          minimum: str = "") -> dict:
    return {"version": version,
            "url": URL.format(version=version),
            "sha256": sha256_of(setup),
            "notes": notes,
            "minimum": minimum}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", default="", help="what changed, shown in the panel")
    parser.add_argument("--minimum", default="",
                        help="clients must not install anything below this")
    parser.add_argument("--setup", default=str(SETUP))
    parser.add_argument("--out", default=str(FEED))
    args = parser.parse_args(argv)

    setup = pathlib.Path(args.setup)
    if not setup.is_file():
        print(f"[ERROR] {setup} is not there. Build the installer first (ISCC installer.iss).")
        return 1
    if not STAMPED.is_file():
        print(f"[ERROR] {STAMPED} is not there. Run the build, which stamps it.")
        return 1
    version = STAMPED.read_text(encoding="utf-8").strip()
    if not version:
        print(f"[ERROR] {STAMPED} is empty.")
        return 1
    # A fourth part means an internal build, not a release — see core/version.py. Publishing
    # one as a release would offer every client an installer whose tag does not exist, so this
    # says so loudly and carries on: the file is still useful for a dry run.
    if len(version.split(".")) > 3:
        print(f"[WARN] {version} is an internal build, not a release. The url in this feed "
              f"points at a tag (v{version}) that will not exist.")

    feed = build(version, setup, args.notes, args.minimum)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"   version {feed['version']}")
    print(f"   sha256  {feed['sha256']}")
    print(f"   url     {feed['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
