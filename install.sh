#!/bin/bash
# Select the platform-specific installer.
set -euo pipefail
cd "$(dirname "$0")"
case "$(uname -s)" in
  Darwin) exec ./setup-mac.sh "$@" ;;
  Linux) exec ./setup-pi.sh "$@" ;;
  *) echo 'Supported platforms: macOS and Raspberry Pi OS.' >&2; exit 1 ;;
esac
