@echo off
title Shortsmith setup
cd /d "%~dp0"
echo.
echo   Setting up Shortsmith. This takes a few minutes.
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo   [!] Python was not found.
  echo       Install it from https://www.python.org/downloads/
  echo       and TICK "Add python.exe to PATH" on the first screen.
  echo.
  pause
  exit /b 1
)
echo   [1/3] Installing what Shortsmith needs...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet pywebview
echo   [2/3] Making the app icon...
python scripts\make_icon.py
echo   [3/3] Putting Shortsmith on your Desktop...
python scripts\make_shortcut.py
echo.
echo   Done. Double-click the Shortsmith icon on your Desktop.
echo   Open Settings the first time to choose and download a model.
echo.
pause
