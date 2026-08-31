#!/usr/bin/env bash
# Diagnostic Gator-only run. Timing and Gator settings come from config.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_configured.sh" \
  --mode diagnostic --run-count 1 --gator --no-endaq "$@"
