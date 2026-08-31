#!/usr/bin/env bash
# Single timed measurement — useful for one-shot fault-response captures.
#
# Usage:  ./run_single.sh [duration_s]
# Example: ./run_single.sh 120
set -euo pipefail

DURATION="${1:-60}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

[[ -f .venv/bin/activate ]] && source .venv/bin/activate

[[ ! -f config.json ]] && cp config_example.json config.json && echo "Created config.json from config_example.json — edit it to set your paths."

echo "Single run: ${DURATION}s"

python3 -m tbdaq \
  --config config.json \
  --run-duration-s "$DURATION" \
  --run-count 1 \
  "${@:2}"
