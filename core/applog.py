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
_configured = False


def setup(level=logging.INFO, to_stderr: bool = False) -> logging.Logger:
    global _configured
    log = logging.getLogger("serviceofficer")
    if _configured:
        return log
    log.setLevel(level)
    try:
        os.makedirs(cfg_mod.APP_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass
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
