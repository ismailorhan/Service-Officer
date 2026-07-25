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
    _configured = True
    log.propagate = False
    return log


def get(name: str = "") -> logging.Logger:
    base = setup()
    return base.getChild(name) if name else base
