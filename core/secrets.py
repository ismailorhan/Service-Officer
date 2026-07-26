"""Passwords, kept somewhere better than the config file.

`services.json` is a document a person is invited to read and hand-edit, so a
password cannot live in it. These live in `secrets.dat` next to it, each value
encrypted with Windows' own DPAPI.

**Machine scope, not user scope.** `CRYPTPROTECT_LOCAL_MACHINE` matters for a
reason that has caught this project before: an administrator sets these up
interactively, and the thing that will one day *use* them is a Windows service
running as LocalSystem. A blob encrypted for the interactive user's profile is
unreadable to LocalSystem — the same class of mistake as the ProgramData ACL that
let admins create files no other admin could write.

What that costs, said plainly rather than hidden: machine scope means **any
administrator on this computer can decrypt these**. The file's ACL (Administrators
and SYSTEM, from the installer) is what keeps everyone else out. An administrator
can already do anything on the box, so this is not a new hole — but storing a
root password anywhere is a decision, and it should be made knowingly.

The blob does not travel: copied to another computer it is useless, which is the
other half of why machine scope is the right one.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading

from . import config as cfg_mod

SECRETS_PATH = os.path.join(cfg_mod.APP_DIR, "secrets.dat")
#: Shown by Windows in a DPAPI prompt, and stored beside the blob.
DESCRIPTION = "Service Officer"

_lock = threading.Lock()
_last_error = ""


def last_error() -> str:
    return _last_error


def ref_for_machine(name: str) -> str:
    """A stable name for a machine's password. Not the password, and not a path —
    just a key, so the config can point at one without containing one."""
    return f"machine/{name or 'this-computer'}"


def _load() -> dict:
    try:
        with open(SECRETS_PATH, "r", encoding="utf-8") as fh:
            found = json.load(fh)
        return found if isinstance(found, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(entries: dict) -> bool:
    global _last_error
    try:
        os.makedirs(os.path.dirname(SECRETS_PATH), exist_ok=True)
        # Atomic, like the config: a torn secrets file would lock someone out of
        # every machine at once.
        handle, tmp = tempfile.mkstemp(dir=os.path.dirname(SECRETS_PATH),
                                       prefix=".sec-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, SECRETS_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _last_error = ""
        return True
    except OSError as exc:
        _last_error = f"{SECRETS_PATH}: {getattr(exc, 'strerror', None) or exc}"
        return False


def _protect(value: str) -> str:
    import win32crypt
    import win32cryptcon
    blob = win32crypt.CryptProtectData(
        value.encode("utf-8"), DESCRIPTION, None, None, None,
        win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE)
    return base64.b64encode(blob).decode("ascii")


def _unprotect(stored: str) -> str:
    import win32crypt
    _description, plain = win32crypt.CryptUnprotectData(
        base64.b64decode(stored.encode("ascii")), None, None, None, 0)
    return plain.decode("utf-8")


def put(ref: str, value: str) -> bool:
    """Store (or replace) a secret. An empty value removes it — there is no
    reason to keep an encrypted empty string around."""
    global _last_error
    if not value:
        return forget(ref)
    try:
        sealed = _protect(value)
    except Exception as exc:
        _last_error = f"could not encrypt: {type(exc).__name__}: {exc}"
        return False
    with _lock:
        entries = _load()
        entries[ref] = sealed
        return _write(entries)


def get(ref: str) -> str:
    """The secret, or "" — never raises, and never logs what it found."""
    global _last_error
    if not ref:
        return ""
    with _lock:
        stored = _load().get(ref)
    if not stored:
        return ""
    try:
        return _unprotect(stored)
    except Exception as exc:
        # A blob from another computer, or a tampered one. Both mean "you have no
        # usable password here", which the caller must be able to say out loud.
        _last_error = (f"{ref} could not be decrypted on this computer: "
                       f"{type(exc).__name__}")
        return ""


def has(ref: str) -> bool:
    """Is something stored under this name? Asked by the UI, which must be able to
    say "a password is saved" without decrypting it to find out."""
    if not ref:
        return False
    with _lock:
        return bool(_load().get(ref))


def forget(ref: str) -> bool:
    with _lock:
        entries = _load()
        if ref not in entries:
            return True
        del entries[ref]
        return _write(entries)


def refs() -> list:
    with _lock:
        return sorted(_load())
