#!/bin/bash
cd "$(dirname "$0")"
echo
echo "  Setting up StoryForge. This takes a few minutes."
echo
if ! command -v python3 >/dev/null 2>&1; then
  echo "  [!] Python was not found."
  echo "      Install it from https://www.python.org/downloads/ and run this again."
  echo
  read -n 1 -s -r -p "  Press any key to close."
  exit 1
fi
echo "  [1/3] Installing what StoryForge needs..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt
python3 -m pip install --quiet pywebview
echo "  [2/3] Making the app icon..."
python3 scripts/make_icon.py
echo "  [3/3] Putting StoryForge on your Desktop..."
python3 scripts/make_shortcut.py
echo
echo "  Done. Double-click the StoryForge icon on your Desktop."
echo "  Open Settings the first time to choose and download a model."
echo
read -n 1 -s -r -p "  Press any key to close."
