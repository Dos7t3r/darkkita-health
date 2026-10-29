#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
mkdir -p "$DIST_DIR"
xcodebuild -project "$ROOT/ios/DarkkitaHealth.xcodeproj" -scheme DarkkitaHealth \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath "$BUILD_DIR/device" CODE_SIGNING_ALLOWED=NO build
xcrun strip -S -x "$BUILD_DIR/device/Build/Products/Release-iphoneos/DarkkitaHealth.app/DarkkitaHealth"
python3 "$ROOT/scripts/package.py" device "$BUILD_DIR/device/Build/Products/Release-iphoneos/DarkkitaHealth.app" "$DIST_DIR"
xcodebuild -project "$ROOT/ios/DarkkitaHealth.xcodeproj" -scheme DarkkitaHealth \
  -configuration Release -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "$BUILD_DIR/simulator" CODE_SIGNING_ALLOWED=NO build
xcrun strip -S -x "$BUILD_DIR/simulator/Build/Products/Release-iphonesimulator/DarkkitaHealth.app/DarkkitaHealth"
python3 "$ROOT/scripts/package.py" simulator "$BUILD_DIR/simulator/Build/Products/Release-iphonesimulator/DarkkitaHealth.app" "$DIST_DIR"
