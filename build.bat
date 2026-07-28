@echo off
setlocal

REM ----------------------------------------------------------------------
REM Build both halves of the product:
REM
REM   dist\ServiceOfficer\ServiceOfficer.exe        the tray application (the client)
REM   dist\ServiceOfficerHub\ServiceOfficerHub.exe  the Windows service (the hub)
REM
REM One-dir, not one-file: a Qt app unpacked from a single exe on every launch
REM starts noticeably slower, and Inno Setup is packaging it anyway.
REM
REM Only the hub asks for administrator. It is the half that talks to a service
REM manager; the tray application asks the hub, and a UAC prompt on every launch
REM was the most visible cost this app used to charge.
REM ----------------------------------------------------------------------

cd /d "%~dp0"

REM An explicit interpreter wins. This matters on machines where "python" is
REM only the Microsoft Store stub: `where python` finds it, so the checks below
REM pass and the build then fails with "Python was not found".
REM   set PYTHON=C:\Path\to\python.exe && build.bat
if defined PYTHON (
    set "PY=%PYTHON%"
) else (
    set PY=py
    where py >nul 2>nul
    if errorlevel 1 (
        set PY=python
        where python >nul 2>nul
        if errorlevel 1 (
            echo [ERROR] No Python found on PATH. Install Python 3.10+ first,
            echo         or set PYTHON to its full path.
            exit /b 1
        )
    )
)

"%PY%" -c "import sys; print('Using', sys.version.split()[0], 'at', sys.executable)"
if errorlevel 1 (
    echo [ERROR] %PY% is not a working Python. Set PYTHON to its full path.
    exit /b 1
)

echo.
echo === Installing build dependencies ===
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements-dev.txt

echo.
echo === Running tests ===
"%PY%" -m pytest tests -q
if errorlevel 1 (
    echo [ERROR] Tests failed — not building.
    exit /b 1
)

echo.
echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist ServiceOfficer.spec del ServiceOfficer.spec
if exist ServiceOfficerHub.spec del ServiceOfficerHub.spec

echo.
echo === Stamping the build ===
REM Baked in, because a frozen app has no git repo to ask which commit it is.
"%PY%" stamp_version.py
if errorlevel 1 (
    echo [ERROR] Could not stamp the version.
    exit /b 1
)

echo.
echo === Building ServiceOfficer.exe ===
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name ServiceOfficer ^
    --icon=icon.ico ^
    --add-data "icon.ico;." ^
    --hidden-import=win32timezone ^
    --exclude-module tkinter ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtNetwork ^
    --exclude-module PySide6.Qt3DCore ^
    app.py
set BUILD_RESULT=%errorlevel%

if not "%BUILD_RESULT%"=="0" (
    "%PY%" stamp_version.py --restore
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo === Building ServiceOfficerHub.exe (the service; no Qt in it at all) ===
REM --console: a Windows service exe, and the same file is the console tool that
REM installs it, pairs clients and prints the certificate fingerprint.
REM
REM PySide6 excluded outright rather than trimmed: core/engine.py is deliberately
REM Qt-free (there is a test that reads it and fails if PySide6 appears), and a
REM service on a server has no display to draw on. It is also 60 MB.
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --console ^
    --uac-admin ^
    --name ServiceOfficerHub ^
    --icon=icon.ico ^
    --add-data "core/hub_pages;core/hub_pages" ^
    --hidden-import=win32timezone ^
    --hidden-import=servicemanager ^
    --hidden-import=win32serviceutil ^
    --exclude-module tkinter ^
    --exclude-module PySide6 ^
    --exclude-module shiboken6 ^
    hub.py
set HUB_RESULT=%errorlevel%

REM Put version.py back whatever happened, so a build never leaves the tree dirty
REM and the next build doesn't stamp a stamp.
"%PY%" stamp_version.py --restore

if not "%HUB_RESULT%"=="0" (
    echo [ERROR] PyInstaller build of the hub failed.
    exit /b 1
)

echo.
echo === Pruning what a tray app never uses (101 MB -^> ~65 MB) ===
REM opengl32sw is the software OpenGL fallback; Qt Widgets renders with raster.
REM libcrypto/libssl come in with Qt's TLS support, and nothing here uses the
REM network. Verified afterwards by launching the built exe.
del /q "dist\ServiceOfficer\_internal\PySide6\opengl32sw.dll" 2>nul
del /q "dist\ServiceOfficer\_internal\PySide6\libcrypto*" 2>nul
del /q "dist\ServiceOfficer\_internal\PySide6\libssl*" 2>nul
del /q "dist\ServiceOfficer\_internal\PySide6\Qt6Network.dll" 2>nul

echo.
echo Build OK
echo   client: dist\ServiceOfficer\ServiceOfficer.exe
echo   hub:    dist\ServiceOfficerHub\ServiceOfficerHub.exe
endlocal
