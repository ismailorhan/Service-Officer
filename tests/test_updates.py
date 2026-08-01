"""Reading the release feed, proving the download, and refusing a bad moment.

Nothing here installs anything — `install()` is one `Popen` and its interesting part is the
detachment, which is asserted by reading the flags rather than by starting a real installer.

The three questions are tested separately because they fail separately: a feed can be
unreachable while the last answer is still worth showing, a download can be the wrong file, and
a perfectly good update can arrive while a stack is halfway up.
"""

import hashlib
import json
import os

import pytest

from core import updates, version


# ── which release is newer ────────────────────────────────────────────────
def test_versions_compare_as_numbers_not_as_text():
    """"2.2.10" is newer than "2.2.9". A string comparison gets that exactly backwards, and
    the tenth patch of a release is not a hypothetical."""
    assert updates.newer("2.2.10", "2.2.9")
    assert not updates.newer("2.2.9", "2.2.10")
    assert updates.newer("2.3.0", "2.2.99")
    assert updates.newer("3.0.0", "2.99.99")


def test_the_same_release_is_not_newer():
    assert not updates.newer("2.2.7", "2.2.7")
    # The build counter is not part of it: 2.2.7.17 is 2.2.7 built again, not an upgrade.
    assert not updates.newer("2.2.7.17", "2.2.7.4")


def test_an_unreadable_version_does_not_look_like_an_upgrade():
    """A feed that arrives mangled must not offer to install itself over a working install."""
    assert not updates.newer("", "2.2.7")
    assert not updates.newer("dev", "2.2.7")
    assert not updates.newer("banana", "2.2.7")


# ── what the feed has to say ──────────────────────────────────────────────
def _feed(**over):
    raw = {"version": "2.3.0", "url": "https://example.invalid/Setup.exe",
           "sha256": "a" * 64, "notes": "things"}
    raw.update(over)
    return updates.Release(raw)


def test_a_feed_with_no_hash_is_not_installable():
    """The alternative is running an unverified installer as administrator. "The publisher
    forgot a field" is not a good enough reason."""
    assert _feed().usable()
    assert not _feed(sha256="").usable()
    assert not _feed(sha256="tooshort").usable()
    assert not _feed(url="").usable()
    assert not _feed(version="").usable()


def test_a_release_can_be_marked_to_step_over():
    """How a broken version gets skipped rather than recalled: the feed names a floor, and a
    release below its own floor is not offered."""
    assert not _feed().skipped()
    assert not _feed(version="2.3.0", minimum="2.3.0").skipped()
    assert _feed(version="2.3.0", minimum="2.3.1").skipped()


# ── proving the download ──────────────────────────────────────────────────
class _Answer:
    """A urlopen result that hands back bytes in chunks."""

    def __init__(self, body):
        self._left = body

    def read(self, size=-1):
        if size is None or size < 0:
            body, self._left = self._left, b""
            return body
        body, self._left = self._left[:size], self._left[size:]
        return body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _serve(monkeypatch, body):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *_a, **_k: _Answer(body))


def test_a_verified_download_is_kept(monkeypatch, tmp_path):
    body = b"pretend installer" * 100
    _serve(monkeypatch, body)
    release = _feed(sha256=hashlib.sha256(body).hexdigest())
    path = updates.download(release, into=str(tmp_path))
    assert os.path.isfile(path)
    assert open(path, "rb").read() == body


def test_a_download_that_does_not_match_is_refused_and_deleted(monkeypatch, tmp_path):
    """And deleted: a rejected installer left in temp is an installer somebody will find and
    run, which is worse than no update at all."""
    _serve(monkeypatch, b"something else entirely")
    with pytest.raises(updates.Refused) as caught:
        updates.download(_feed(sha256="b" * 64), into=str(tmp_path))
    assert "does not match" in str(caught.value)
    assert list(tmp_path.iterdir()) == [], "the rejected file was left behind"


