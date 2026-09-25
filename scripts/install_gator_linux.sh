#!/usr/bin/env bash
# Install the local Gator recorder and PhotonFirst runtime for all users.
# The vendor SDK is an input to this script; it is not copied into Git.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/install_gator_linux.sh --gtr-api-dir /path/to/raspberry-pi4

Builds gator_recorder, installs it to /usr/local/bin/gator_recorder, installs
the PhotonFirst shared library to /usr/local/lib/photonfirst, and registers
that directory with ldconfig. Run as the normal acquisition user; sudo is
requested only for the system-wide installation steps.
EOF
}

gtr_api_dir=""
while (($#)); do
  case "$1" in
    --gtr-api-dir)
      (($# >= 2)) || { echo "--gtr-api-dir needs a value." >&2; exit 2; }
      gtr_api_dir="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$gtr_api_dir" ]] || { usage >&2; exit 2; }
[[ "$(uname -s)" == "Linux" ]] || { echo "This installer supports Linux only." >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" ]] || {
  echo "This installer requires an AArch64 machine; found $(uname -m)." >&2
  exit 1
}

gtr_api_dir="$(realpath "$gtr_api_dir")"
[[ -d "$gtr_api_dir/include" ]] || {
  echo "No include/ directory in GTR API package: $gtr_api_dir" >&2
  exit 1
}
shopt -s nullglob
libraries=("$gtr_api_dir"/libgtrlib-pub-shared-v*.so)
shopt -u nullglob
(( ${#libraries[@]} == 1 )) || {
  echo "Expected exactly one libgtrlib-pub-shared-v*.so in $gtr_api_dir." >&2
  exit 1
}

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$repo_dir/gator_recorder/build"
cmake -S "$repo_dir/gator_recorder" -B "$build_dir" -DGTR_API_DIR="$gtr_api_dir" -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir"

recorder="$build_dir/gator_recorder"
[[ -x "$recorder" ]] || { echo "Build did not produce $recorder" >&2; exit 1; }

sudo install -d -o root -g root -m 0755 /usr/local/lib/photonfirst
sudo install -o root -g root -m 0755 "$recorder" /usr/local/bin/gator_recorder
sudo install -o root -g root -m 0644 "${libraries[0]}" /usr/local/lib/photonfirst/
printf '%s\n' /usr/local/lib/photonfirst | sudo tee /etc/ld.so.conf.d/photonfirst.conf >/dev/null
sudo ldconfig

echo "Installed /usr/local/bin/gator_recorder"
echo "Installed $(basename "${libraries[0]}") in /usr/local/lib/photonfirst"
echo "Checking dynamic dependencies:"
ldd /usr/local/bin/gator_recorder | grep -F "$(basename "${libraries[0]}")"
echo "Gator runtime installation completed. No binary_path or library_path is needed."
