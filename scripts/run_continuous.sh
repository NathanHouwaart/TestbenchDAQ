#!/usr/bin/env bash
# Continuous measurement — press Ctrl+C to stop.
# Both Gator and endaq run until you interrupt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

[[ -f .venv/bin/activate ]] && source .venv/bin/activate

[[ ! -f config.json ]] && cp config_example.json config.json && echo "Created config.json from config_example.json — edit it to set your paths."

python3 -m tbdaq \
  --config config.json \
  --run-count 1 \
  "$@"
