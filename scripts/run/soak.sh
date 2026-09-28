#!/usr/bin/env bash
# Long prognostic series; all timing and sensor settings come from config.json.
# This remains as a descriptive alias for burst.sh.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/configured.sh" --mode prognostic "$@"
