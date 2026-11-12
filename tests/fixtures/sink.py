"""Local fake destination for manual and end-to-end checks.

Run with:
    uv run uvicorn tests.fixtures.sink:app --port 9099

SINK_MODE=fail returns 500, SINK_MODE=reject returns 400; default returns 200.
Every request is appended to SINK_LOG (JSONL) and kept in memory for GET /requests.
"""

import asyncio
import json
import os
import time
from typing import Any

from fastapi import FastAPI, Request, Response

app = FastAPI(title="HookRelay test sink")

_requests: list[dict[str, Any]] = []


def _append_log(path: str, entry: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


@app.post("/hook")
async def hook(request: Request, response: Response) -> dict[str, Any]:
    raw = await request.body()
    entry = {
        "ts": time.time(),
        "headers": dict(request.headers),
        "body": raw.decode("utf-8", errors="replace"),
    }
    _requests.append(entry)

    log_path = os.environ.get("SINK_LOG")
    if log_path:
        await asyncio.to_thread(_append_log, log_path, entry)

    mode = os.environ.get("SINK_MODE", "ok")
    if mode == "fail":
        response.status_code = 500
        return {"error": "down"}
    if mode == "reject":
        response.status_code = 400
        return {"error": "bad request"}

    return {"ok": True, "count": len(_requests)}


@app.get("/requests")
async def list_requests() -> list[dict[str, Any]]:
    return _requests


@app.delete("/requests")
async def clear_requests() -> dict[str, bool]:
    _requests.clear()
    return {"ok": True}
