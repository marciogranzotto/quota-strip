#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup-mac.sh once from your regular terminal."
  exit 1
fi
exec ./run.sh --windowed --source standalone
