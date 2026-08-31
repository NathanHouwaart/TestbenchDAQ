#!/usr/bin/env bash
# Long-running soak test — many short runs over an extended period.
# Useful for endurance / fatigue tests where you want snapshots every N minutes.
#
# Usage:  ./run_soak.sh [duration_s] [count] [period_s]
# Example: ./run_soak.sh 60 60 300
#   → 60 runs of 1 min each, one every 5 min  → 5 hours of soak with 4 min idle between runs
set -euo pipefail

DURATION="${1:-60}"
COUNT="${2:-60}"
PERIOD="${3:-300}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

[[ -f .venv/bin/activate ]] && source .venv/bin/activate

[[ ! -f config.json ]] && cp config_example.json config.json && echo "Created config.json from config_example.json — edit it to set your paths."

TOTAL_HOURS=$(echo "scale=2; $COUNT * $PERIOD / 3600" | bc)
echo "Soak test: ${COUNT} run(s) × ${DURATION}s  |  period ${PERIOD}s  |  total wall time ~${TOTAL_HOURS}h"
echo "Output → $(python3 -c "import json,sys; c=json.load(open('config.json')); print(c.get('output_root','./csv-output'))")"
echo ""

python3 -m tbdaq \
  --config config.json \
  --mode prognostic \
  --run-duration-s "$DURATION" \
  --run-count      "$COUNT" \
  --run-period-s   "$PERIOD" \
  "${@:4}"
