"""Who may talk to the hub, and how a client knows it is the right hub.

Two separate questions, and neither answers the other:

* **A token** says *you are allowed*. One per client, issued on the hub, shown once,
  revocable. Only its hash is stored, so a copy of the store is not a set of keys.
* **A pinned certificate** says *this is the machine you meant*. Self-signed, kept for
  ten years, identified by a SHA-256 fingerprint spelled exactly the way this app
  already spells an SSH host key — because it is the same idea and deserves the same
  words on screen.

Why not Windows-integrated authentication, which would need neither: the machines this
runs against are in a different forest from the workstations that manage them, measured
on 2026-07-26 — `CT\\ismail.orhan` is refused by `SC` no matter what rights it holds at
home. A token works across a boundary that Negotiate cannot cross.

Why not a CA-signed certificate: there is no CA here, and a self-signed certificate
verified by a pin is stronger in this setting than one verified by a chain nobody
checks. The pin is the verification, so it is checked on *every* connection.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets as stdlib_secrets
import socket
import threading

from . import applog
from . import secrets as store          # the project's DPAPI store, not the stdlib

log = applog.get("hubauth")

#: Where the client list lives in the DPAPI store. One entry holding JSON rather than
#: one entry per client: the list is read on every request, and one decrypt is cheaper
#: than N.
CLIENTS_REF = "hub/clients"
#: Long enough that guessing is not a strategy. 32 bytes is 43 url-safe characters.
TOKEN_BYTES = 32
#: Ten years. The certificate is pinned by fingerprint rather than trusted by a chain,
#: so an expiry that lapsed would break every client and buy nothing.
CERT_YEARS = 10

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
def new_token() -> str:
    """A token. Note the import name: this project has its own `secrets` module, and
    the standard library's is aliased, because reaching for the wrong one silently is
    the sort of thing that would only show up as a token nobody could use."""
    return stdlib_secrets.token_urlsafe(TOKEN_BYTES)


def _digest(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _read() -> dict:
    raw = store.get(CLIENTS_REF)
    if not raw:
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        log.warning("the client list could not be read; starting an empty one")
        return {}
    return found if isinstance(found, dict) else {}


def _write(clients: dict) -> bool:
    return store.put(CLIENTS_REF, json.dumps(clients))


def add_client(name: str) -> str:
    """Issue a token for a client and return it — the only time it is ever readable.

    Adding a name that already exists replaces its token, so re-pairing a client that
    lost one does not leave the old one working.
    """
    token = new_token()
    with _lock:
        clients = _read()
        clients[name] = {
            "hash": _digest(token),
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
            "last_seen": "",
        }
        if not _write(clients):
            raise RuntimeError(store.last_error()
                               or "the token could not be stored")
    log.info("client %s paired", name)
    return token


def check(token: str) -> str:
    """The client's name, or "" — and in constant time, so the comparison itself does
    not narrow down a guess."""
    if not token:
        return ""
    wanted = _digest(token)
    with _lock:
        for name, facts in _read().items():
            if hmac.compare_digest(str(facts.get("hash", "")), wanted):
                return name
    return ""


def note_seen(name: str) -> None:
    """Remember when a client last spoke, so `client list` can say. Best-effort: a
    failed write here must never refuse a request that was otherwise fine."""
    try:
        with _lock:
            clients = _read()
            if name in clients:
                clients[name]["last_seen"] = datetime.datetime.now().isoformat(
                    timespec="seconds")
                _write(clients)
    except Exception:
        log.debug("could not record that %s was seen", name, exc_info=True)


def clients() -> list:
    """Every paired client — name, when it was added, when it last spoke. No hashes:
    this is printed, and a hash on screen invites somebody to try it."""
    with _lock:
        return [{"name": name, "added": facts.get("added", ""),
                 "last_seen": facts.get("last_seen", "")}
                for name, facts in sorted(_read().items())]


def revoke(name: str) -> bool:
    """True if there was one to remove."""
    with _lock:
        found = _read()
        if name not in found:
            return False
        found.pop(name)
        _write(found)
    log.info("client %s revoked", name)
    return True


# ---------------------------------------------------------------------------
# the certificate
# ---------------------------------------------------------------------------
def _local_addresses() -> list:
    """Every IPv4 address this machine answers on, so a client that connects by
    address rather than by name still matches the certificate."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    found.add("127.0.0.1")
    return sorted(found)


def _make_certificate(path: str) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    host = socket.gethostname()
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    names = [x509.DNSName(host), x509.DNSName("localhost")]
    for address in _local_addresses():
        try:
            import ipaddress
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            continue
    cert = (x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365 * CERT_YEARS))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))

    body = (key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption())
            + cert.public_bytes(serialization.Encoding.PEM))
    # Written whole and then moved: a half-written certificate is a hub that cannot
    # start, and the file is read by the server on every restart.
    temporary = path + ".new"
    with open(temporary, "wb") as fh:
        fh.write(body)
    os.replace(temporary, path)
    log.info("made a certificate for %s", host)


def fingerprint_of(path: str) -> str:
    """SHA256:… over the certificate's DER, spelled the way ssh-keygen spells a host
    key: unpadded base64, so the two can share a field and a sentence on screen."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(open(path, "rb").read())
    raw = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)).digest()
    return "SHA256:" + base64.b64encode(raw).decode("ascii").rstrip("=")


def ensure_certificate(path: str) -> tuple:
    """(path, fingerprint). Made on first use and reused after — a new one every start
    would mean every client refusing every connection."""
    with _lock:
        if not os.path.exists(path):
            _make_certificate(path)
        return path, fingerprint_of(path)
