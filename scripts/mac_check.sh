#!/bin/bash
# What a Mac does with the published Shortsmith download.
#
# Everything about the macOS bundle has been checked from a Windows machine:
# file hashes, permission bits, that pyobjc is present, that symlinks survived
# the zip. None of that answers the only question that matters -- whether a
# person who downloads it can open it -- and that question has three parts no
# amount of file inspection reaches:
#
#   1. Does macOS block the launcher, and if so, is there an "Open Anyway"
#      button for it? Apple documents that button for apps. Shortsmith ships a
#      .command, which is a script, and nothing I can find says whether scripts
#      get the same treatment. If they do not, a Mac user is stuck with no
#      button to click and the whole design needs rethinking.
#   2. Once past that, does the bundled Python actually run, or does macOS kill
#      it because the 591 compiled files beside it are still marked?
#   3. Does a window appear? pywebview borrows the system webview through
#      pyobjc. That is installed, which is not the same as working.
#
# Run this, then do the short manual part it prints at the end. Nothing here
# installs anything or touches your Mac outside one temporary folder.

set -u

TAG="${1:-v2.3.0}"
URL="https://github.com/Onrynx/ShortSmith/releases/download/$TAG/Shortsmith-macOS.zip"
WORK="$(mktemp -d /tmp/shortsmith-check.XXXXXX)"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
item() { printf '   %s\n' "$*"; }

say "Shortsmith macOS check  ($TAG)"
item "working in: $WORK"
item "macOS $(sw_vers -productVersion), $(uname -m)"

# --------------------------------------------------------------------------- #
say "1. Downloading the published bundle"
# --------------------------------------------------------------------------- #
if ! curl -fL --progress-bar -o "$WORK/Shortsmith-macOS.zip" "$URL"; then
    item "download failed -- is $TAG the right tag?"
    exit 1
fi
item "$(du -h "$WORK/Shortsmith-macOS.zip" | cut -f1) downloaded"

# --------------------------------------------------------------------------- #
say "2. Marking it the way a browser would"
# --------------------------------------------------------------------------- #
# curl does not set the quarantine flag; Safari and Chrome do. Without this the
# test is meaningless, because quarantine is the entire reason macOS objects.
# Setting it by hand is the worst case: every file marked, which is roughly
# what Archive Utility produces from a downloaded zip.
xattr -w com.apple.quarantine \
      "0081;$(printf '%x' "$(date +%s)");Safari;" \
      "$WORK/Shortsmith-macOS.zip"
item "quarantine set on the zip"

cd "$WORK" || exit 1
ditto -x -k "$WORK/Shortsmith-macOS.zip" "$WORK/unpacked" 2>/dev/null \
    || unzip -q "$WORK/Shortsmith-macOS.zip" -d "$WORK/unpacked"
APP="$WORK/unpacked/Shortsmith"
[ -d "$APP" ] || { item "expected $APP, not found"; exit 1; }

xattr -wr com.apple.quarantine \
      "0081;$(printf '%x' "$(date +%s)");Safari;" "$APP"
item "quarantine forced onto the unpacked folder (worst case)"

MARKED=$(find "$APP" -type f -print0 2>/dev/null \
         | xargs -0 xattr -p com.apple.quarantine 2>/dev/null | wc -l | tr -d ' ')
item "files now carrying the mark: $MARKED"

# --------------------------------------------------------------------------- #
say "3. Can the bundled Python run while everything is still marked?"
# --------------------------------------------------------------------------- #
# This is the question the launcher's own guard exists to answer. If it fails
# here, the self-clearing line in the launcher is doing real work.
if "$APP/runtime/bin/python3" -c "print('yes')" >/dev/null 2>&1; then
    item "RUNS even while quarantined -- macOS is not blocking the interpreter"
    PY_BLOCKED=no
else
    item "BLOCKED while quarantined -- expected, and what the launcher clears"
    PY_BLOCKED=yes
fi

