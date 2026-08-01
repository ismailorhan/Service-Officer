"""Is there a newer release, and may it be installed right now.

Three separate questions, kept separate on purpose:

* **Is there one.** A `latest.json` published beside each GitHub release. Read once a day, no
  more — this is a server tool, not a browser.
* **Is it the one that was published.** Downloaded to a temp file and hashed. We are about to
  run an installer with administrator rights; the hash is not optional, and a feed that says
  one thing while the file says another is a refusal, not a warning.
* **Is now a good moment.** No. Not while a stack is running, an action is in flight, a
  scheduled trigger is mid-run or a recovery is waiting to retry. An update that restarts the
  watchdog in the middle of a recovery is worse than being a release behind.

Nothing here installs anything on its own. The default is to say so and wait to be asked; see
DECISIONS.md, "Auto-update".

The installer is launched **detached**. It stops `ServiceOfficerHub` — which is the process
that started it — so a child sharing this process's lifetime would be killed by the very step
it was asked to perform.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request

from . import applog, version

log = applog.get("updates")

#: Where releases are published. A file beside the release rather than the GitHub API: the API
#: is rate-limited and needs a token for anything private, and a static file behind HTTPS is
#: the whole requirement.
FEED_URL = ("https://github.com/ismailorhan/Service-Officer/releases/latest/download/"
            "latest.json")
#: Once a day. A server tool that phones home every ten minutes is a server tool nobody keeps.
EVERY_SECONDS = 24 * 60 * 60
#: A feed is a few hundred bytes. Anything slower than this is a network problem, and this runs
#: on a thread that must not hold a shutdown up.
TIMEOUT = 20
#: Refuse a download that could not possibly be our installer. Measured: the real one is 37 MB.
MOST_BYTES = 300 * 1024 * 1024


class Refused(RuntimeError):
    """The download is not what the feed said it would be."""


class BadMoment(RuntimeError):
    """Something is in flight. Carries what, because "not now" without a reason reads as a
    bug in the button."""


class Release:
    """What the feed says about the newest release."""

    def __init__(self, raw: dict):
        self.version = str(raw.get("version") or "")
        self.url = str(raw.get("url") or "")
        self.sha256 = str(raw.get("sha256") or "").lower().strip()
        self.notes = str(raw.get("notes") or "")
        #: A release below this one must not be updated *to* — the way a broken version gets
        #: skipped rather than recalled. Empty means no floor.
        self.minimum = str(raw.get("minimum") or "")

    def usable(self) -> bool:
        """Whether this is a release we could install at all.

        A feed missing the hash is not installable, deliberately: the alternative is running an
        unverified installer as administrator, and "the publisher forgot a field" is not a good
        enough reason to do that.
        """
        return bool(self.version and self.url and len(self.sha256) == 64)

    def newer_than(self, mine: str = None) -> bool:
        return newer(self.version, mine)

    def skipped(self) -> bool:
        """Whether the feed itself says this release should be stepped over."""
        return bool(self.minimum) and newer(self.minimum, self.version)


def _parts(text: str) -> tuple:
    """The comparable part of a version, as integers. Unreadable pieces count as zero."""
    out = []
    for piece in str(text or "").split(".")[:version.RELEASE_PARTS]:
        digits = "".join(c for c in piece if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < version.RELEASE_PARTS:
        out.append(0)
    return tuple(out)


def newer(theirs: str, mine: str = None) -> bool:
    """Whether `theirs` is a later release than `mine`.

    The first three parts only, and numerically: "2.2.10" is newer than "2.2.9", which a string
    comparison gets exactly backwards. The build counter is not part of it — see
    version.RELEASE_PARTS.
    """
    return _parts(theirs) > _parts(mine or version.short())


def fetch(url: str = FEED_URL, timeout: float = TIMEOUT) -> Release:
    """Read the feed. Raises whatever the network raised — the caller decides if that matters."""
    request = urllib.request.Request(url, headers={"User-Agent": "ServiceOfficer"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return Release(json.loads(answer.read(64 * 1024).decode("utf-8")))


def keep_dir() -> str:
    """Where a verified installer is kept, rather than temp.

    Temp is swept, and this file has a second job after the hub has used it: the clients need
    it. A client is on the old release and the hub is on the new one — that is the *normal*
    order, hub first — so the file the hub installed from is exactly what each client needs
    next, and the hub is the one machine on the network that fetched it.
    """
    from . import config as cfg_mod
    return os.path.join(cfg_mod.APP_DIR, "updates")


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kept(release_version: str = None) -> str:
    """The verified installer for that version if it is still here, else "".

    Named by version rather than remembered in a file: after an update the process that
    downloaded it is gone — replaced by the one it installed — and a new hub finding its own
    installer by name needs no state carried across the restart.
    """
    wanted = release_version or version.short()
    parts = _parts(wanted)
    try:
        names = os.listdir(keep_dir())
    except OSError:
        return ""
    for name in names:
        if not name.lower().endswith(".exe"):
            continue
        stem = name.rsplit("-", 1)[-1][:-4] if "-" in name else ""
        if stem and _parts(stem) == parts:
            return os.path.join(keep_dir(), name)
    return ""


def download(release: Release, into: str = None, timeout: float = TIMEOUT) -> str:
    """Fetch the installer and prove it is the published one. Returns the path.

    Hashed as it is written rather than afterwards, so the file is never read twice and a
    mismatch is known before anything else touches it. On a mismatch the file is deleted: a
    rejected installer left in temp is an installer somebody will find and run.
    """
    if not release.usable():
        raise Refused("the feed does not describe an installable release")
    where = into or keep_dir()
    os.makedirs(where, exist_ok=True)
    path = os.path.join(where, f"ServiceOfficerSetup-{release.version}.exe")
    digest = hashlib.sha256()
    read = 0
    request = urllib.request.Request(release.url,
                                     headers={"User-Agent": "ServiceOfficer"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer, \
                open(path, "wb") as out:
            while True:
                chunk = answer.read(256 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                if read > MOST_BYTES:
                    raise Refused(f"the download passed {MOST_BYTES // (1024 * 1024)} MB, "
                                  "which is not our installer")
                digest.update(chunk)
                out.write(chunk)
        found = digest.hexdigest()
        if found != release.sha256:
            raise Refused(f"the installer does not match the feed. Expected "
                          f"{release.sha256[:16]}…, got {found[:16]}…. Not running it.")
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    log.info("verified %s (%s bytes)", path, read)
    return path


def why_not_now(engine) -> str:
    """"" when an update may proceed, or what is in the way.

    Asked of the engine rather than tracked here: it already knows, and a second tally of the
    same thing is a second tally to get wrong. An engine that is None — a client — is never in
    the way, because a client is not doing any of this.
    """
    if engine is None:
        return ""
    runner = getattr(engine, "runner", None)
    if runner is not None and getattr(runner, "busy", False):
        return "a stack is running"
    if getattr(engine, "_in_flight", None):
        return "an action is still running"
    watchdog = getattr(engine, "watchdog", None)
    if watchdog is not None and getattr(watchdog, "_timers", None):
        # A recovery that is counting down. Restarting the watchdog now would drop the retry
        # and leave a stopped service stopped, with the history saying recovery was under way.
        return "a service is waiting to be restarted"
    return ""


#: The two ways to start the installer, and they are not interchangeable.
#:
#: The hub runs as LocalSystem, so it is already elevated and has no desktop to put a prompt
#: on: `CreateProcess`, detached. Detachment is the whole subtlety there — the installer stops
#: `ServiceOfficerHub`, which is the process that started it, so a child in this process's job
#: would be killed by the very step it was asked to perform, halfway through replacing files.
#:
#: A client panel is deliberately *not* elevated (see app.needs_elevation), and `CreateProcess`
#: does not elevate: launching an installer whose manifest requires administrator fails
#: outright with ERROR_ELEVATION_REQUIRED and shows nobody anything. `ShellExecuteW` is what
#: honours the manifest and puts the consent prompt on screen. The codebase already relies on
#: this distinction — `app.relaunch_elevated` exists for the same reason.
AS_SERVICE, ASK_THE_PERSON = "service", "ask"
ERROR_ELEVATION_REQUIRED = 740


def install(path: str, extra=(), how: str = AS_SERVICE) -> int:
    """Run the installer and let go of it. Returns a process id, or 0 when Windows started it.

    `/NORESTART` because an installer restarting Windows on an ERP server unasked is not
    something a background update may decide.
    """
    if not os.path.isfile(path):
        raise Refused(f"{path} is not there")
    arguments = ["/SILENT", "/NORESTART", *extra]
    if how == ASK_THE_PERSON:
        import ctypes
        answer = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", path, subprocess.list2cmdline(arguments), None, 1)
        # Anything above 32 is success. 5 is "the person said no", which is an answer and not a
        # failure — they keep the release they have and the panel goes on saying why it will
        # not connect.
        if answer == 5:
            raise Refused("administrator rights were refused, so nothing was installed")
        if answer <= 32:
            raise Refused(f"Windows would not start the installer (code {answer})")
        log.info("handed the installer to Windows for elevation")
        return 0
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    started = subprocess.Popen([path, *arguments], close_fds=True, creationflags=flags)
    log.info("started the installer as pid %s", started.pid)
    return started.pid


class Watcher:
    """Asks once a day, remembers the answer, and never installs anything.

    The remembering is the point: the UI has to be able to say "2.3.0 is available" without a
    network call every time somebody opens a page, and a failed check must not empty what was
    already known — a hub that loses its internet for an afternoon should not forget that an
    update exists.
    """

    def __init__(self, feed: str = FEED_URL, every: float = EVERY_SECONDS,
                 on_found=None, now=None):
        self.feed = feed
        self.every = every
        self._on_found = on_found
        self._now = now or time.monotonic
        #: The newest usable release, or None. Read by the UI thread; only ever replaced, never
        #: mutated in place, so no lock is needed for a reader.
        self.available = None
        #: Why the last check failed, worded, or "" — shown rather than swallowed, because
        #: "no updates" and "could not ask" are different facts.
        self.trouble = ""
        self.last_checked = 0.0
        self._stop = threading.Event()
        self._thread = None

    def check_now(self) -> object:
        """One check, on the calling thread. Returns the release or None."""
        self.last_checked = self._now()
        try:
            found = fetch(self.feed)
        except Exception as exc:
            self.trouble = f"could not read the release feed: {exc}"
            log.info("%s", self.trouble)
            return self.available
        self.trouble = ""
        if not found.usable():
            log.info("the feed does not describe an installable release")
            return self.available
        if found.skipped():
            log.info("release %s is below its own minimum %s — stepping over it",
                     found.version, found.minimum)
            return self.available
        if not found.newer_than():
            self.available = None
            return None
        if self.available is None or newer(found.version, self.available.version):
            self.available = found
            log.info("update available: %s", found.version)
            if self._on_found is not None:
                try:
                    self._on_found(found)
                except Exception:
                    log.exception("an update listener failed")
        return self.available

    # -- the daily loop ----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="update-check")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # A first check on start, then daily. Not on a timer that only fires after a day: a
        # server rebooted every night would never check at all.
        while not self._stop.is_set():
            self.check_now()
            self._stop.wait(self.every)
