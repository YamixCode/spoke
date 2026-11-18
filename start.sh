#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -f spoke/native/libSpokeUI.dylib || spoke/native/SettingsUI.swift -nt spoke/native/libSpokeUI.dylib ]]; then
    ./scripts/build_native.sh
fi
exec .venv/bin/python -m spoke
