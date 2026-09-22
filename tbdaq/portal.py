"""Read-only session index used by the data portal API."""
from __future__ import annotations

import json
import re
import csv
from pathlib import Path
from typing import Any


class PortalError(ValueError):
    """Raised when a requested portal resource is not safe or does not exist."""


_MACHINE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class SessionIndex:
    """Safely expose manifests and artifacts below a fixed read-only root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def machines(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        return [self.machine_summary(path.name) for path in sorted(self.root.iterdir()) if path.is_dir() and _MACHINE_NAME.fullmatch(path.name)]

    def machine_summary(self, machine: str) -> dict[str, Any]:
        directory = self._machine_directory(machine)
        sessions = self.sessions(machine)
        active = next((item for item in sessions if item["status"] == "running"), None)
        return {
            "machine": machine,
            "session_count": len(sessions),
            "active_session": active,
            "latest_session": sessions[0] if sessions else None,
            "directory_available": directory.is_dir(),
        }

    def sessions(self, machine: str) -> list[dict[str, Any]]:
        directory = self._machine_directory(machine)
        if not directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for session_dir in directory.iterdir():
            manifest = session_dir / "session_manifest.json"
            if not session_dir.is_dir() or not manifest.is_file():
                continue
            try:
                data = self._read_json(manifest)
            except PortalError:
                continue
            records.append(self._summary(data, session_dir.name))
        return sorted(records, key=lambda item: item.get("started_at_utc") or "", reverse=True)

    def manifest(self, machine: str, session_id: str) -> dict[str, Any]:
        return self._read_json(self._session_directory(machine, session_id) / "session_manifest.json")

    def artifact(self, machine: str, session_id: str, relative_path: str) -> Path:
        session = self._session_directory(machine, session_id)
        candidate = (session / relative_path).resolve()
        if candidate == session or session not in candidate.parents or not candidate.is_file():
            raise PortalError("Artifact not found.")
        return candidate

    def artifacts(self, machine: str, session_id: str) -> list[dict[str, Any]]:
        session = self._session_directory(machine, session_id)
        files: list[dict[str, Any]] = []
        for candidate in session.rglob("*"):
            if candidate.is_file() and not candidate.is_symlink():
                files.append({
                    "path": str(candidate.relative_to(session)).replace("\\", "/"),
                    "size_bytes": candidate.stat().st_size,
                })
        return sorted(files, key=lambda item: item["path"])

    def plot(self, machine: str, session_id: str, relative_path: str, max_points: int = 2_000) -> dict[str, Any]:
        """Return an evenly sampled time/value CSV series for browser plotting."""
        if not 100 <= max_points <= 10_000:
            raise PortalError("max_points must be between 100 and 10000.")
        path = self.artifact(machine, session_id, relative_path)
        if path.suffix.lower() != ".csv":
            raise PortalError("Only CSV signal files can be plotted.")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
                rows = sum(1 for _ in reader)
        except (OSError, UnicodeError, csv.Error, StopIteration) as exc:
            raise PortalError(f"Could not read CSV: {exc}") from exc
        if len(header) < 2:
            raise PortalError("CSV needs a time column and a value column.")
        step = max(1, (rows + max_points - 1) // max_points)
        points: list[list[float]] = []
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                next(reader)
                for row_number, row in enumerate(reader):
                    if row_number % step or len(row) < 2:
                        continue
                    try:
                        points.append([float(row[0]), float(row[1])])
                    except ValueError:
                        continue
        except (OSError, UnicodeError, csv.Error) as exc:
            raise PortalError(f"Could not read CSV: {exc}") from exc
        if not points:
            raise PortalError("CSV contains no numeric points.")
        return {"path": relative_path, "x_label": header[0], "y_label": header[1], "points": points}

    def archive_files(self, machine: str, session_id: str) -> list[tuple[Path, str]]:
        """Return safe files to include in a whole-session archive."""
        return [
            (self.artifact(machine, session_id, entry["path"]), entry["path"])
            for entry in self.artifacts(machine, session_id)
        ]

    def _machine_directory(self, machine: str) -> Path:
        if not _MACHINE_NAME.fullmatch(machine):
            raise PortalError("Unknown machine.")
        candidate = (self.root / machine).resolve()
        if candidate.parent != self.root or not candidate.is_dir():
            raise PortalError("Unknown machine.")
        return candidate

    def _session_directory(self, machine: str, session_id: str) -> Path:
        if not session_id or Path(session_id).name != session_id:
            raise PortalError("Unknown session.")
        machine_dir = self._machine_directory(machine)
        candidate = (machine_dir / session_id).resolve()
        if candidate.parent != machine_dir or not candidate.is_dir():
            raise PortalError("Unknown session.")
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PortalError(f"Could not read manifest: {exc}") from exc
        if not isinstance(value, dict):
            raise PortalError("Manifest is not a JSON object.")
        return value

    @staticmethod
    def _summary(manifest: dict[str, Any], directory_name: str) -> dict[str, Any]:
        return {
            "session_id": manifest.get("session_id", directory_name),
            "name": manifest.get("name"),
            "status": manifest.get("status", "unknown"),
            "started_at_utc": manifest.get("started_at_utc"),
            "ended_at_utc": manifest.get("ended_at_utc"),
            "live_status": manifest.get("live_status"),
            "run_count": len(manifest.get("runs", [])),
        }