# --------------------------------------------------------------------------- #
say "4. Does the launcher's self-clearing actually work?"
# --------------------------------------------------------------------------- #
# Run the launcher's own clearing line, exactly as the launcher would.
( cd "$APP" && xattr -dr com.apple.quarantine . 2>/dev/null || true )
LEFT=$(find "$APP" -type f -print0 2>/dev/null \
       | xargs -0 xattr -p com.apple.quarantine 2>/dev/null | wc -l | tr -d ' ')
item "files still marked afterwards: $LEFT  (want 0)"

if "$APP/runtime/bin/python3" -c "print('ok')" >/dev/null 2>&1; then
    item "the interpreter runs now"
else
    item "the interpreter STILL will not run -- this is the serious case"
fi

# --------------------------------------------------------------------------- #
say "5. Is the app itself intact?"
# --------------------------------------------------------------------------- #
PY="$APP/runtime/bin/python3"
( cd "$APP" && "$PY" -c "
import shortsmith, shortsmith.webapp, shortsmith.control
print('   app imports, v' + shortsmith.__version__)
import numpy, PIL, yaml, psutil, onnxruntime, kokoro_onnx, faster_whisper
print('   every dependency present')
import objc, Cocoa, WebKit
from importlib.metadata import version
print('   pyobjc ' + version('pyobjc-core') + ' imports -- the window layer is real')
from shortsmith.hardware import scan
h = scan()
print('   hardware scan: ' + h['os'] + ' ' + h['arch'])
" ) || item "something above failed -- copy the error back"

# --------------------------------------------------------------------------- #
say "6. Does a window actually open?"
# --------------------------------------------------------------------------- #
# Create the window, show it for three seconds, close it. If a Shortsmith
# window appears on screen, the whole GUI path works.
( cd "$APP" && "$PY" - <<'PYCODE'
import threading, time
try:
    import webview
except Exception as exc:
    print("   pywebview will not import:", exc)
    raise SystemExit

def close_soon():
    time.sleep(3)
    try:
        for w in webview.windows:
            w.destroy()
    except Exception:
        pass

try:
    webview.create_window("Shortsmith window test",
                          html="<h1 style='font:48px sans-serif'>It works</h1>")
    threading.Thread(target=close_soon, daemon=True).start()
    print("   opening a window for three seconds -- watch the screen")
    webview.start()
    print("   window opened and closed cleanly")
except Exception as exc:
    print("   window failed:", exc)
PYCODE
) || item "the window test crashed -- copy the error back"

# --------------------------------------------------------------------------- #
say "Now the part only a person can do"
# --------------------------------------------------------------------------- #
cat <<NOTE
   Everything above ran from a terminal, which macOS treats differently from
   a double-click in Finder. The real first-run experience is the Finder path,
   and that is the bit that has never been tested.

   1. Open this folder in Finder:
          open "$WORK/unpacked"
      (the quarantine mark was cleared in step 4, so first put it back:
          xattr -wr com.apple.quarantine "0081;0;Safari;" "$APP" )

   2. Double-click 'Shortsmith.command'.

   3. Write down EXACTLY what macOS says. The wording matters:
        - "cannot be opened because it is from an unidentified developer"
        - "Apple could not verify ... free of malware"
        - "is damaged and can't be opened"   <- this one is bad news
        - or it simply opens

   4. Then open System Settings > Privacy & Security, scroll to Security,
      and say whether an 'Open Anyway' button is there for Shortsmith.
      THIS IS THE ANSWER I NEED. If that button exists for a .command file,
      the current design is finished. If it does not, the bundle has to become
      a real .app and that is a different piece of work.

   5. If you click it and Shortsmith opens: say whether the window appeared,
      and whether it asked you about anything else afterwards.

   Clean up when done:   rm -rf "$WORK"
NOTE

say "Summary"
item "python blocked while quarantined: $PY_BLOCKED"
item "files left marked after clearing: $LEFT"
item "folder kept at: $WORK"
