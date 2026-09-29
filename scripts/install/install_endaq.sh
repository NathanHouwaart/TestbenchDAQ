#!/usr/bin/env bash
# Install deterministic enDAQ mounting and perform a read-only FAT check.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/install/install_endaq.sh [OPTIONS]

Install the deterministic enDAQ mount rule, check the recorder's FAT
filesystem, and mount it when it is safe to do so.

Options:
  --repair            Automatically repair a dirty FAT filesystem, then mount it
  --non-interactive   Do not prompt; use --repair to permit filesystem changes
  --help, -h          Show this help
EOF
}

repair=false
non_interactive=false
while (($#)); do
  case "$1" in
    --repair) repair=true ;;
    --non-interactive) non_interactive=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

INSTALL_USER="${SUDO_USER:-$USER}"
INSTALL_UID="$(id -u "$INSTALL_USER")"
INSTALL_GID="$(id -g "$INSTALL_USER")"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

FSTAB_LINE="UUID=6430-3964 /mnt/endaq vfat noauto,nofail,users,uid=${INSTALL_UID},gid=${INSTALL_GID},utf8,umask=022 0 0"
FSTAB_TMP="$(mktemp)"
trap 'rm -f "$FSTAB_TMP"' EXIT

matching_entries="$(awk '$1 == "UUID=6430-3964" { count++ } END { print count + 0 }' /etc/fstab)"
case "$matching_entries" in
  0)
    # A fresh Pi has no enDAQ entry. Add one exact, UUID-bound line rather
    # than asking the operator to edit fstab manually.
    cat /etc/fstab > "$FSTAB_TMP"
    printf '\n%s\n' "$FSTAB_LINE" >> "$FSTAB_TMP"
    ;;
  1)
    awk -v replacement="$FSTAB_LINE" '
      $1 == "UUID=6430-3964" { print replacement; next }
      { print }
    ' /etc/fstab > "$FSTAB_TMP"
    ;;
  *)
    echo "Found $matching_entries UUID=6430-3964 entries in /etc/fstab; no changes made." >&2
    echo "Remove the ambiguity manually before rerunning the installer." >&2
    exit 1
    ;;
esac

if [[ ! -e /etc/fstab.tbdaq-backup ]]; then
  install -o root -g root -m 0644 /etc/fstab /etc/fstab.tbdaq-backup
fi
if [[ -e /etc/udev/rules.d/99-endaq.rules ]] && \
   [[ ! -e /etc/udev/rules.d/99-endaq.rules.tbdaq-backup ]]; then
  install -o root -g root -m 0644 \
    /etc/udev/rules.d/99-endaq.rules \
    /etc/udev/rules.d/99-endaq.rules.tbdaq-backup
fi

install -o root -g root -m 0644 "$FSTAB_TMP" /etc/fstab
install -d -o root -g root -m 0755 /mnt/endaq
install -o root -g root -m 0644 \
  "$PROJECT_DIR/udev/99-endaq.rules" /etc/udev/rules.d/99-endaq.rules
systemctl daemon-reload
udevadm control --reload-rules

# A freshly configured Pi has no mount yet.  `findmnt` signals that with a
# non-zero exit status, which is expected here rather than an installer error.
BLOCK_DEVICE="$(findmnt --noheadings --raw --types vfat --output SOURCE --target /mnt/endaq | tail -n 1 || true)"
if [[ -z "$BLOCK_DEVICE" ]]; then
  BLOCK_DEVICE=/dev/disk/by-uuid/6430-3964
fi

systemctl stop mnt-endaq.automount 2>/dev/null || true
if findmnt --noheadings --types vfat --target /mnt/endaq >/dev/null 2>&1; then
  umount /mnt/endaq
fi

echo "Running read-only FAT check on $BLOCK_DEVICE..."
set +e
fsck.vfat -n "$BLOCK_DEVICE"
FSCK_STATUS=$?
set -e

if [[ "$FSCK_STATUS" -ne 0 ]]; then
  echo >&2
  echo "The enDAQ FAT check reported errors (exit $FSCK_STATUS)." >&2
  echo "The recorder must be repaired before it can be mounted for acquisition." >&2

  if ! $repair; then
    if $non_interactive || [[ ! -t 0 ]]; then
      echo "No repair was attempted. After confirming the recorder is not recording," >&2
      echo "rerun: sudo ./scripts/install/install_endaq.sh --repair" >&2
      exit "$FSCK_STATUS"
    fi

    echo >&2
    echo "Repairing modifies the recorder's FAT filesystem." >&2
    read -r -p "Confirm the recorder is stopped and repair it now? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
      repair=true
    else
      echo "No repair was attempted; the recorder remains unmounted." >&2
      exit "$FSCK_STATUS"
    fi
  fi

  echo "Repairing enDAQ FAT filesystem on $BLOCK_DEVICE..."
  set +e
  fsck.vfat -a "$BLOCK_DEVICE"
  REPAIR_STATUS=$?
  set -e

  echo "Verifying repaired enDAQ filesystem..."
  set +e
  fsck.vfat -n "$BLOCK_DEVICE"
  VERIFY_STATUS=$?
  set -e
  if [[ "$VERIFY_STATUS" -ne 0 ]]; then
    echo "The enDAQ filesystem could not be verified after repair (repair exit $REPAIR_STATUS, verification exit $VERIFY_STATUS)." >&2
    echo "It remains unmounted; inspect the output above before continuing." >&2
    exit "$VERIFY_STATUS"
  fi
fi

systemctl start mnt-endaq.mount
findmnt -T /mnt/endaq -o TARGET,SOURCE,FSTYPE,OPTIONS
echo "enDAQ mount setup installed; read-only FAT check passed."
