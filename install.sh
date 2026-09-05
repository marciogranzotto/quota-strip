#!/bin/bash
# The current deliverable is the Mac prototype. Pi setup is a later phase.
set -euo pipefail
cd "$(dirname "$0")"
exec ./setup-mac.sh
