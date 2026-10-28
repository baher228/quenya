#!/usr/bin/env bash
set -euo pipefail

: "${ASC_KEY_PATH:?ASC_KEY_PATH is required (path to the App Store Connect private key)}"
: "${ASC_KEY_ID:?ASC_KEY_ID is required (App Store Connect key ID)}"
: "${ASC_ISSUER_ID:?ASC_ISSUER_ID is required (App Store Connect issuer ID)}"

BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -x "$HOME/ios-work/venv/bin/python" ]]; then
  PYTHON="$HOME/ios-work/venv/bin/python"
else
  PYTHON=python3
fi

if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate
fi

"$PYTHON" scripts/signing_setup.py

KEYCHAIN="$("$PYTHON" -c 'import os; from pathlib import Path; print((Path(os.environ.get("QUENYA_SIGNING_DIR", "~/ios-work/signing")).expanduser().resolve() / "quenya-signing.keychain-db"))')"

xcodebuild archive \
  -project Quenya.xcodeproj \
  -scheme Quenya \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/Quenya.xcarchive \
  DEVELOPMENT_TEAM=CJ74KDAF7R \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="Apple Distribution" \
  PROVISIONING_PROFILE_SPECIFIER="Quenya App Store" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  OTHER_CODE_SIGN_FLAGS="--keychain $KEYCHAIN"

xcodebuild -exportArchive \
  -archivePath build/Quenya.xcarchive \
  -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export \
  -allowProvisioningUpdates \
  -authenticationKeyPath "$ASC_KEY_PATH" \
  -authenticationKeyID "$ASC_KEY_ID" \
  -authenticationKeyIssuerID "$ASC_ISSUER_ID"

echo "BUILD_NUMBER=$BUILD_NUMBER"
