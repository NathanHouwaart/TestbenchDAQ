#!/usr/bin/env bash
# Set up the Python application and optionally install Gator and enDAQ support.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ./scripts/install/setup_linux.sh [OPTIONS]

Set up TestbenchDAQ on a supported Linux/AArch64 acquisition host. Without
component options, an interactive terminal asks which hardware to install.

Options:
  --gator             Install the PhotonFirst Gator runtime and USB access rule
  --endaq             Install deterministic enDAQ mounting
  --all               Install both hardware components
  --non-interactive   Do not ask questions; requires a component option
  --help, -h          Show this help

The script creates .venv when needed, installs the Python application, runs
the selected hardware installers, then verifies tbdaq --help.
EOF
}

want_gator=false
want_endaq=false
non_interactive=false
while (($#)); do
  case "$1" in
    --gator) want_gator=true ;;
    --endaq) want_endaq=true ;;
    --all) want_gator=true; want_endaq=true ;;
    --non-interactive) non_interactive=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$(uname -s)" == "Linux" ]] || { echo "This setup script supports Linux only." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Python 3.11 or newer is required." >&2; exit 1; }

if ! $want_gator && ! $want_endaq; then
  if $non_interactive || [[ ! -t 0 ]]; then
    echo "Choose --gator, --endaq, or --all when not using interactive setup." >&2
    exit 2
  fi
  read -r -p "Install Gator support? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] && want_gator=true
  read -r -p "Install enDAQ mount support? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] && want_endaq=true
fi

if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
  python3 -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"

$want_gator && "$SCRIPT_DIR/install_gator.sh"
if $want_endaq; then
  endaq_args=()
  $non_interactive && endaq_args+=(--non-interactive)
  "$SCRIPT_DIR/install_endaq.sh" "${endaq_args[@]}"
fi

"$PROJECT_DIR/.venv/bin/tbdaq" --help
echo "TestbenchDAQ setup completed."
