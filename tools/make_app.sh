#!/bin/bash
# Build Highvisor.app — a stable TCC identity for the daemon.
#
# WHY (short version; the long one is in tools/app/main.c): macOS attributes Screen Recording to
# a "responsible process". A daemon started from a terminal inherits the terminal's grant; the
# same daemon started by launchd does not, and then every window title reads blank and every
# capture comes back empty. Granting `.venv/bin/python` would work but dies with the next venv
# rebuild and hands the permission to every script that interpreter runs. A signed bundle is one
# thing to grant, once.
#
#   tools/make_app.sh          # -> build/Highvisor.app
#   hv install-daemon          # runs this for you and points launchd at the result
#
# GRANT IT AFTERWARDS: System Settings > Privacy & Security > Screen Recording > enable
# "Highvisor". The bundle is AD-HOC signed, so rebuilding it changes the code hash and macOS may
# ask again — which is exactly why the stub is a dozen lines that never need to change. Editing
# Python does NOT require a rebuild.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$REPO/build/Highvisor.app"
PY="$REPO/.venv/bin/python"
BUNDLE_ID="com.dazzlingdukeoflazers.highvisor"

if [ ! -x "$PY" ]; then
  echo "no venv interpreter at $PY" >&2
  echo "create it first:  /usr/bin/python3 -m venv $REPO/.venv && $REPO/.venv/bin/python -m pip install -e $REPO" >&2
  exit 2
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>Highvisor</string>
  <key>CFBundleDisplayName</key>     <string>Highvisor</string>
  <key>CFBundleIdentifier</key>      <string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key>      <string>Highvisor</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>12.0</string>
  <!-- Agent app: no Dock icon, no menu bar. It is a background daemon that happens to need a
       bundle identity, not something anyone opens. -->
  <key>LSUIElement</key>             <true/>
  <!-- Shown in the permission prompt, so say what it is FOR. -->
  <key>NSCameraUsageDescription</key><string>Highvisor does not use the camera.</string>
</dict>
</plist>
PLIST

# The stub is compiled, not a shell script, and that is deliberate: a script's shebang means the
# kernel executes /bin/sh, and TCC would have /bin/sh to attribute to instead of this bundle.
clang -O2 -Wall -Wextra -o "$APP/Contents/MacOS/Highvisor" \
      -DHV_PYTHON="\"$PY\"" -DHV_REPO="\"$REPO\"" \
      "$REPO/tools/app/main.c"

# Ad-hoc sign with a PINNED identifier. Without --identifier the ad-hoc identity is derived from
# the binary name and the designated requirement can shift under you; pinning it keeps the
# identity macOS remembers stable across rebuilds of everything except the stub itself.
codesign --force --sign - --identifier "$BUNDLE_ID" "$APP" >/dev/null 2>&1
codesign --verify --deep --strict "$APP" 2>&1 | sed 's/^/  /' || true

echo "built: $APP"
echo "  interpreter: $PY"
echo "  bundle id:   $BUNDLE_ID"
echo
echo "Grant it Screen Recording once:"
echo "  System Settings > Privacy & Security > Screen Recording > enable \"Highvisor\""
echo "(without it, window titles read blank and every capture/OCR returns nothing)"
