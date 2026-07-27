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
