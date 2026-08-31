#!/usr/bin/env bash
# Shared config-first launcher. Set TBDAQ_CONFIG to use a different file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${TBDAQ_CONFIG:-$PROJECT_DIR/config.json}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Configuration not found: $CONFIG_PATH" >&2
  echo "Copy config_example.json to config.json and edit it first." >&2
  exit 2
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/tbdaq" ]]; then
  echo "TestbenchDAQ is not installed in $PROJECT_DIR/.venv" >&2
  exit 2
fi

exec "$PROJECT_DIR/.venv/bin/tbdaq" --config "$CONFIG_PATH" "$@"
