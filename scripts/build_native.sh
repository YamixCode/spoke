#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
swiftc -emit-library -o spoke/native/libSpokeUI.dylib -module-name SpokeUI -parse-as-library -swift-version 5 -target arm64-apple-macos13.0 spoke/native/*.swift
