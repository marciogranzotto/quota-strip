#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(uname -s)" != Darwin ]; then
  echo "This installer is for macOS. Raspberry Pi deployment is a later phase."
  exit 1
fi
PYTHON_BIN="${QUOTA_PYTHON:-python3}"
# A framework build gives SDL a macOS app identity and normal window handling.
# Honor an explicit interpreter; otherwise prefer an installed 3.13 framework
# over a non-framework pyenv interpreter for this native GUI prototype.
if [ -z "${QUOTA_PYTHON:-}" ] && [ -z "$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("PYTHONFRAMEWORK") or "")')" ]; then
  if command -v python3.13 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.13)"
  fi
fi
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
import json, pathlib, shutil
config = {}
binary = shutil.which('codex')
if binary:
    config['codex_bin'] = binary
else:
    print('Codex CLI was not found. Put codex on PATH or set QUOTA_CODEX_BIN before launch.')
# Optional: with cswap installed, Claude accounts are read through its store.
if shutil.which('cswap'):
    config['cswap_bin'] = shutil.which('cswap')
pathlib.Path('.local-config.json').write_text(json.dumps(config))
PY
chmod +x run.sh 'Launch Quota Strip.command'
echo "Ready. Open Launch Quota Strip.command or run ./run.sh --source local --windowed"
