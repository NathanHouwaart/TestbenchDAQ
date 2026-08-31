#!/usr/bin/env bash
# One diagnostic run. Duration and sensor settings come from config.json.
# Explicit named CLI options may still be supplied as overrides.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_configured.sh" --mode diagnostic --run-count 1 "$@"
