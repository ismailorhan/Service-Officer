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
cfg_mod.USER_DIR = str(_SANDBOX / "user")
cfg_mod.CONFIG_PATH = str(_SANDBOX / "services.json")
applog.LOG_PATH = str(_SANDBOX / "service-officer.log")
# The fallback as well: a process that cannot write the first one must not reach the real
# profile — see applog.setup.
applog.USER_LOG_PATH = str(_SANDBOX / "user" / "service-officer.log")

from core import db, history                               # noqa: E402
history.HISTORY_PATH = str(_SANDBOX / "history.db")
history.LEGACY_JSONL = str(_SANDBOX / "history.jsonl")

# A sandbox left over from an older run holds a *JSON Lines* file under a name
# the store now expects to be SQLite, and "file is not a database" would then
# fail the suite for a reason that has nothing to do with the code under test.
for _stale in (Path(history.HISTORY_PATH), Path(history.LEGACY_JSONL)):
    if _stale.exists():
        db.close(str(_stale))
        _stale.unlink(missing_ok=True)
for _suffix in ("-wal", "-shm"):
    Path(history.HISTORY_PATH + _suffix).unlink(missing_ok=True)


@pytest.fixture
def real_paths():
    """The directories the app would actually use, for the tests that check them."""
    return {"app_dir": REAL_APP_DIR, "legacy_dirs": REAL_LEGACY_DIRS}


@pytest.fixture(autouse=True)
def _no_real_data():
    """Fail loudly if something still points at the installed app's directory."""
    assert "ProgramData" not in applog.LOG_PATH
    assert "ProgramData" not in applog.USER_LOG_PATH
    assert str(_SANDBOX) in applog.log_path, applog.log_path
    assert "ProgramData" not in history.HISTORY_PATH
    yield


def pytest_report_header(config):
    return f"test data sandbox: {_SANDBOX}"
