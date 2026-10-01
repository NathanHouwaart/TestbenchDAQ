#!/usr/bin/env bash
# Shared split-configuration launcher. Both files are required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MACHINE_PATH="${TBDAQ_MACHINE:-$PROJECT_DIR/machine.json}"
ACQUISITION_PATH="${TBDAQ_ACQUISITION:-$PROJECT_DIR/acquisition.json}"

if [[ ! -f "$MACHINE_PATH" || ! -f "$ACQUISITION_PATH" ]]; then
  echo "Machine or acquisition configuration is missing." >&2
  echo "Copy machine.example.json to machine.json and acquisition.example.json to acquisition.json first." >&2
  exit 2
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/tbdaq" ]]; then
  echo "TestbenchDAQ is not installed in $PROJECT_DIR/.venv" >&2
  exit 2
fi

exec "$PROJECT_DIR/.venv/bin/tbdaq" \
  --machine "$MACHINE_PATH" --acquisition "$ACQUISITION_PATH" "$@"
