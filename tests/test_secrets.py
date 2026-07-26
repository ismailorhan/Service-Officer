"""Passwords: where they go, and — more importantly — where they don't."""

import json

import pytest

from core import config as cfg_mod
from core import secrets

pytest.importorskip("win32crypt")


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))
    return tmp_path / "secrets.dat"


def test_a_secret_survives_a_round_trip(store):
    assert secrets.put("machine/hanadev", "s3cret pass") is True
    assert secrets.get("machine/hanadev") == "s3cret pass"
    assert secrets.has("machine/hanadev") is True


def test_the_stored_file_does_not_contain_the_password(store):
    """The whole point. If the password is readable in the file, the encryption
    was decoration."""
    secrets.put("machine/hanadev", "CorrectHorse42")

    raw = store.read_text(encoding="utf-8")

    assert "CorrectHorse42" not in raw
    assert "machine/hanadev" in raw          # the name is not a secret
    entries = json.loads(raw)
    assert len(entries["machine/hanadev"]) > 100, "suspiciously short for a blob"


def test_a_password_is_never_written_into_the_config():
    """services.json is a document people are invited to hand-edit. It holds the
    *name* of a secret, never the secret."""
    machine = cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                              secret_ref=secrets.ref_for_machine("hanadev"))
    cfg = cfg_mod.Config(machines=[cfg_mod.Machine(), machine])
    secrets.put(machine.secret_ref, "hunter2")

    written = json.dumps(cfg_mod.to_dict(cfg))

    assert "hunter2" not in written
    assert "machine/hanadev" in written
    assert cfg_mod.from_dict(json.loads(written)).machine("hanadev").secret_ref \
        == "machine/hanadev"


def test_forgetting_really_forgets(store):
    secrets.put("machine/x", "abc")
    assert secrets.forget("machine/x") is True
    assert secrets.get("machine/x") == ""
    assert secrets.has("machine/x") is False
    assert "abc" not in store.read_text(encoding="utf-8")


def test_storing_an_empty_value_removes_the_entry(store):
    secrets.put("machine/x", "abc")
    secrets.put("machine/x", "")
    assert secrets.has("machine/x") is False


def test_asking_for_something_that_was_never_stored_is_not_an_error():
    assert secrets.get("machine/never") == ""
    assert secrets.get("") == ""
    assert secrets.has("") is False


def test_a_tampered_blob_yields_nothing_and_says_why(store):
    """A blob from another computer looks exactly like a corrupted one, and both
    mean "there is no usable password here" — which the UI has to be able to
    say out loud rather than hanging on a failed connection."""
    secrets.put("machine/x", "abc")
    entries = json.loads(store.read_text(encoding="utf-8"))
    entries["machine/x"] = entries["machine/x"][:-8] + "AAAAAAAA"
    store.write_text(json.dumps(entries), encoding="utf-8")

    assert secrets.get("machine/x") == ""
    assert "could not be decrypted" in secrets.last_error()


def test_several_machines_keep_separate_secrets(store):
    secrets.put(secrets.ref_for_machine("hanadev"), "one")
    secrets.put(secrets.ref_for_machine("CTL052"), "two")

    assert secrets.get("machine/hanadev") == "one"
    assert secrets.get("machine/CTL052") == "two"
    assert secrets.refs() == ["machine/CTL052", "machine/hanadev"]


def test_a_corrupt_secrets_file_does_not_take_the_app_down(store):
    store.write_text("this is not json at all", encoding="utf-8")

    assert secrets.get("machine/x") == ""
    assert secrets.has("machine/x") is False
    # And it can be written over rather than needing a human to delete it.
    assert secrets.put("machine/x", "fresh") is True
    assert secrets.get("machine/x") == "fresh"


def test_the_transport_reads_the_store_and_not_the_config(monkeypatch, store):
    """The connector must go to the store for a password, so a config file on its
    own can never be enough to reach a machine."""
    from core import ssh_linux

    machine = cfg_mod.Machine(name="hanadev", kind="linux", auth="password",
                              secret_ref="machine/hanadev")
    assert ssh_linux._secret_for(machine) == ""

    secrets.put("machine/hanadev", "letmein")
    assert ssh_linux._secret_for(machine) == "letmein"

    # A machine set to key authentication does not get one handed to paramiko.
    machine.auth = "key"
    assert ssh_linux._secret_for(machine) == "letmein"   # the store still has it
