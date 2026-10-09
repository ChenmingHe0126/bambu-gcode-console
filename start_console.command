#!/usr/bin/env bash
# macOS: Finder runs .command files in Terminal on double-click (right-click > Open the first time).
cd "$(dirname "$0")" || exit 1
exec bash ./start_console.sh "$@"
