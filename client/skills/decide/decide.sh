#!/bin/sh
# Thin wrapper so `decide` works as a command on Unix-like shells.
# Point it at this repo: adjust REPO if the skill folder is copied elsewhere.
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
exec python3 "$REPO/bin/decide.py" "$@"
