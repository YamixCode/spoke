#!/bin/zsh
set -e
cd "$(dirname "$0")"
for source in spoke/native/*.swift; do
    if [[ "$source" -nt spoke/native/libSpokeUI.dylib || ! -f spoke/native/libSpokeUI.dylib ]]; then
        ./scripts/build_native.sh
        break
    fi
done
exec .venv/bin/python -m spoke
