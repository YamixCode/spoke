#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
SITE=$($PY -c "import site;print(site.getsitepackages()[0])")
rm -rf build dist
$PY setup.py py2app > packaging/build.log 2>&1
APP=dist/Spoke.app
LIB=$APP/Contents/Resources/lib/python3.12
rm -rf $LIB/mlx
rsync -a --exclude '__pycache__' $SITE/mlx/ $LIB/mlx/
codesign --force --deep --sign - $APP
echo "Собрано: $APP ($(du -sh $APP | cut -f1))"
STAGE=$(mktemp -d)
cp -R $APP $STAGE/
ln -s /Applications $STAGE/Applications
rm -f dist/Spoke.dmg
hdiutil create -volname Spoke -srcfolder $STAGE -ov -format UDZO -imagekey zlib-level=1 dist/Spoke.dmg > /dev/null
rm -rf $STAGE
echo "DMG: dist/Spoke.dmg ($(du -sh dist/Spoke.dmg | cut -f1))"
