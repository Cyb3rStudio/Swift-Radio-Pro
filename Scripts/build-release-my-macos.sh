#!/bin/bash
#
# build-release-my-macos.sh
#
# Builds a Release version of SwiftRadio as a native Mac app using
# Mac Catalyst, installs it into /Applications, and launches it.
#
# Usage:
#   ./Scripts/build-release-my-macos.sh               # build, install, launch
#   ./Scripts/build-release-my-macos.sh --build-only  # build only
#
# Requirements:
#   - Xcode with the SwiftRadio development team signed in
#   - Requires SUPPORTS_MACCATALYST = YES on the SwiftRadio target
#     (already set in SwiftRadio.xcodeproj/project.pbxproj)
#
set -euo pipefail

# --- Project configuration ----------------------------------------------------
readonly PROJECT="SwiftRadio.xcodeproj"
readonly SCHEME="SwiftRadio"
readonly CONFIGURATION="Release"
readonly BUNDLE_ID="com.cyb3rstudio.radiomy"
readonly APP_NAME="Radio My.app"

# --- Destination ----------------------------------------------------------------
# My Mac, built with the Mac Catalyst variant so the result is a real macOS
# app that LaunchServices can open (unlike "Designed for iPad" builds, which
# only run when launched from Xcode).
readonly DEVICE_NAME="My Mac"
readonly DEVICE_SLUG="macos"
readonly DESTINATION="platform=macOS,variant=Mac Catalyst"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--build-only]

Builds the Release configuration of SwiftRadio for $DEVICE_NAME
(Mac Catalyst), installs it into /Applications, and launches it.

Options:
  --build-only   Only build; skip the install and launch steps.
  -h, --help     Show this help.
EOF
}

# --- Options --------------------------------------------------------------------
BUILD_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --build-only) BUILD_ONLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "error: unknown option '$arg'" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# --- Paths ----------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

readonly BUILD_DIR="build"
# A per-device DerivedData folder keeps incremental build state consistent for
# each destination (mixing the "My Mac" and iOS destinations in one folder can
# leave a stale, invalidly-sealed .app behind).
readonly DERIVED_DATA="$BUILD_DIR/DerivedData-$DEVICE_SLUG"
readonly LOG_FILE="$BUILD_DIR/build-release-$DEVICE_SLUG.log"
mkdir -p "$BUILD_DIR"

# --- Build ------------------------------------------------------------------------
echo "==> Building SwiftRadio ($CONFIGURATION) for $DEVICE_NAME"
xcodebuild \
    -project "$PROJECT" \
    -scheme "$SCHEME" \
    -configuration "$CONFIGURATION" \
    -destination "$DESTINATION" \
    -derivedDataPath "$DERIVED_DATA" \
    -allowProvisioningUpdates \
    build 2>&1 | tee "$LOG_FILE"

# --- Locate the built app -----------------------------------------------------------
# Newer Xcode layouts place products in Products/<Configuration>-<platform>
# (e.g. Release-iphoneos); older ones use Products/<Configuration>/<platform>.
PRODUCTS_ROOT="$DERIVED_DATA/Build/Products"
APP_PATH=""
for candidate in \
    "$PRODUCTS_ROOT/$CONFIGURATION-maccatalyst/$APP_NAME" \
    "$PRODUCTS_ROOT/$CONFIGURATION-iphoneos/$APP_NAME" \
    "$PRODUCTS_ROOT/$CONFIGURATION/iphoneos/$APP_NAME" \
    "$PRODUCTS_ROOT/$CONFIGURATION"-*/"$APP_NAME" \
    "$PRODUCTS_ROOT/$CONFIGURATION"/*/"$APP_NAME"
do
    [[ -d "$candidate" ]] || continue
    APP_PATH="$candidate"
    break
done
if [[ -z "${APP_PATH:-}" ]]; then
    echo "error: $APP_NAME was not found in $PRODUCTS_ROOT" >&2
    exit 1
fi
echo "==> Built: $APP_PATH"

if (( BUILD_ONLY )); then
    echo "==> --build-only given, skipping install and launch"
    exit 0
fi

# --- Install -----------------------------------------------------------------------
INSTALL_DIR="/Applications"
if [[ ! -w "$INSTALL_DIR" ]]; then
    INSTALL_DIR="$HOME/Applications"
    mkdir -p "$INSTALL_DIR"
fi
INSTALL_PATH="$INSTALL_DIR/$APP_NAME"

echo "==> Installing to $INSTALL_PATH"
if [[ -d "$INSTALL_PATH" ]]; then
    rm -rf "$INSTALL_PATH"
fi
ditto "$APP_PATH" "$INSTALL_PATH"   # ditto preserves code signature and metadata

echo "==> Launching SwiftRadio"
open "$INSTALL_PATH"

echo "==> Done (log: $LOG_FILE)"
