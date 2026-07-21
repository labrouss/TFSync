@echo off
REM ============================================================
REM  build.bat - TFSync (Total File Sync)
REM               builds all executables with PyInstaller, then
REM               the Windows installer with Inno Setup if present.
REM
REM  Run this from the folder containing:
REM     acl_compare_core.py, compare_acls.py, tfsync_gui.py,
REM     robocopy_sync.py, sync_shares.py, decode_mask.py
REM
REM  Requires: pip install pywin32 PyQt5 pyinstaller
REM ============================================================

echo.
echo === Cleaning previous build output ===
rmdir /S /Q build 2>nul
rmdir /S /Q dist 2>nul
del /Q *.spec 2>nul

echo.
echo === Building CLI: compare_acls.exe ===
pyinstaller --onefile --console --name compare_acls ^
    --hidden-import=win32timezone ^
    --hidden-import=win32security ^
    --hidden-import=win32process ^
    --hidden-import=ntsecuritycon ^
    compare_acls.py

if errorlevel 1 (
    echo.
    echo *** CLI build FAILED - see errors above ***
    goto :end
)

echo.
echo === Building GUI: tfsync_gui.exe ===
REM NOTE: using --console here on purpose for the first build so any
REM startup errors are visible. Once you've confirmed it runs cleanly,
REM re-run with --windowed instead (edit the line below) for a
REM console-free GUI app.
pyinstaller --onefile --console --name tfsync_gui ^
    --hidden-import=win32timezone ^
    --hidden-import=win32security ^
    --hidden-import=win32process ^
    --hidden-import=ntsecuritycon ^
    --hidden-import=PyQt5.sip ^
    tfsync_gui.py

if errorlevel 1 (
    echo.
    echo *** GUI build FAILED - see errors above ***
    goto :end
)

echo.
echo === Building sync_shares.exe ===
pyinstaller --onefile --console --name sync_shares ^
    --hidden-import=win32timezone ^
    --hidden-import=win32security ^
    --hidden-import=win32process ^
    --hidden-import=ntsecuritycon ^
    sync_shares.py

if errorlevel 1 (
    echo.
    echo *** sync_shares build FAILED - see errors above ***
    goto :end
)

echo.
echo === Building decode_mask.exe ===
pyinstaller --onefile --console --name decode_mask decode_mask.py

if errorlevel 1 (
    echo.
    echo *** decode_mask build FAILED - see errors above ***
    goto :end
)

echo.
echo === Done ===
echo   CLI exe:          dist\compare_acls.exe
echo   GUI exe:          dist\tfsync_gui.exe
echo   Sync exe:         dist\sync_shares.exe
echo   Mask decoder exe: dist\decode_mask.exe

set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
    echo.
    echo === Building installer with Inno Setup ===
    %ISCC% installer\compare_acls_installer.iss /DMyAppVersion=0.0.0-local
    if errorlevel 1 (
        echo *** Installer build FAILED - see errors above ***
    ) else (
        echo   Installer: installer\installer_output\TFSync_Setup.exe
    )
) else (
    echo.
    echo Inno Setup not found at %ISCC% - skipping installer build.
    echo Install it from https://jrsoftware.org/isdl.php to also build a
    echo proper Windows installer, or rely on the GitHub Actions workflow,
    echo which has it preinstalled.
)

echo.
echo Once you've confirmed tfsync_gui.exe runs cleanly, you can
echo rebuild it with --windowed instead of --console to drop the
echo console window (see comment above).

:end
pause
