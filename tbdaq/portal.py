"""Read-only session index used by the data portal API."""
from __future__ import annotations

import json
import re
import csv
import shutil
import itertools
import re
from pathlib import Path
from typing import Any


class PortalError(ValueError):
    """Raised when a requested portal resource is not safe or does not exist."""


_MACHINE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class SessionIndex:
    """Safely expose manifests and artifacts below a fixed read-only root."""

    def __init__(self, root: Path, metadata_root: Path | None = None) -> None:
        self.root = root.resolve()
        self.metadata_root = (metadata_root or self.root / ".portal-metadata").resolve()

    def machines(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        return [self.machine_summary(path.name) for path in sorted(self.root.iterdir()) if path.is_dir() and _MACHINE_NAME.fullmatch(path.name)]

    def overview(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.root)
        return {
            "storage": {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "free_percent": round((usage.free / usage.total) * 100, 1) if usage.total else 0,
            },
            "machines": self.machines(),
        }

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
        labels = self._labels(machine)
        for session_dir in directory.iterdir():
            manifest = session_dir / "session_manifest.json"
            if not session_dir.is_dir() or not manifest.is_file():
                continue
            try:
                data = self._read_json(manifest)
            except PortalError:
                continue
            records.append(self._summary(data, session_dir.name, labels.get(session_dir.name)))
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

    def csv_preview(self, machine: str, session_id: str, relative_path: str, offset: int = 0, limit: int = 250) -> dict[str, Any]:
        """Read a bounded page of a CSV without loading a recording into memory."""
        if offset < 0 or not 1 <= limit <= 500:
            raise PortalError("Invalid CSV page request.")
        path = self.artifact(machine, session_id, relative_path)
        if path.suffix.lower() != ".csv":
            raise PortalError("Only CSV files can be previewed.")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                columns = next(reader)
                rows = list(itertools.islice(reader, offset, offset + limit))
                has_more = next(reader, None) is not None
        except (OSError, UnicodeError, csv.Error, StopIteration) as exc:
            raise PortalError(f"Could not read CSV: {exc}") from exc
        return {"path": relative_path, "columns": columns, "rows": rows, "offset": offset, "limit": limit, "has_more": has_more}

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

    def chart(self, machine: str, session_id: str, relative_path: str, columns: list[str], start_s: float | None, end_s: float | None, buckets: int) -> dict[str, Any]:
        """Return min/max envelopes for the requested visible CSV time window."""
        if not 100 <= buckets <= 4_000:
            raise PortalError("buckets must be between 100 and 4000.")
        path = self.artifact(machine, session_id, relative_path)
        if path.suffix.lower() != ".csv":
            raise PortalError("Only CSV files can be charted.")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                if len(header) < 2:
                    raise PortalError("CSV needs a time column and value columns.")
                time_column, available = header[0], header[1:]
                first = next(reader, None)
                if first is None:
                    raise PortalError("CSV contains no data.")
                origin = self._csv_time(first[time_column], time_column)
                maximum = origin
                for row in reader:
                    try:
                        maximum = self._csv_time(row[time_column], time_column)
                    except (KeyError, TypeError, ValueError):
                        continue
        except (OSError, UnicodeError, csv.Error) as exc:
            raise PortalError(f"Could not read CSV: {exc}") from exc
        selected = [column for column in columns if column in available]
        lower = origin if start_s is None else max(origin, origin + start_s)
        upper = maximum if end_s is None else min(maximum, origin + end_s)
        if upper <= lower:
            upper = maximum if maximum > origin else origin + 1
        width = (upper - lower) / buckets
        envelopes: dict[str, list[list[float | None]]] = {column: [[float("inf"), float("-inf")] for _ in range(buckets)] for column in selected}
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    try:
                        timestamp = self._csv_time(row[time_column], time_column)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if timestamp < lower or timestamp > upper:
                        continue
                    bucket = min(buckets - 1, int((timestamp - lower) / width))
                    for column in selected:
                        try:
                            value = float(row[column])
                        except (KeyError, TypeError, ValueError):
                            continue
                        envelopes[column][bucket][0] = min(envelopes[column][bucket][0], value)
                        envelopes[column][bucket][1] = max(envelopes[column][bucket][1], value)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise PortalError(f"Could not read CSV: {exc}") from exc
        series = []
        for column, values in envelopes.items():
            points = [[round((lower + (index + 0.5) * width) - origin, 6), low, high] for index, (low, high) in enumerate(values) if low != float("inf")]
            series.append({"name": column, "points": points})
        return {"path": relative_path, "time_column": time_column, "available_columns": available, "start_s": lower - origin, "end_s": upper - origin, "series": series}

    @staticmethod
    def _csv_time(value: str, name: str) -> float:
        numeric = float(value)
        return numeric / 1_000_000 if name.endswith("_us") else numeric

    def archive_files(self, machine: str, session_id: str) -> list[tuple[Path, str]]:
        """Return safe files to include in a whole-session archive."""
        prefix = self.archive_name(machine, session_id)
        return [
            (self.artifact(machine, session_id, entry["path"]), f"{prefix}/{entry['path']}")
            for entry in self.artifacts(machine, session_id)
        ]

    def rename(self, machine: str, session_id: str, label: str) -> dict[str, Any]:
        self._session_directory(machine, session_id)
        label = " ".join(label.split())
        if not 1 <= len(label) <= 120:
            raise PortalError("Name must contain 1 to 120 characters.")
        labels = self._labels(machine)
        labels[session_id] = label
        path = self.metadata_root / f"{machine}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(labels, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            raise PortalError(f"Could not save portal label: {exc}") from exc
        return {"session_id": session_id, "display_name": label, "archive_name": self.archive_name(machine, session_id)}

    def archive_name(self, machine: str, session_id: str) -> str:
        label = self._labels(machine).get(session_id, session_id)
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", label).strip(" .")
        return safe or session_id

    def _labels(self, machine: str) -> dict[str, str]:
        try:
            value = json.loads((self.metadata_root / f"{machine}.json").read_text(encoding="utf-8"))
            return {key: text for key, text in value.items() if isinstance(key, str) and isinstance(text, str)} if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}

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
    def _summary(manifest: dict[str, Any], directory_name: str, display_name: str | None = None) -> dict[str, Any]:
        return {
            "session_id": manifest.get("session_id", directory_name),
            "name": manifest.get("name"),
            "display_name": display_name,
            "status": manifest.get("status", "unknown"),
            "started_at_utc": manifest.get("started_at_utc"),
            "ended_at_utc": manifest.get("ended_at_utc"),
            "live_status": manifest.get("live_status"),
            "run_count": len(manifest.get("runs", [])),
        }
