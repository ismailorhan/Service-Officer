@echo off
REM ----------------------------------------------------------------------
REM Run from source, no build. A build takes about ninety seconds; this takes
REM two, and it is the same code — only the version reads "running from source".
REM
REM Must be elevated: controlling services needs administrator rights, and the
REM data directory under %ProgramData% only lets administrators write. Without
REM that you get a panel that lists services and fails to start them, and a
REM history that silently records nothing.
REM
REM   run.bat            the app
REM   run.bat -t         the tests
REM   run.bat -p         the panel on its own, no tray (quick UI work)
REM
REM Note on batch: no `exit /b %errorlevel%` inside a parenthesised block. The
REM whole block is parsed at once, so it would expand to whatever errorlevel was
REM *before* the command ran — which is how this script first reported failure
REM after 165 passing tests.
REM ----------------------------------------------------------------------

cd /d "%~dp0"

if defined PYTHON goto :have_python
set PY=py
where py >nul 2>nul || set PY=python
goto :check_admin
:have_python
set "PY=%PYTHON%"

:check_admin
REM "net session" fails for a standard token, which is the cheapest elevation test.
net session >nul 2>nul
if not errorlevel 1 goto :dispatch
echo [WARNING] Not running as administrator.
echo           Starting and stopping services will fail, and the history under
echo           %%ProgramData%% cannot be written.
echo.

:dispatch
if "%~1"=="-t" goto :tests
if "%~1"=="-p" goto :panel
echo Running from source. Quit from the tray icon to stop.
"%PY%" app.py
exit /b %errorlevel%

:tests
"%PY%" -m pytest tests -q
exit /b %errorlevel%

:panel
REM Just the panel, so a UI change can be looked at without the tray, the
REM watchdog, the scheduler or the health monitor running.
"%PY%" -c "import sys; from PySide6.QtWidgets import QApplication; from core import config, connectors, state; from ui import panel, theme; a=QApplication(sys.argv); cfg=config.load(); connectors.use_config(lambda: cfg); theme.set_mode(cfg.theme); a.setStyleSheet(theme.sheet()); w=panel.MainPanel(cfg, store=state.store); w.resize(1060,700); w.show(); sys.exit(a.exec())"
exit /b %errorlevel%
