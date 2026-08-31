#!/usr/bin/env bash
# Gator-only measurement (no endaq).
# Handy for quickly checking sensor signal quality without needing the endaq connected.
#
# Usage:  ./run_gator_only.sh [duration_s] [channel]
# Example: ./run_gator_only.sh 10 8
set -euo pipefail

DURATION="${1:-10}"
CHANNEL="${2:-8}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

[[ -f .venv/bin/activate ]] && source .venv/bin/activate

[[ ! -f config.json ]] && cp config_example.json config.json && echo "Created config.json from config_example.json — edit it to set your paths."

echo "Gator-only: channel ${CHANNEL} for ${DURATION}s"

python3 -m tbdaq \
  --config config.json \
  --no-endaq \
  --gator-channel  "$CHANNEL" \
  --run-duration-s "$DURATION" \
  --run-count 1 \
  "${@:3}"
