"""Who may talk to the hub, and how a client knows it is the right hub.

Two separate questions, deliberately kept separate. A token says *you are allowed*; a
pinned certificate says *this is the machine you meant*. Neither answers the other, and
the second is the one that stops a token being handed to whoever answers on that port.
"""

import pytest

pytest.importorskip("win32crypt")

from core import hub_auth                                    # noqa: E402


@pytest.fixture(autouse=True)
def own_store(tmp_path, monkeypatch):
    """Its own secret store, so a test never reads or writes the real one."""
    from core import secrets
    monkeypatch.setattr(secrets, "SECRETS_PATH", str(tmp_path / "secrets.dat"))
    monkeypatch.setattr(secrets, "_last_error", "")


def test_a_token_is_long_enough_to_be_uninteresting():
    token = hub_auth.new_token()
    assert len(token) >= 32
    assert token != hub_auth.new_token()


def test_a_client_is_recognised_by_its_token():
    token = hub_auth.add_client("ismail-laptop")

    assert hub_auth.check(token) == "ismail-laptop"
    assert hub_auth.check("not-a-token") == ""
    assert hub_auth.check("") == ""


def test_revoking_a_client_stops_it_immediately():
    token = hub_auth.add_client("temp")
    assert hub_auth.revoke("temp") is True
    assert hub_auth.check(token) == ""
    assert hub_auth.revoke("temp") is False        # gone is not an error


def test_the_token_is_shown_once_and_only_its_hash_is_kept():
    """A store that can hand tokens back is a store worth stealing."""
    token = hub_auth.add_client("once")
    listed = hub_auth.clients()

    assert [c["name"] for c in listed] == ["once"]
    assert token not in repr(listed)
    from core import secrets
    assert token not in (secrets.get("hub/clients") or "")


def test_two_clients_do_not_share_a_token():
    first = hub_auth.add_client("one")
    second = hub_auth.add_client("two")

    assert first != second
    assert hub_auth.check(first) == "one"
    assert hub_auth.check(second) == "two"


def test_adding_the_same_name_twice_replaces_the_token():
    """Re-pairing a client that lost its token must not leave the old one working."""
    old = hub_auth.add_client("laptop")
    new = hub_auth.add_client("laptop")

    assert hub_auth.check(old) == ""
    assert hub_auth.check(new) == "laptop"
    assert [c["name"] for c in hub_auth.clients()] == ["laptop"]


def test_a_certificate_is_made_once_and_reused(tmp_path):
    path = str(tmp_path / "hub.pem")
    made, fingerprint = hub_auth.ensure_certificate(path)
    again, same = hub_auth.ensure_certificate(path)

    assert made == again == path
    assert fingerprint == same
    assert fingerprint.startswith("SHA256:")
    assert hub_auth.fingerprint_of(path) == fingerprint


def test_the_certificate_names_this_machine_and_carries_a_key(tmp_path):
    """A client connects by name, so the name has to be in it — and the file has to
    hold the private key too, or the server cannot serve with it."""
    import socket
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    path = str(tmp_path / "hub.pem")
    hub_auth.ensure_certificate(path)
    raw = open(path, "rb").read()

    assert b"PRIVATE KEY" in raw and b"CERTIFICATE" in raw
    cert = x509.load_pem_x509_certificate(raw)
    names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    assert names[0].value == socket.gethostname()
    alt = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert socket.gethostname() in alt.value.get_values_for_type(x509.DNSName)
    # Ten years: this is pinned by fingerprint, not trusted by a CA, so an expiry
    # that lapses would break every client for no security gain.
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days > 3000
    serialization.load_pem_private_key(raw, password=None)   # parses


def test_the_fingerprint_is_spelled_the_way_a_host_key_is(tmp_path):
    """Deliberately the same shape as the SSH host key already shown in the Machines
    page, so the same field and the same words can present both."""
    path = str(tmp_path / "hub.pem")
    _made, fingerprint = hub_auth.ensure_certificate(path)

    assert fingerprint.startswith("SHA256:")
    body = fingerprint.split(":", 1)[1]
    assert "=" not in body            # unpadded base64, as ssh-keygen prints it
    assert len(body) == 43


def test_a_replaced_certificate_has_a_different_fingerprint(tmp_path):
    """The pin exists to notice this. A hub rebuilt with a new certificate is either
    a rebuilt hub or not the hub, and the client must be able to tell."""
    path = str(tmp_path / "hub.pem")
    _p, first = hub_auth.ensure_certificate(path)
    import os
    os.remove(path)
    _p, second = hub_auth.ensure_certificate(path)

    assert first != second
