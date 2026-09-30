"""Local-only control API for a single TestbenchDAQ acquisition session.

This service intentionally accepts only experiment-level acquisition choices.
Machine identity, storage roots, recorder paths, calibration, and destructive
retention settings remain in a protected local configuration file.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Mapping

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from tbdaq.config import (
    ConfigError,
    SessionConfig,
    session_config_from_machine_and_acquisition,
)
from tbdaq.session import Session
from tbdaq.storage import StorageError, StorageInfo, validate_output_storage


class ServiceError(RuntimeError):
    """A safe, user-facing local service error."""


@dataclass(frozen=True)
class RunRequest:
    """The small, safe subset of settings an operator client may submit."""

    session_id: str
    acquisition: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunRequest":
        allowed = {"session_id", "acquisition"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ServiceError(f"Unknown run request field(s): {', '.join(unknown)}.")
        try:
            session_id = str(value["session_id"]).strip()
            acquisition = value["acquisition"]
        except KeyError as exc:
            raise ServiceError(f"Missing required run request field: {exc.args[0]}.") from exc
        if not session_id or any(char in session_id for char in "/\\"):
            raise ServiceError("session_id must be a non-empty directory name.")
        if not isinstance(acquisition, Mapping):
            raise ServiceError("acquisition must be a JSON object.")
        return cls(session_id, dict(acquisition))


def load_machine_config(path: Path) -> dict[str, Any]:
    """Load the protected local JSON configuration without mutating it."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ServiceError(f"Could not read machine configuration: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ServiceError(f"Invalid JSON in machine configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ServiceError("Machine configuration root must be a JSON object.")
    return value


def merge_run_request(machine_config: Mapping[str, Any], request: RunRequest, *, base_dir: Path) -> SessionConfig:
    """Resolve the portable request against protected machine configuration."""
    try:
        return session_config_from_machine_and_acquisition(
            machine_config, request.acquisition, base_dir=base_dir,
        )
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


class LocalServiceController:
    """Thread-safe controller enforcing one active local DAQ session."""

    def __init__(
        self,
        machine_config_path: Path,
        *,
        allow_local_output: bool = False,
        session_factory: Callable[..., Session] = Session,
    ) -> None:
        self._config_path = machine_config_path.resolve()
        self._allow_local_output = allow_local_output
        self._session_factory = session_factory
        self._lock = threading.RLock()
        self._session: Session | None = None
        self._thread: threading.Thread | None = None
        self._request: RunRequest | None = None
        self._terminal_manifest: dict[str, Any] | None = None
        self._error: str | None = None

    def health(self) -> dict[str, Any]:
        try:
            values = load_machine_config(self._config_path)
            storage = self._storage_from_machine_config(values)
            configured_sources = [
                family for family in ("gator", "endaq")
                if isinstance(values.get(family), dict)
            ]
            return {
                "service": "ready",
                "storage": _storage_dict(storage),
                "configured_sources": configured_sources,
            }
        except (ServiceError, ConfigError, StorageError, OSError) as exc:
            return {"service": "unavailable", "error": str(exc)}

    def validate(self, request: RunRequest) -> dict[str, Any]:
        try:
            config = self._resolve_request(request)
            storage = validate_output_storage(
                Path(config.output_root), allow_local_output=self._allow_local_output
            )
        except (ServiceError, ConfigError, StorageError, OSError) as exc:
            return {"valid": False, "errors": [str(exc)]}
        return {
            "valid": True,
            "errors": [],
            "enabled_sources": list(config.enabled_families()),
            "storage": _storage_dict(storage),
        }

    def capabilities(self) -> dict[str, Any]:
        """Return read-only recorder capabilities for an operator client.

        This intentionally never calls ``discover()`` because discovery would
        apply settings and synchronise the device clock.  It only describes a
        mounted enDAQ, allowing TestDesigner to populate a selector safely.
        """
        acquisition = {
            "schema": "testbenchdaq/acquisition/v1",
            "name": "capability-query",
            "schedule": {"window_duration_s": 1, "run_count": 1},
            "gator": {"enabled": False},
            "endaq": {"enabled": True, "channels": []},
        }
        try:
            config = session_config_from_machine_and_acquisition(
                load_machine_config(self._config_path), acquisition,
                base_dir=self._config_path.parent,
            )
            from tbdaq.adapters.endaq import EndaqAdapter

            return {"endaq": {"available": True, **EndaqAdapter(config.endaq).describe()}}
        except (ConfigError, OSError, RuntimeError) as exc:
            return {"endaq": {"available": False, "error": str(exc)}}

    def start(self, request: RunRequest) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ServiceError("TestbenchDAQ is busy with another active session.")
            config = self._resolve_request(request)
            try:
                session = self._session_factory(
                    config,
                    session_id=request.session_id,
                    allow_local_output=self._allow_local_output,
                )
            except Exception as exc:
                raise ServiceError(f"Could not create DAQ session: {exc}") from exc
            self._session = session
            storage = self._storage_from_machine_config(load_machine_config(self._config_path))
            if storage.mode == "nfs" and hasattr(session, "enable_server_processing"):
                session.enable_server_processing()
            self._request = request
            self._terminal_manifest = None
            self._error = None
            self._thread = threading.Thread(target=self._run_session, daemon=True)
            self._thread.start()
            return {
                "state": "starting",
                "session_id": request.session_id,
                "run_root": str(session.session_dir / "run_01"),
            }

    def complete(self) -> dict[str, Any]:
        with self._lock:
            if self._session is None or self._thread is None or not self._thread.is_alive():
                raise ServiceError("No active TestbenchDAQ session to complete.")
            self._session.request_complete()
            return self.status()

    def abort(self) -> dict[str, Any]:
        with self._lock:
            if self._session is None or self._thread is None or not self._thread.is_alive():
                raise ServiceError("No active TestbenchDAQ session to abort.")
            self._session.request_stop()
            return self.status()

    def finalise(self) -> dict[str, Any]:
        """Backward-compatible alias for the former interrupting endpoint."""
        return self.abort()

    def status(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            thread = self._thread
            request = self._request
            terminal = copy.deepcopy(self._terminal_manifest)
            error = self._error
        if session is None or request is None:
            return {"service": "ready", "run": {"state": "idle"}}
        manifest = _read_manifest(session.session_dir / "session_manifest.json")
        if thread is not None and thread.is_alive():
            return {
                "service": "ready",
                "run": {
                    "state": _active_state(manifest),
                    "session_id": request.session_id,
                    "run_number": _current_run(manifest),
                    "run_root": str(session.session_dir / "run_01"),
                    "error": error,
                },
            }
        latest = _read_manifest(session.session_dir / "session_manifest.json")
        if not latest.get("status"):
            latest = terminal or latest
        return {
            "service": "ready",
            "run": {
                "state": _terminal_state(latest, error),
                "session_id": request.session_id,
                "run_number": _current_run(latest),
                "run_root": str(session.session_dir / "run_01"),
                "error": error or (latest or {}).get("abort_reason"),
            },
        }

    def _storage_from_machine_config(self, values: Mapping[str, Any]) -> StorageInfo:
        output_root = values.get("output_root")
        if not isinstance(output_root, str) or not output_root.strip():
            raise ServiceError("Machine configuration requires a non-empty output_root.")
        root = Path(output_root).expanduser()
        if not root.is_absolute():
            root = self._config_path.parent / root
        return validate_output_storage(
            root.resolve(), allow_local_output=self._allow_local_output
        )

    def _resolve_request(self, request: RunRequest) -> SessionConfig:
        return merge_run_request(
            load_machine_config(self._config_path), request, base_dir=self._config_path.parent
        )

    def _run_session(self) -> None:
        assert self._session is not None
        try:
            manifest = self._session.run()
            with self._lock:
                self._terminal_manifest = manifest
        except Exception as exc:
            with self._lock:
                self._error = f"Unhandled service session error: {exc}"


class RunRequestBody(BaseModel):
    session_id: str
    acquisition: dict[str, Any]


def create_app(machine_config_path: Path, *, allow_local_output: bool = False) -> FastAPI:
    """Create the loopback-only API application used by the systemd service."""
    controller = LocalServiceController(machine_config_path, allow_local_output=allow_local_output)
    app = FastAPI(title="TestbenchDAQ local control", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return controller.health()

    @app.get("/status")
    def status() -> dict[str, Any]:
        return controller.status()

    @app.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        return controller.capabilities()

    @app.post("/validate")
    def validate(body: RunRequestBody) -> dict[str, Any]:
        return controller.validate(RunRequest.from_mapping(body.model_dump()))

    @app.post("/runs/start")
    def start(body: RunRequestBody) -> dict[str, Any]:
        try:
            return controller.start(RunRequest.from_mapping(body.model_dump()))
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/runs/complete")
    def complete() -> dict[str, Any]:
        try:
            return controller.complete()
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/runs/abort")
    def abort() -> dict[str, Any]:
        try:
            return controller.abort()
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def _storage_dict(storage: StorageInfo) -> dict[str, Any]:
    return {
        "output_root": storage.path,
        "available": True,
        "free_bytes": storage.free_bytes,
        "total_bytes": storage.total_bytes,
        "filesystem_type": storage.filesystem_type,
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _active_state(manifest: Mapping[str, Any]) -> str:
    phase = str(manifest.get("live_status", {}).get("phase", "starting"))
    if phase == "measuring":
        return "recording"
    if phase == "waiting":
        return "armed"
    if phase in {"stopping", "processing"}:
        return "finalising"
    if phase == "queued":
        return "queued"
    return "starting"


def _terminal_state(manifest: Mapping[str, Any] | None, error: str | None) -> str:
    if error:
        return "failed"
    status = str((manifest or {}).get("status", "failed"))
    if status in {"success", "partial"}:
        return "completed"
    if status == "processing":
        return "queued"
    if status == "processing_failed":
        return "processing_failed"
    if status in {"aborted", "interrupted"}:
        return "aborted"
    return "failed"


def _current_run(manifest: Mapping[str, Any] | None) -> int | None:
    value = (manifest or {}).get("live_status", {}).get("current_run")
    return value if isinstance(value, int) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TestbenchDAQ's localhost control service.")
    parser.add_argument("--config", required=True, type=Path, help="Protected local machine JSON config")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--allow-local-output", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.config, allow_local_output=args.allow_local_output),
        host="127.0.0.1", port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
