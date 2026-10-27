#!/usr/bin/env bash
set -euo pipefail

: "${ASC_KEY_PATH:?ASC_KEY_PATH is required (path to the App Store Connect private key)}"
: "${ASC_KEY_ID:?ASC_KEY_ID is required (App Store Connect key ID)}"
: "${ASC_ISSUER_ID:?ASC_ISSUER_ID is required (App Store Connect issuer ID)}"

BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate
fi

xcodebuild archive \
  -project Quenya.xcodeproj \
  -scheme Quenya \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/Quenya.xcarchive \
  DEVELOPMENT_TEAM=CJ74KDAF7R \
  CODE_SIGN_STYLE=Automatic \
  CODE_SIGN_IDENTITY="Apple Distribution" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  -allowProvisioningUpdates \
  -authenticationKeyPath "$ASC_KEY_PATH" \
  -authenticationKeyID "$ASC_KEY_ID" \
  -authenticationKeyIssuerID "$ASC_ISSUER_ID"

xcodebuild -exportArchive \
  -archivePath build/Quenya.xcarchive \
  -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export \
  -allowProvisioningUpdates \
  -authenticationKeyPath "$ASC_KEY_PATH" \
  -authenticationKeyID "$ASC_KEY_ID" \
  -authenticationKeyIssuerID "$ASC_ISSUER_ID"

echo "BUILD_NUMBER=$BUILD_NUMBER"
