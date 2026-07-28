"""Rotating application log.

On a customer's server, "it didn't restart the service" is unanswerable without
one. Written next to the config so it travels with the install, size-capped so it
can be forgotten about.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from . import config as cfg_mod

LOG_PATH = os.path.join(cfg_mod.APP_DIR, "service-officer.log")
#: Where a process that cannot write the machine's log puts its own. The hub owns
#: APP_DIR and, once the installer has stopped ProgramData handing `Write` to everybody,
#: an unelevated tray application cannot write there at all — and a client with no log is
#: a client nobody can troubleshoot.
USER_LOG_PATH = os.path.join(cfg_mod.USER_DIR, "service-officer.log")
#: Which of the two this process ended up with, so About and the History page can say.
log_path = LOG_PATH
_configured = False


def setup(level=logging.INFO, to_stderr: bool = False) -> logging.Logger:
    global _configured
    log = logging.getLogger("serviceofficer")
    if _configured:
        return log
    log.setLevel(level)
    global log_path
    for candidate in (LOG_PATH, USER_LOG_PATH):
        try:
            os.makedirs(os.path.dirname(candidate), exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                candidate, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            log.addHandler(handler)
            log_path = candidate
            break
        except OSError:
            # The machine's log first, so a single-machine install keeps one file with
            # everything in it. A client that is refused falls back to its own rather
            # than running silently.
            continue
    if to_stderr and sys.stderr:
        log.addHandler(logging.StreamHandler(sys.stderr))
    _quieten_paramiko()
    _configured = True
    log.propagate = False
    return log


def _quieten_paramiko() -> None:
    """Stop paramiko printing a traceback for a machine that is merely switched off.

    It logs "Error reading SSH protocol banner" at ERROR with the full stack whenever a
    connection does not complete — which for us is an ordinary, expected answer that the
    poller already reports as one sentence on the machine's row. Left alone, a SUSE box
    that is off fills the hub's log with tracebacks every retry, and the log is the thing
    that has to answer "why did it restart at 04:12".

    Critical only, and no propagation: paramiko's own failures that matter arrive here as
    exceptions we catch and describe.
    """
    for name in ("paramiko", "paramiko.transport", "paramiko.transport.sftp"):
        quiet = logging.getLogger(name)
        quiet.setLevel(logging.CRITICAL)
        quiet.propagate = False
        if not quiet.handlers:
            quiet.addHandler(logging.NullHandler())


def get(name: str = "") -> logging.Logger:
    base = setup()
    return base.getChild(name) if name else base
