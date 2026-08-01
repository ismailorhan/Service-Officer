"""The feed a release publishes, and whether the product can read it.

Two halves that have to agree, and nothing checked that they did: `tools/make_feed.py` writes
`latest.json` and `core/updates.py` reads it. So the interesting test is not "does the writer
write JSON" — it is to hand what the writer produced to the reader and require that the reader
finds it installable.

The hash is the point of the exercise. It is computed rather than typed because a hash written
by hand is a hash written wrong, and the one thing it decides is whether an installer runs with
administrator rights.
"""

import hashlib
import json

import pytest

from core import updates
from tools import make_feed


@pytest.fixture
def a_release(tmp_path, monkeypatch):
    """A pretend installer and a stamped version, laid out as the build leaves them."""
    setup = tmp_path / "dist" / "ServiceOfficerSetup.exe"
    setup.parent.mkdir(parents=True)
    setup.write_bytes(b"pretend installer" * 1000)
    stamped = tmp_path / "installer-version.txt"
    stamped.write_text("2.3.0\n", encoding="utf-8")
    monkeypatch.setattr(make_feed, "STAMPED", stamped)
    return setup, tmp_path / "dist" / "latest.json"


def test_what_the_writer_writes_the_reader_can_install(a_release):
    """The closing of the loop. A feed the product cannot use is not a feed."""
    setup, out = a_release
    assert make_feed.main(["--setup", str(setup), "--out", str(out),
                           "--notes", "things changed"]) == 0

    release = updates.Release(json.loads(out.read_text(encoding="utf-8")))
    assert release.usable(), "the product cannot install what the build published"
    assert release.version == "2.3.0"
    assert release.notes == "things changed"
    assert release.sha256 == hashlib.sha256(setup.read_bytes()).hexdigest()
    assert release.url.endswith("/v2.3.0/ServiceOfficerSetup.exe")


def test_the_hash_is_of_the_file_and_not_of_anything_else(a_release):
    setup, out = a_release
    make_feed.main(["--setup", str(setup), "--out", str(out)])
    first = json.loads(out.read_text(encoding="utf-8"))["sha256"]

    setup.write_bytes(setup.read_bytes() + b"one more byte")
    make_feed.main(["--setup", str(setup), "--out", str(out)])
    second = json.loads(out.read_text(encoding="utf-8"))["sha256"]
    assert first != second, "the hash did not follow the file"


def test_a_feed_offers_the_release_to_an_older_client(a_release, monkeypatch):
    """What the whole feature turns on: an older client reads this and sees an upgrade."""
    setup, out = a_release
    make_feed.main(["--setup", str(setup), "--out", str(out)])
    release = updates.Release(json.loads(out.read_text(encoding="utf-8")))
    assert release.newer_than("2.2.7")
    assert not release.newer_than("2.3.0")
    assert not release.skipped()


def test_a_minimum_travels_into_the_feed(a_release):
    setup, out = a_release
    make_feed.main(["--setup", str(setup), "--out", str(out), "--minimum", "2.4.0"])
    release = updates.Release(json.loads(out.read_text(encoding="utf-8")))
    # A release below its own floor is stepped over — how a broken version gets skipped.
    assert release.skipped()


def test_no_installer_is_a_clear_refusal(a_release, capsys):
    _setup, out = a_release
    assert make_feed.main(["--setup", str(out.parent / "nope.exe"),
                           "--out", str(out)]) == 1
    assert "not there" in capsys.readouterr().out


def test_an_internal_build_is_flagged_rather_than_published_quietly(a_release, capsys,
                                                                   monkeypatch):
    """A fourth part means a build, not a release. The url would point at a tag that does not
    exist, and a client offered an installer it cannot download is worse than no offer."""
    setup, out = a_release
    make_feed.STAMPED.write_text("2.3.0.17\n", encoding="utf-8")
    assert make_feed.main(["--setup", str(setup), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "internal build" in said, said
