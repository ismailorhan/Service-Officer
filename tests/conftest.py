"""Keep the test run out of the installed app's data.

The application log is a module-level singleton pointed at
%ProgramData%\\Service Officer\\service-officer.log, so simply importing core.health
in a test made the suite write into the real log — and the lines it wrote were
things like "Svc failed 2 checks in a row; restarting it", which read exactly like
a production incident. A log that cannot be trusted is worse than no log.

This redirects it before any test imports anything that logs. The same applies to
the history and the config: tests that touch them pass explicit paths, and
APP_DIR is redirected here so a missed one lands in a temp directory rather than
in the installed app's.
"""

import tempfile
from pathlib import Path

import pytest

from core import applog
from core import config as cfg_mod

_SANDBOX = Path(tempfile.gettempdir()) / "service-officer-tests"
_SANDBOX.mkdir(parents=True, exist_ok=True)

#: Where the app would really put things. Kept because redirecting the constants
#: below is exactly what stops a test from being able to check them.
REAL_APP_DIR = cfg_mod.APP_DIR
REAL_LEGACY_DIRS = cfg_mod.LEGACY_DIRS

# Done at import time, not in a fixture: the logger is configured on first use,
# which can happen while a test module is still being imported.
cfg_mod.APP_DIR = str(_SANDBOX)
cfg_mod.CONFIG_PATH = str(_SANDBOX / "services.json")
applog.LOG_PATH = str(_SANDBOX / "service-officer.log")

from core import history                                   # noqa: E402
history.HISTORY_PATH = str(_SANDBOX / "history.jsonl")


@pytest.fixture
def real_paths():
    """The directories the app would actually use, for the tests that check them."""
    return {"app_dir": REAL_APP_DIR, "legacy_dirs": REAL_LEGACY_DIRS}


@pytest.fixture(autouse=True)
def _no_real_data():
    """Fail loudly if something still points at the installed app's directory."""
    assert "ProgramData" not in applog.LOG_PATH
    assert "ProgramData" not in history.HISTORY_PATH
    yield


def pytest_report_header(config):
    return f"test data sandbox: {_SANDBOX}"
