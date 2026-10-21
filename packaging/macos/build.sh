#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/klean/__init__.py")"
PYTHON="${PYTHON:-python3}"
BUILD="${BUILD_DIR:-$(mktemp -d /tmp/klean-build.XXXXXX)}"
SRC="$BUILD/src"
DIST="$BUILD/dist"
APP="$DIST/Klean.app"
OUT="$ROOT/dist"

echo "==> klean $VERSION"
echo "==> build dir: $BUILD"
mkdir -p "$OUT" "$SRC"

rsync -a \
  --exclude '.git' --exclude '.venv' --exclude 'reports' --exclude 'dist' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude '*.egg-info' \
  "$ROOT/" "$SRC/"

# ---- adb ---------------------------------------------------------------
ADB="${ADB:-$(command -v adb || true)}"
if [ -z "${ADB}" ] || [ ! -f "${ADB}" ]; then
  echo "==> downloading Android platform-tools"
  curl -L --fail --retry 3 -o "$BUILD/platform-tools.zip" \
    https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
  unzip -q "$BUILD/platform-tools.zip" -d "$BUILD/platform-tools"
  ADB="$BUILD/platform-tools/platform-tools/adb"
fi

# ---- ffmpeg ------------------------------------------------------------
FFMPEG="${FFMPEG:-$(command -v ffmpeg || true)}"
FFPROBE="${FFPROBE:-$(command -v ffprobe || true)}"
if [ -z "${FFMPEG}" ] || [ -z "${FFPROBE}" ] || [ ! -f "${FFMPEG}" ] || [ ! -f "${FFPROBE}" ]; then
  echo "==> downloading static ffmpeg"
  curl -L --fail --retry 3 -o "$BUILD/ffmpeg.zip" \
    https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin_arm64.zip
  mkdir -p "$BUILD/ffmpeg"
  unzip -q "$BUILD/ffmpeg.zip" -d "$BUILD/ffmpeg"
  FFMPEG="$(find "$BUILD/ffmpeg" -type f -name ffmpeg | head -1)"
  FFPROBE="$(find "$BUILD/ffmpeg" -type f -name ffprobe | head -1)"
fi
for bin in "$ADB" "$FFMPEG" "$FFPROBE"; do
  file "$bin" | grep -q arm64 || {
    echo "error: $bin is not an arm64 macOS binary" >&2
    exit 1
  }
done

# ---- Python runtime ----------------------------------------------------
if [ ! -x "$BUILD/venv/bin/python" ]; then
  "$PYTHON" -m venv "$BUILD/venv"
fi
"$BUILD/venv/bin/pip" install -q --upgrade pip
"$BUILD/venv/bin/pip" install -q -e "$SRC" pyinstaller pywebview

echo "==> building app bundle"
"$BUILD/venv/bin/pyinstaller" \
  --noconfirm --clean --windowed \
  --name Klean \
  --distpath "$DIST" \
  --workpath "$BUILD/work" \
  --specpath "$BUILD/spec" \
  --icon "$SRC/packaging/macos/AppIcon.icns" \
  --osx-bundle-identifier com.kkkkahn.klean \
  --add-binary "$ADB:bin" \
  --add-binary "$FFMPEG:bin" \
  --add-binary "$FFPROBE:bin" \
  --add-data "$SRC/src/klean/web:klean/web" \
  --hidden-import webview.platforms.cocoa \
  --exclude-module pytest --exclude-module ruff --exclude-module tkinter \
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 \
  --exclude-module gi \
  "$SRC/packaging/mac_app.py" >/dev/null

# ---- bundle metadata ---------------------------------------------------
PLIST="$APP/Contents/Info.plist"
plist_set_string() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" >/dev/null 2>&1 ||
    /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST" >/dev/null
}
plist_set_bool() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" >/dev/null 2>&1 ||
    /usr/libexec/PlistBuddy -c "Add :$1 bool $2" "$PLIST" >/dev/null
}
plist_set_string CFBundleShortVersionString "$VERSION"
plist_set_string CFBundleVersion "$(date +%Y%m%d%H%M)"
plist_set_string CFBundleDisplayName "Klean"
plist_set_string LSMinimumSystemVersion "12.0"
plist_set_string LSApplicationCategoryType "public.app-category.utilities"
plist_set_string NSHumanReadableCopyright "MIT License · Klean"
plist_set_bool NSHighResolutionCapable true
plist_set_bool LSMultipleInstancesProhibited true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST" >/dev/null 2>&1 || true

# ---- signing & checks --------------------------------------------------
echo "==> ad-hoc signing"
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null
codesign --verify --deep --strict "$APP"

echo "==> privacy checks"
if grep -R -I -q -E 'IMG_2026(09|10)|V2405A|X200 Pro|Telegram|ghp_[A-Za-z0-9]+|/Users/kahn' "$APP"; then
  echo "error: personal data found in app bundle" >&2
  exit 1
fi
if find "$APP" \( -name 'scan-*.json.gz' -o -name 'history.json' -o -name 'manifest.tsv' \) | grep -q .; then
  echo "error: user data found in app bundle" >&2
  exit 1
fi

# ---- disk image --------------------------------------------------------
DMG="$OUT/Klean-$VERSION-macos-arm64.dmg"
echo "==> creating $DMG"
rm -f "$DMG" "$DMG.sha256"
rm -rf "$BUILD/dmg"
mkdir -p "$BUILD/dmg"
cp -R "$APP" "$BUILD/dmg/"
ln -s /Applications "$BUILD/dmg/Applications"
hdiutil create -volname "Klean" -srcfolder "$BUILD/dmg" -ov -format UDZO "$DMG" >/dev/null
shasum -a 256 "$DMG" | tee "$DMG.sha256"

echo
echo "app: $APP"
echo "dmg: $DMG"
du -sh "$DMG"
