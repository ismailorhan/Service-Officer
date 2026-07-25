@echo off
setlocal

REM ----------------------------------------------------------------------
REM Build ServiceOfficer with the requireAdministrator manifest baked in.
REM
REM One-dir, not one-file: a Qt app unpacked from a single exe on every launch
REM starts noticeably slower, and Inno Setup is packaging it anyway.
REM Output: dist\ServiceOfficer\ServiceOfficer.exe
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
"%PY%" -m pip install -r requirements.txt
"%PY%" -m pip install pyinstaller

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
    --uac-admin ^
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

REM Put version.py back whatever happened, so a build never leaves the tree dirty
REM and the next build doesn't stamp a stamp.
"%PY%" stamp_version.py --restore

if not "%BUILD_RESULT%"=="0" (
    echo [ERROR] PyInstaller build failed.
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
echo Build OK -^> dist\ServiceOfficer\ServiceOfficer.exe
endlocal
