#!/usr/bin/env bash
# Install deterministic enDAQ mounting and perform a read-only FAT check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

FSTAB_LINE='UUID=6430-3964 /mnt/endaq vfat noauto,nofail,users,uid=1000,gid=1000,utf8,umask=022 0 0'
FSTAB_TMP="$(mktemp)"
trap 'rm -f "$FSTAB_TMP"' EXIT

awk -v replacement="$FSTAB_LINE" '
  $1 == "UUID=6430-3964" { print replacement; found++; next }
  { print }
  END { if (found != 1) exit 42 }
' /etc/fstab > "$FSTAB_TMP" || {
  echo "Expected exactly one UUID=6430-3964 entry in /etc/fstab; no changes made." >&2
  exit 1
}

[[ -e /etc/fstab.tbdaq-backup ]] || \
  install -o root -g root -m 0644 /etc/fstab /etc/fstab.tbdaq-backup
[[ ! -e /etc/udev/rules.d/99-endaq.rules || -e /etc/udev/rules.d/99-endaq.rules.tbdaq-backup ]] || \
  install -o root -g root -m 0644 \
    /etc/udev/rules.d/99-endaq.rules \
    /etc/udev/rules.d/99-endaq.rules.tbdaq-backup

install -o root -g root -m 0644 "$FSTAB_TMP" /etc/fstab
install -o root -g root -m 0644 \
  "$PROJECT_DIR/udev/99-endaq.rules" /etc/udev/rules.d/99-endaq.rules
systemctl daemon-reload
udevadm control --reload-rules

BLOCK_DEVICE="$(findmnt --noheadings --raw --types vfat --output SOURCE --target /mnt/endaq | tail -n 1)"
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
  echo "It has been left unmounted; no repair was attempted." >&2
  echo "Paste the output above into the TestbenchDAQ conversation before repairing it." >&2
  exit "$FSCK_STATUS"
fi

systemctl start mnt-endaq.mount
findmnt -T /mnt/endaq -o TARGET,SOURCE,FSTYPE,OPTIONS
echo "enDAQ mount setup installed; read-only FAT check passed."
