"""Output-storage safety checks for acquisition sessions."""
from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class StorageError(RuntimeError):
    """Raised when the configured acquisition destination is unsafe."""


@dataclass(frozen=True)
class StorageInfo:
    path: str
    mode: str
    filesystem_type: str | None
    free_bytes: int
    total_bytes: int


def validate_output_storage(
    output_root: Path,
    *,
    allow_local_output: bool = False,
    mount_lines: Callable[[], Iterable[str]] | None = None,
) -> StorageInfo:
    """Require a writable NFS mount unless an operator explicitly overrides it."""
    root = output_root.resolve()
    if not root.is_dir():
        raise StorageError(f"Output root does not exist or is not a directory: {root}")

    filesystem_type = _filesystem_type(root, mount_lines or _read_mountinfo)
    is_nfs = filesystem_type in {"nfs", "nfs4"}
    if not is_nfs and not allow_local_output:
        detail = filesystem_type or "unknown filesystem"
        raise StorageError(
            f"Output root must be an active NFS mount; {root} is on {detail}. "
            "Mount the server storage or rerun with --allow-local-output."
        )
    _verify_writable(root)
    usage = shutil.disk_usage(root)
    return StorageInfo(
        path=str(root),
        mode="nfs" if is_nfs else "local_override",
        filesystem_type=filesystem_type,
        free_bytes=usage.free,
        total_bytes=usage.total,
    )


def _read_mountinfo() -> Iterable[str]:
    try:
        return Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()


def _filesystem_type(path: Path, read_lines: Callable[[], Iterable[str]]) -> str | None:
    """Return the filesystem for the deepest Linux mount containing *path*."""
    candidate = str(path)
    matches: list[tuple[int, str]] = []
    for line in read_lines():
        fields = line.split(" - ", maxsplit=1)
        if len(fields) != 2:
            continue
        before, after = fields
        before_fields = before.split()
        after_fields = after.split()
        if len(before_fields) < 5 or not after_fields:
            continue
        mount_point = _unescape_mount_path(before_fields[4])
        if candidate == mount_point or candidate.startswith(mount_point.rstrip("/") + "/"):
            matches.append((len(mount_point), after_fields[0]))
    return max(matches, default=(0, None))[1]


def _unescape_mount_path(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _verify_writable(root: Path) -> None:
    probe = root / f".tbdaq-write-check-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("TestbenchDAQ output preflight.\n")
        probe.unlink()
    except OSError as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError(f"Output root is not writable: {root}: {exc}") from exc
