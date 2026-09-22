"""Docker-hosted, read-only HTTP API for TestbenchDAQ session data."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from tbdaq.portal import PortalError, SessionIndex


index = SessionIndex(Path(os.environ.get("TBDAQ_DATA_ROOT", "/data")))
app = FastAPI(title="TestbenchDAQ data portal", docs_url=None, redoc_url=None)


def _not_found(action):
    try:
        return action()
    except PortalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/machines")
def machines():
    return index.machines()


@app.get("/api/machines/{machine}")
def machine(machine: str):
    return _not_found(lambda: index.machine_summary(machine))


@app.get("/api/machines/{machine}/sessions")
def sessions(machine: str):
    return _not_found(lambda: index.sessions(machine))


@app.get("/api/machines/{machine}/sessions/{session_id}/manifest")
def manifest(machine: str, session_id: str):
    return _not_found(lambda: index.manifest(machine, session_id))


@app.get("/api/machines/{machine}/sessions/{session_id}/artifacts")
def artifacts(machine: str, session_id: str):
    return _not_found(lambda: index.artifacts(machine, session_id))


@app.get("/api/machines/{machine}/sessions/{session_id}/artifacts/{relative_path:path}")
def artifact(machine: str, session_id: str, relative_path: str):
    path = _not_found(lambda: index.artifact(machine, session_id, relative_path))
    return FileResponse(path)
