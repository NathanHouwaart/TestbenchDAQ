#!/usr/bin/env bash
# Run the acquisition defined in acquisition.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/configured.sh" "$@"
