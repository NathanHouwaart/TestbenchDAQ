#!/usr/bin/env bash
# Multiple short fixed-duration runs.
#
# Usage:  ./run_burst.sh [duration_s] [count] [period_s]
# Example: ./run_burst.sh 30 5 60
#   → 5 runs of 30 s each, new run every 60 s (30 s recording + 30 s gap)
set -euo pipefail

DURATION="${1:-30}"
COUNT="${2:-5}"
PERIOD="${3:-60}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

[[ -f .venv/bin/activate ]] && source .venv/bin/activate

[[ ! -f config.json ]] && cp config_example.json config.json && echo "Created config.json from config_example.json — edit it to set your paths."

echo "Burst: ${COUNT} run(s) × ${DURATION}s  |  period ${PERIOD}s  |  gap $((PERIOD - DURATION))s"

python3 -m tbdaq \
  --config config.json \
  --run-duration-s "$DURATION" \
  --run-count      "$COUNT" \
  --run-period-s   "$PERIOD" \
  "${@:4}"
