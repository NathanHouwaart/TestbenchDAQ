#!/usr/bin/env bash
# Compatibility alias. Select Gator-only in acquisition.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/configured.sh" "$@"