def test_an_endless_download_is_cut_off(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "MOST_BYTES", 1024)
    _serve(monkeypatch, b"x" * 4096)
    with pytest.raises(updates.Refused):
        updates.download(_feed(sha256="c" * 64), into=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_a_feed_with_no_hash_never_reaches_the_network(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise AssertionError("the network was touched for an unusable release")
    monkeypatch.setattr(updates.urllib.request, "urlopen", refuse)
    with pytest.raises(updates.Refused):
        updates.download(_feed(sha256=""), into=str(tmp_path))


# ── is now a good moment ──────────────────────────────────────────────────
class _Engine:
    """The three things the engine already knows about, and nothing else."""

    class _Runner:
        busy = False

    class _Watchdog:
        def __init__(self):
            self._timers = {}

    def __init__(self):
        self.runner = self._Runner()
        self.watchdog = self._Watchdog()
        self._in_flight = set()


def test_a_quiet_engine_may_be_updated():
    assert updates.why_not_now(_Engine()) == ""


def test_a_client_is_never_in_the_way():
    """A client has no engine — it is not running any of this, so there is nothing to
    interrupt."""
    assert updates.why_not_now(None) == ""


def test_a_running_stack_stops_an_update():
    engine = _Engine()
    engine.runner.busy = True
    assert "stack" in updates.why_not_now(engine)


def test_an_action_in_flight_stops_an_update():
    engine = _Engine()
    engine._in_flight.add("a1")
    assert "action" in updates.why_not_now(engine)


def test_a_recovery_counting_down_stops_an_update():
    """Restarting the watchdog now would drop the retry and leave a stopped service stopped,
    with the history saying a recovery was under way."""
    engine = _Engine()
    engine.watchdog._timers["AppEngine"] = object()
    assert "restarted" in updates.why_not_now(engine)


# ── remembering the answer ────────────────────────────────────────────────
def _watcher(monkeypatch, body, **kw):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *_a, **_k: _Answer(json.dumps(body).encode()))
    return updates.Watcher(**kw)


def test_a_newer_release_is_remembered_and_announced(monkeypatch):
    monkeypatch.setattr(version, "VERSION", "2.2.7")
    monkeypatch.setattr(version, "BUILD", 0)
    seen = []
    watcher = _watcher(monkeypatch,
                       {"version": "2.3.0", "url": "https://example.invalid/s.exe",
                        "sha256": "a" * 64, "notes": "n"},
                       on_found=seen.append)
    found = watcher.check_now()
    assert found is not None and found.version == "2.3.0"
    assert watcher.available is found
    assert [r.version for r in seen] == ["2.3.0"]


def test_the_same_release_is_not_announced_twice(monkeypatch):
    monkeypatch.setattr(version, "VERSION", "2.2.7")
    monkeypatch.setattr(version, "BUILD", 0)
    seen = []
    watcher = _watcher(monkeypatch,
                       {"version": "2.3.0", "url": "https://example.invalid/s.exe",
                        "sha256": "a" * 64},
                       on_found=seen.append)
    watcher.check_now()
    watcher.check_now()
    assert len(seen) == 1, "a daily check would toast every day for ever"


def test_a_failed_check_keeps_what_was_already_known(monkeypatch):
    """A hub that loses its internet for an afternoon should not forget that an update
    exists — and "could not ask" is a different fact from "nothing new", so it is kept
    separately rather than folded into an empty answer."""
    monkeypatch.setattr(version, "VERSION", "2.2.7")
    monkeypatch.setattr(version, "BUILD", 0)
    watcher = _watcher(monkeypatch,
                       {"version": "2.3.0", "url": "https://example.invalid/s.exe",
                        "sha256": "a" * 64})
    watcher.check_now()
    assert watcher.available is not None

    def broken(*_a, **_k):
        raise OSError("no route to host")
    monkeypatch.setattr(updates.urllib.request, "urlopen", broken)
    watcher.check_now()
    assert watcher.available is not None, "forgot a known update because the network blipped"
    assert "no route to host" in watcher.trouble


def test_a_release_we_already_have_clears_the_badge(monkeypatch):
    monkeypatch.setattr(version, "VERSION", "2.3.0")
    monkeypatch.setattr(version, "BUILD", 0)
    watcher = _watcher(monkeypatch,
                       {"version": "2.3.0", "url": "https://example.invalid/s.exe",
                        "sha256": "a" * 64})
    assert watcher.check_now() is None
    assert watcher.available is None


# ── letting go of the installer ───────────────────────────────────────────
def test_the_installer_is_started_detached(monkeypatch, tmp_path):
    """It stops ServiceOfficerHub, which is the process starting it. A child in this process's
    group would be killed by the very step it was asked to perform, halfway through replacing
    the files."""
    setup = tmp_path / "ServiceOfficerSetup.exe"
    setup.write_bytes(b"MZ")
    seen = {}

    class _Started:
        pid = 4242

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Started()

    monkeypatch.setattr(updates.subprocess, "Popen", fake_popen)
    assert updates.install(str(setup)) == 4242
    assert seen["argv"][1:] == ["/SILENT", "/NORESTART"]
    flags = seen["kw"]["creationflags"]
    assert flags & updates.subprocess.DETACHED_PROCESS
    assert flags & updates.subprocess.CREATE_NEW_PROCESS_GROUP
    assert seen["kw"]["close_fds"] is True


def test_installing_something_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(updates.Refused):
        updates.install(str(tmp_path / "nope.exe"))


# ── keeping the installer for the clients ─────────────────────────────────
def test_the_installer_is_found_by_the_version_in_its_name(monkeypatch, tmp_path):
    """After an update the process that downloaded it is gone — replaced by the one it
    installed. A new hub finding its own installer by name needs no state carried across that
    restart, which is why the name carries the version."""
    monkeypatch.setattr(updates, "keep_dir", lambda: str(tmp_path))
    monkeypatch.setattr(version, "VERSION", "2.3.0")
    monkeypatch.setattr(version, "BUILD", 0)

    assert updates.kept() == ""
    (tmp_path / "ServiceOfficerSetup-2.3.0.exe").write_bytes(b"MZ")
    assert updates.kept().endswith("ServiceOfficerSetup-2.3.0.exe")

    # A build of the same release counts: 2.3.0.4 and 2.3.0 are one release built twice.
    monkeypatch.setattr(version, "BUILD", 4)
    assert updates.kept() != ""
    # A different release does not.
    monkeypatch.setattr(version, "VERSION", "2.4.0")
    assert updates.kept() == ""


def test_a_missing_directory_is_not_an_error(monkeypatch, tmp_path):
    """A hub installed by hand has never downloaded anything, so the directory is simply not
    there — and "no installer to hand out" is an ordinary answer."""
    monkeypatch.setattr(updates, "keep_dir", lambda: str(tmp_path / "nope"))
    assert updates.kept() == ""


def test_the_hash_of_a_kept_file_is_readable(tmp_path):
    body = b"pretend installer"
    path = tmp_path / "s.exe"
    path.write_bytes(body)
    assert updates.sha256_of(str(path)) == hashlib.sha256(body).hexdigest()


# ── the two ways to start it ──────────────────────────────────────────────
def test_a_client_hands_the_installer_to_windows(monkeypatch, tmp_path):
    """CreateProcess does not elevate: an installer whose manifest requires administrator fails
    outright from an unelevated process and shows nobody anything. ShellExecute honours the
    manifest and puts the consent prompt on screen — which is the whole of option (a)."""
    setup = tmp_path / "s.exe"
    setup.write_bytes(b"MZ")
    seen = {}

    class _Shell:
        @staticmethod
        def ShellExecuteW(parent, verb, path, args, folder, show):
            seen.update(verb=verb, path=path, args=args)
            return 42

    class _Windll:
        shell32 = _Shell()

    import ctypes
    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)

    def refuse(*_a, **_k):
        raise AssertionError("Popen was used, which cannot elevate")
    monkeypatch.setattr(updates.subprocess, "Popen", refuse)

    assert updates.install(str(setup), how=updates.ASK_THE_PERSON) == 0
    assert seen["verb"] == "runas"
    assert "/SILENT" in seen["args"] and "/NORESTART" in seen["args"]


def test_a_refused_prompt_is_an_answer_not_a_crash(monkeypatch, tmp_path):
    """They keep the release they have, and the panel goes on saying why it will not connect."""
    setup = tmp_path / "s.exe"
    setup.write_bytes(b"MZ")

    class _Shell:
        @staticmethod
        def ShellExecuteW(*_a):
            return 5                      # the person said no

    class _Windll:
        shell32 = _Shell()

    import ctypes
    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)
    with pytest.raises(updates.Refused) as caught:
        updates.install(str(setup), how=updates.ASK_THE_PERSON)
    assert "refused" in str(caught.value)
