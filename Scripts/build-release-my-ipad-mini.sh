#!/bin/bash
#
# build-release-my-ipad-mini.sh
#
# Builds a Release version of SwiftRadio for Serafim Junior's iPad mini,
# installs it on the device, and launches it.
#
# Usage:
#   ./Scripts/build-release-my-ipad-mini.sh               # build, install, launch
#   ./Scripts/build-release-my-ipad-mini.sh --build-only  # build only
#
# Requirements:
#   - The iPad must be connected, trusted, and unlocked
#   - Xcode with the SwiftRadio development team signed in
#
set -euo pipefail

# --- Project configuration ----------------------------------------------------
readonly PROJECT="SwiftRadio.xcodeproj"
readonly SCHEME="SwiftRadio"
readonly CONFIGURATION="Release"
readonly BUNDLE_ID="com.cyb3rstudio.radiomy"
readonly APP_NAME="SwiftRadio.app"

# --- Destination ----------------------------------------------------------------
# Serafim Junior's iPad mini. Verify the identifier with:
#   xcodebuild -showdestinations -scheme SwiftRadio -project SwiftRadio.xcodeproj
# or: xcrun devicectl list devices
readonly DEVICE_NAME="Serafim Junior's iPad mini"
readonly DEVICE_SLUG="ipad-mini"
readonly DEVICE_UDID="00008130-000C24A618EB8D3A"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--build-only]

Builds the Release configuration of SwiftRadio and installs and launches
it on $DEVICE_NAME.

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
# each destination (mixing destinations in one folder can leave a stale,
# invalidly-sealed .app behind).
readonly DERIVED_DATA="$BUILD_DIR/DerivedData-$DEVICE_SLUG"
readonly LOG_FILE="$BUILD_DIR/build-release-$DEVICE_SLUG.log"
mkdir -p "$BUILD_DIR"

# --- Build ------------------------------------------------------------------------
echo "==> Building SwiftRadio ($CONFIGURATION) for $DEVICE_NAME"
xcodebuild \
    -project "$PROJECT" \
    -scheme "$SCHEME" \
    -configuration "$CONFIGURATION" \
    -destination "id=$DEVICE_UDID" \
    -derivedDataPath "$DERIVED_DATA" \
    -allowProvisioningUpdates \
    build 2>&1 | tee "$LOG_FILE"

# --- Locate the built app -----------------------------------------------------------
# Newer Xcode layouts place products in Products/<Configuration>-<platform>
# (e.g. Release-iphoneos); older ones use Products/<Configuration>/<platform>.
PRODUCTS_ROOT="$DERIVED_DATA/Build/Products"
APP_PATH=""
for candidate in \
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

# --- Install on the device -----------------------------------------------------------
echo "==> Installing on $DEVICE_NAME (the device must be unlocked)"
xcrun devicectl device install app --device "$DEVICE_UDID" "$APP_PATH"

# --- Launch on the device -------------------------------------------------------------
echo "==> Launching $BUNDLE_ID on $DEVICE_NAME"
xcrun devicectl device process launch --device "$DEVICE_UDID" "$BUNDLE_ID"

echo "==> Done (log: $LOG_FILE)"
