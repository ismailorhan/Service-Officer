@echo off
setlocal

REM ----------------------------------------------------------------------
REM Build ServiceOfficer.exe with the requireAdministrator manifest baked in.
REM Output goes to .\dist\ServiceOfficer.exe and is also copied to the
REM project root so the Inno Setup script can pick it up.
REM ----------------------------------------------------------------------

cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher 'py' not found. Install Python 3.10+ first.
    exit /b 1
)

echo.
echo === Installing build dependencies ===
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install pyinstaller

echo.
echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist ServiceOfficer.spec del ServiceOfficer.spec

echo.
echo === Building ServiceOfficer.exe ===
py -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --uac-admin ^
    --name ServiceOfficer ^
    --icon=icon.ico ^
    --hidden-import=win32com ^
    --hidden-import=win32com.client ^
    --hidden-import=pywintypes ^
    service_officer.py

if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo === Copying exe to project root ===
copy /y dist\ServiceOfficer.exe ServiceOfficer.exe >nul

echo.
echo Build OK -^> ServiceOfficer.exe
endlocal
