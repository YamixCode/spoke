#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."

PY=.venv/bin/python
OUTPUT=${1:-dist}
IDENTITY=${SPOKE_DEVELOPER_ID:-}
NOTARY_PROFILE=${SPOKE_NOTARY_PROFILE:-}
if [[ -n "$NOTARY_PROFILE" && -z "$IDENTITY" ]]; then
  print -u2 'Для нотарификации укажите SPOKE_DEVELOPER_ID.'
  exit 1
fi
if [[ -n "$IDENTITY" ]] && ! security find-identity -v -p codesigning | grep -F 'Developer ID Application:' | grep -Fq -- "$IDENTITY"; then
  print -u2 'В связке ключей нет указанного сертификата Developer ID Application с закрытым ключом.'
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
./scripts/build_native.sh
$PY setup.py py2app --bdist-base "$WORK/build" --dist-dir "$WORK/dist" > packaging/build.log 2>&1
APP="$WORK/dist/Spoke.app"
mkdir -p "$APP/Contents/Resources/native"
cp spoke/native/libSpokeUI.dylib "$APP/Contents/Resources/native/"
if [[ -n "$IDENTITY" ]]; then
  /usr/libexec/PlistBuddy -c 'Add :SpokeSignedDistribution bool true' "$APP/Contents/Info.plist"
  $PY packaging/sign_app.py "$APP" "$IDENTITY" packaging/distribution.entitlements
else
  codesign --force --deep --sign - "$APP"
fi

mkdir "$WORK/image"
ditto "$APP" "$WORK/image/Spoke.app"
ln -s /Applications "$WORK/image/Applications"
DMG="$WORK/Spoke.dmg"
hdiutil create -volname Spoke -srcfolder "$WORK/image" -format UDZO -imagekey zlib-level=1 "$DMG" > /dev/null
if [[ -n "$IDENTITY" ]]; then
  codesign --sign "$IDENTITY" --timestamp "$DMG"
fi
if [[ -n "$NOTARY_PROFILE" ]]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose "$DMG"
fi

mkdir -p "$OUTPUT"
rm -rf "$OUTPUT/Spoke.app"
ditto "$APP" "$OUTPUT/Spoke.app"
mv -f "$DMG" "$OUTPUT/Spoke.dmg"
echo "Собрано: $OUTPUT/Spoke.app и $OUTPUT/Spoke.dmg"
