"""This client's own settings, which are not the landscape's.

Two kinds of setting exist once there is a hub, and mixing them means five people each
believing they can change the theme for everyone, or that history retention is somebody
else's problem:

  the landscape — services, machines, stacks, triggers, retention — one copy, on the hub
  this client  — which hub, its token, the pinned certificate, theme, autostart, whether
                 *this* screen shows notifications — on the machine it runs on

`client.json` is the second one. It is not a second config: it holds nothing about any
service, and losing it costs a re-pairing.
"""

import json

import pytest

from core import local


@pytest.fixture(autouse=True)
def own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "PATH", str(tmp_path / "client.json"))
    from core import secrets
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))


def test_a_missing_file_is_defaults_not_an_error():
    """A fresh install has none, and the app has to open anyway."""
    settings = local.load()

    assert settings.hub_url == ""
    assert settings.theme == "system"
    assert settings.notify is True


def test_it_round_trips():
    settings = local.load()
    settings.hub_url = "https://ctl052:8797"
    settings.hub_fingerprint = "SHA256:abc"
    settings.theme = "dark"
    settings.notify = False
    local.save(settings)

    back = local.load()
    assert back.hub_url == "https://ctl052:8797"
    assert back.hub_fingerprint == "SHA256:abc"
    assert back.theme == "dark"
    assert back.notify is False


def test_the_token_is_not_in_the_file(tmp_path):
    """It goes in the DPAPI store like every other secret. client.json is a document
    somebody may open, and a bearer token in it is a password in a text file."""
    url = "https://ctl052:8797"
    assert local.set_token(url, "a-real-token") is True

    raw = (tmp_path / "client.json").read_text(encoding="utf-8") \
        if (tmp_path / "client.json").exists() else ""
    assert "a-real-token" not in raw
    assert local.token(url) == "a-real-token"


def test_a_token_is_kept_per_hub():
    """Somebody with a test hub and a real one must not have one overwrite the other."""
    local.set_token("https://one:8797", "first")
    local.set_token("https://two:8797", "second")

    assert local.token("https://one:8797") == "first"
    assert local.token("https://two:8797") == "second"


def test_forgetting_a_token_leaves_the_rest_alone():
    local.set_token("https://one:8797", "first")
    local.set_token("https://two:8797", "second")

    local.forget_token("https://one:8797")

    assert local.token("https://one:8797") == ""
    assert local.token("https://two:8797") == "second"


def test_a_broken_file_does_not_stop_the_app(tmp_path):
    """Half-written by a crash, or hand-edited badly. Defaults and a log line beat a
    client that will not start."""
    (tmp_path / "client.json").write_text("{not json", encoding="utf-8")

    settings = local.load()

    assert settings.hub_url == ""


def test_the_file_is_written_whole(tmp_path):
    """Replaced rather than truncated and rewritten: a crash mid-write must not leave a
    client that cannot read its own settings."""
    settings = local.load()
    settings.hub_url = "https://ctl052:8797"
    local.save(settings)

    written = json.loads((tmp_path / "client.json").read_text(encoding="utf-8"))
    assert written["hub_url"] == "https://ctl052:8797"
    assert list((tmp_path).glob("*.tmp")) == []      # nothing left behind


def test_an_unknown_field_in_the_file_is_ignored(tmp_path):
    """A newer client wrote it, or somebody experimented. Unknown keys are dropped
    rather than raising, the same rule services.json follows."""
    (tmp_path / "client.json").write_text(
        json.dumps({"hub_url": "https://x:1", "from_the_future": 42}),
        encoding="utf-8")

    settings = local.load()

    assert settings.hub_url == "https://x:1"
    assert not hasattr(settings, "from_the_future")


# ---------------------------------------------------------------------------
# who owns which file
# ---------------------------------------------------------------------------
# The tray application stopped running elevated in 2.2.0, and the data directory
# stopped being writable by non-administrators in 2.2.1 — because the hub reads
# services.json as LocalSystem and a `command` health check is a shell command line, so a
# user-writable copy of that file was a way to run code as SYSTEM.
#
# Both of those are right, and together they mean a client cannot write where it used to.
# What it must still be able to do is remember its own pairing.
def test_a_client_writes_its_own_copy_not_the_machines(tmp_path, monkeypatch):
    from core import config as cfg_mod

    machine = tmp_path / "ProgramData" / "client.json"
    machine.parent.mkdir(parents=True)
    machine.write_text('{"hub_url": "https://installed:8797"}', encoding="utf-8")
    monkeypatch.setattr(local, "MACHINE_PATH", str(machine))
    monkeypatch.setattr(local, "PATH", str(tmp_path / "user" / "client.json"))

    settings = local.load()
    assert settings.hub_url == "https://installed:8797", "did not inherit the pairing"

    settings.theme = "dark"
    assert local.save(settings) is True

    assert (tmp_path / "user" / "client.json").exists()
    assert machine.read_text(encoding="utf-8") == \
        '{"hub_url": "https://installed:8797"}', "wrote over the machine's copy"


def test_the_users_own_copy_wins_once_there_is_one(tmp_path, monkeypatch):
    """Otherwise a machine-wide file dropped by a deployment tool would silently
    repoint somebody who had chosen their own hub."""
    from core import config as cfg_mod

    machine = tmp_path / "machine.json"
    machine.write_text('{"hub_url": "https://installed:8797"}', encoding="utf-8")
    mine = tmp_path / "mine.json"
    mine.write_text('{"hub_url": "https://mine:8797", "theme": "dark"}',
                    encoding="utf-8")
    monkeypatch.setattr(local, "MACHINE_PATH", str(machine))
    monkeypatch.setattr(local, "PATH", str(mine))

    settings = local.load()

    assert settings.hub_url == "https://mine:8797"
    assert settings.theme == "dark"


def test_a_token_is_read_from_either_store_and_written_to_ours(tmp_path, monkeypatch):
    """`client pair --local` writes the machine's store as SYSTEM at install time. A user
    can read that and cannot write it, which is the right way round."""
    from core import secrets

    machine_store = str(tmp_path / "machine-secrets.dat")
    user_store = str(tmp_path / "user-secrets.dat")
    monkeypatch.setattr(secrets, "SECRETS_PATH", machine_store)
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", user_store)
    url = "https://installed:8797"

    # What the installer left, in the machine's store.
    secrets.put(local._token_ref(url), "from-the-installer")
    assert local.token(url) == "from-the-installer"

    # What this user does afterwards goes in theirs, and wins.
    assert local.set_token(url, "mine") is True
    assert local.token(url) == "mine"
    assert secrets.get(local._token_ref(url), path=machine_store) == \
        "from-the-installer", "overwrote the machine's token"

    # And forgetting is only ever ours to forget.
    local.forget_token(url)
    assert local.token(url) == "from-the-installer"
