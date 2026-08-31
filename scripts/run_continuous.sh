#!/usr/bin/env bash
# Manual diagnostic measurement. Press Enter to stop.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_configured.sh" --mode diagnostic --manual --run-count 1 "$@"
