#!/bin/bash
# Builds ~/Applications/ytgist.app — a Dock icon that brings the whole thing up.
#
# NOT Electron or Tauri. Both would bundle a second browser engine (~100MB) to show a page
# the Mac can already show, and this is a personal tool, not something to ship. The .app is
# a launcher: it starts the two servers if they are down, waits for them, and opens a
# CHROMELESS browser window so there is no address bar to make it feel like a tab.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/ytgist.app"
[ -w /Applications ] || APP="$HOME/Applications/ytgist.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ytgist</string>
  <key>CFBundleDisplayName</key><string>ytgist</string>
  <key>CFBundleIdentifier</key><string>com.ytgist.launcher</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>ytgist</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/ytgist" <<LAUNCH
#!/bin/bash
REPO="$REPO"
LAUNCH
cat >> "$APP/Contents/MacOS/ytgist" <<'LAUNCH'
LOG="$HOME/Library/Logs/ytgist.log"
exec >>"$LOG" 2>&1
echo "--- launch $(date) ---"

up() { nc -z 127.0.0.1 "$1" >/dev/null 2>&1; }
wait_for() { for _ in $(seq 1 60); do up "$1" && return 0; sleep 0.5; done; return 1; }

# The ENGINE owns transcription and the model; the WEB server is just the interface.
# Started separately and only if down, so launching twice never doubles either one.
up 8765 || ( cd "$REPO" && ./serve & )

# Production build, not `next dev`: dev takes ~4s to first paint and recompiles on every
# navigation. Built once here if missing, then served.
if ! up 3210; then
  [ -d "$REPO/web/.next/BUILD_ID" ] || [ -f "$REPO/web/.next/BUILD_ID" ] || \
    ( cd "$REPO/web" && npm run build )
  ( cd "$REPO/web" && npm run start & )
fi

wait_for 8765 || { osascript -e 'display alert "ytgist" message "The engine did not start. See ~/Library/Logs/ytgist.log"'; exit 1; }
wait_for 3210 || { osascript -e 'display alert "ytgist" message "The web server did not start. See ~/Library/Logs/ytgist.log"'; exit 1; }

# --app= gives a window with no tabs and no address bar — the whole difference between
# "a tab I have to find" and "an app".
#
# Launched as a BINARY with its own --user-data-dir, not via `open -na`. When Chrome is
# already running, `open` hands the request to the existing instance and silently drops
# --app=, so you get an ordinary tab in the middle of your other tabs. A separate profile
# forces a genuinely separate instance, gives it its own Dock entry, and keeps this out of
# your real browser session entirely.
PROFILE="$HOME/Library/Application Support/ytgist-browser"
for B in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
         "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  if [ -x "$B" ]; then
    # arch -arm64 is NOT optional. Chrome's binary is universal, and launching it directly
    # rather than through LaunchServices let macOS start the x86_64 slice under Rosetta —
    # verified: our window reported LSArchitecture=x86_64 while the user's own Chrome
    # reported arm64, and macOS warned about ending Intel support (2026-08-08). `open`
    # picks the native slice for you; exec'ing the binary does not, so say it explicitly.
    arch -arm64 "$B" --app=http://127.0.0.1:3210 \
         --user-data-dir="$PROFILE" \
         --no-first-run --no-default-browser-check \
         --window-size=1180,900 >/dev/null 2>&1 &
    exit 0
  fi
done
open "http://127.0.0.1:3210"      # no Chromium-family browser — a normal tab still works
LAUNCH
chmod +x "$APP/Contents/MacOS/ytgist"

# The icon: reuse the site's look rather than shipping a design.
ICON_SRC="$REPO/web/app/icon.png"
if [ -f "$ICON_SRC" ]; then
  SET="$(mktemp -d)/icon.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/icon.icns"
fi

touch "$APP"
echo "built: $APP"
echo "drag it to the Dock, or run: open -a ytgist"
