#!/usr/bin/env bash
# Compatibility alias. Configure a manual diagnostic window in acquisition.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/configured.sh" "$@"
