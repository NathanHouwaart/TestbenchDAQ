#!/usr/bin/env bash
# Install the Gator recorder and the approved PhotonFirst ARM64 runtime.
# Vendor files come from the private MaintenanceLab GitHub Release, never from
# this public source repository.
set -Eeuo pipefail

RUNTIME_REPOSITORY="Maintenance-Lab/ML-Machine-Vendor-Runtimes"
DEFAULT_RELEASE_TAG="photonfirst-gtr-v0.1.0"
ARCHIVE_NAME="photonfirst-gtr-linux-aarch64.tar.gz"
CHECKSUM_NAME="SHA256SUMS"
SDK_ROOT_NAME="public_gtr_api_v0.1.0"
INSTALL_BINARY="/usr/local/bin/gator_recorder"
INSTALL_LIBRARY_DIR="/usr/local/lib/photonfirst"
LINKER_CONFIG="/etc/ld.so.conf.d/photonfirst.conf"

usage() {
  cat <<EOF
Usage: ./scripts/install_gator_linux.sh [OPTIONS]

Install the pinned PhotonFirst Gator runtime and the TestbenchDAQ recorder.

Default mode downloads '${ARCHIVE_NAME}' and '${CHECKSUM_NAME}' from the
private GitHub Release '${DEFAULT_RELEASE_TAG}'. It requires a one-time:

  gh auth login

Options:
  --release TAG       Private runtime release tag (default: ${DEFAULT_RELEASE_TAG})
  --archive PATH      Use an already-downloaded runtime archive instead
  --checksums PATH    Checksum file for --archive (default: sibling SHA256SUMS)
  --help, -h          Show this help

The archive is always verified before extraction. The installer builds in a
temporary directory, installs the recorder to ${INSTALL_BINARY}, installs the
vendor .so to ${INSTALL_LIBRARY_DIR}, and runs ldconfig.
EOF
}

release_tag="$DEFAULT_RELEASE_TAG"
archive_path=""
checksums_path=""
while (($#)); do
  case "$1" in
    --release)
      (($# >= 2)) || { echo "--release needs a value." >&2; exit 2; }
      release_tag="$2"
      shift 2
      ;;
    --archive)
      (($# >= 2)) || { echo "--archive needs a value." >&2; exit 2; }
      archive_path="$2"
      shift 2
      ;;
    --checksums)
      (($# >= 2)) || { echo "--checksums needs a value." >&2; exit 2; }
      checksums_path="$2"
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

die() { echo "Error: $*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

[[ "$(uname -s)" == "Linux" ]] || die "This installer supports Linux only."
[[ "$(uname -m)" == "aarch64" ]] || die "This installer requires AArch64; found $(uname -m)."
require_command cmake
require_command c++
require_command tar
require_command sha256sum
require_command sudo
require_command grep
require_command realpath
require_command mktemp

temporary_dir="$(mktemp -d -t testbenchdaq-gator.XXXXXX)"
cleanup() { rm -rf "$temporary_dir"; }
trap cleanup EXIT

if [[ -n "$archive_path" ]]; then
  [[ -f "$archive_path" ]] || die "Runtime archive not found: $archive_path"
  archive_path="$(realpath "$archive_path")"
  if [[ -z "$checksums_path" ]]; then
    checksums_path="$(dirname "$archive_path")/$CHECKSUM_NAME"
  fi
  [[ -f "$checksums_path" ]] || die "Checksum file not found: $checksums_path"
  checksums_path="$(realpath "$checksums_path")"
else
  [[ -z "$checksums_path" ]] || die "--checksums requires --archive."
  require_command gh
  gh auth status --hostname github.com >/dev/null 2>&1 || die \
    "GitHub CLI is not authenticated. Run 'gh auth login' with an account that can read $RUNTIME_REPOSITORY."
  echo "Downloading private runtime release $release_tag from $RUNTIME_REPOSITORY..."
  gh release download "$release_tag" \
    --repo "$RUNTIME_REPOSITORY" \
    --dir "$temporary_dir" \
    --pattern "$ARCHIVE_NAME" \
    --pattern "$CHECKSUM_NAME" \
    --clobber
  archive_path="$temporary_dir/$ARCHIVE_NAME"
  checksums_path="$temporary_dir/$CHECKSUM_NAME"
fi

expected_name="$(basename "$archive_path")"
[[ "$expected_name" == "$ARCHIVE_NAME" ]] || die \
  "Expected archive named $ARCHIVE_NAME, got $expected_name."
tr -d '\r' < "$checksums_path" | grep -F "  $ARCHIVE_NAME" > "$temporary_dir/checksum-entry" || die \
  "$CHECKSUM_NAME has no entry for $ARCHIVE_NAME."
(
  cd "$(dirname "$archive_path")"
  sha256sum --check "$temporary_dir/checksum-entry"
)

tar -tzf "$archive_path" > "$temporary_dir/archive-list"
grep -q "^${SDK_ROOT_NAME}/" "$temporary_dir/archive-list" || die \
  "Runtime archive does not contain the expected $SDK_ROOT_NAME/ root."
tar -xzf "$archive_path" -C "$temporary_dir"
sdk_dir="$temporary_dir/$SDK_ROOT_NAME/raspberry-pi4"
[[ -d "$sdk_dir/include" ]] || die "Runtime archive has no Raspberry Pi include directory."

shopt -s nullglob
libraries=("$sdk_dir"/libgtrlib-pub-shared-v*.so)
shopt -u nullglob
(( ${#libraries[@]} == 1 )) || die \
  "Expected exactly one libgtrlib-pub-shared-v*.so in the runtime archive."

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$temporary_dir/gator-recorder-build"
echo "Building gator_recorder against $SDK_ROOT_NAME..."
cmake -S "$repo_dir/gator_recorder" -B "$build_dir" \
  -DGTR_API_DIR="$sdk_dir" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir"

recorder="$build_dir/gator_recorder"
[[ -x "$recorder" ]] || die "Build did not produce $recorder"

echo "Installing system-wide runtime (sudo may prompt once)..."
sudo install -d -o root -g root -m 0755 "$INSTALL_LIBRARY_DIR"
sudo install -o root -g root -m 0755 "$recorder" "$INSTALL_BINARY"
sudo install -o root -g root -m 0644 "${libraries[0]}" "$INSTALL_LIBRARY_DIR/"
printf '%s\n' "$INSTALL_LIBRARY_DIR" | sudo tee "$LINKER_CONFIG" >/dev/null
sudo ldconfig

installed_library="$INSTALL_LIBRARY_DIR/$(basename "${libraries[0]}")"
[[ -f "$installed_library" ]] || die "Installed vendor library is missing: $installed_library"
ldd "$INSTALL_BINARY" | grep -F "$(basename "$installed_library")" >/dev/null || die \
  "The installed recorder cannot resolve $(basename "$installed_library")."

echo "Installed $INSTALL_BINARY"
echo "Installed $installed_library"
echo "Gator runtime installation completed. Normal acquisition commands need no Gator path settings."
