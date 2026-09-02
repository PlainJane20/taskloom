#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")
APP_DIR="$PROJECT_DIR/src-tauri/target/release/bundle/macos"
APP_PATH="$APP_DIR/Taskloom.app"
EXECUTABLE_PATH="$APP_PATH/Contents/MacOS/taskloom"
VERSION=$(node -p "require('$PROJECT_DIR/package.json').version")
OUTPUT_PATH=${1:-"/private/tmp/Taskloom-v${VERSION}-macOS-arm64.zip"}

cd "$PROJECT_DIR"
npm run tauri build -- --bundles app

if [ ! -d "$APP_PATH" ]; then
  echo "Taskloom bundle was not created at $APP_PATH" >&2
  exit 1
fi

if [ ! -x "$EXECUTABLE_PATH" ]; then
  echo "Taskloom executable was not created at $EXECUTABLE_PATH" >&2
  exit 1
fi

# Tauri's local linker signature covers only the executable. Seal the complete
# application after resources have been bundled so macOS can validate it.
# macOS 26.5+ requires 4 KiB code-signing pages for locally built Tauri apps.
# Remove build-host metadata, then sign the nested executable before the outer
# bundle so Launch Services sees one clean, consistently sealed application.
xattr -cr "$APP_PATH"
codesign --force --sign - --timestamp=none --options runtime --pagesize=4096 "$EXECUTABLE_PATH"
codesign --force --deep --sign - --timestamp=none --options runtime --pagesize=4096 "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

rm -f "$OUTPUT_PATH"
(
  cd "$APP_DIR"
  COPYFILE_DISABLE=1 zip -qry "$OUTPUT_PATH" Taskloom.app -x '*.DS_Store'
)

unzip -tq "$OUTPUT_PATH"
echo "Created $OUTPUT_PATH"
