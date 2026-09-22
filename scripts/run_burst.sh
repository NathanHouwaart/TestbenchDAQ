#!/usr/bin/env bash
# Prognostic run series. Count, duration, period and sensors come from config.json.
# Example explicit override: ./scripts/run_burst.sh --run-count 5 --name trial-a
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_configured.sh" --mode prognostic "$@"
