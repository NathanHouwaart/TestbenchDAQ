"""Docker-hosted, read-only HTTP API for TestbenchDAQ session data."""
from __future__ import annotations

import os
import io
import queue
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

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


@app.get("/api/machines/{machine}/sessions/{session_id}/plot/{relative_path:path}")
def plot(machine: str, session_id: str, relative_path: str, max_points: int = 2_000):
    return _not_found(lambda: index.plot(machine, session_id, relative_path, max_points))


class _QueueWriter(io.RawIOBase):
    def __init__(self, output: queue.Queue[bytes | None]) -> None:
        self.output = output

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:
        if data:
            self.output.put(data)
        return len(data)


@app.get("/api/machines/{machine}/sessions/{session_id}/download")
def download(machine: str, session_id: str):
    files = _not_found(lambda: index.archive_files(machine, session_id))
    output: queue.Queue[bytes | None] = queue.Queue(maxsize=8)

    def build_archive() -> None:
        try:
            with zipfile.ZipFile(_QueueWriter(output), "w", zipfile.ZIP_DEFLATED) as archive:
                for path, name in files:
                    archive.write(path, name)
        finally:
            output.put(None)

    threading.Thread(target=build_archive, daemon=True).start()

    def stream():
        while (chunk := output.get()) is not None:
            yield chunk

    safe_name = "".join(char if char.isalnum() or char in "-_." else "_" for char in session_id)
    return StreamingResponse(
        stream(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )
