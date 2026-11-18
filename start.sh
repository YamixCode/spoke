#!/bin/zsh
cd "$(dirname "$0")"
[ -f spoke/native/libSpokeUI.dylib ] || ./scripts/build_native.sh
exec .venv/bin/python -m spoke
