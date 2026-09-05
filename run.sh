#!/bin/bash
# Launcher for Quota Strip. Uses the local venv when available.
cd "$(dirname "$0")" || exit 1
export PYGAME_HIDE_SUPPORT_PROMPT=1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -u quota_display.py "$@"
fi
exec python3 -u quota_display.py "$@"
