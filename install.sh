#!/usr/bin/env bash
# Durable content-addressed installation; Python owns locks and recovery.
# Overrides: PLUGIN_HOME, VULNGATE_MARKETPLACE, CODEX_BIN.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/scripts/install_plugin.py" --source "$ROOT" "$@"
