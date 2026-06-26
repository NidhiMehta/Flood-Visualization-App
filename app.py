# -*- coding: utf-8 -*-
"""FloodWatch standalone web app — FastAPI + WebSocket, LangChain backend."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

AGENT_DIR = Path(__file__).parent
STATIC_DIR = AGENT_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
MAP_FILE = AGENT_DIR / "flood_map.html"
PYTHON_BIN = sys.executable

app = FastAPI(title="FloodWatch")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_MAP_PLACEHOLDER = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="margin:0;display:flex;align-items:center;justify-content:center;
             height:100vh;font-family:Arial,sans-serif;background:#eef2f7;color:#5a7a9a;">
  <div style="text-align:center">
    <div style="font-size:3rem;margin-bottom:12px">&#128167;</div>
    <p style="font-size:1.1rem;font-weight:600">No map yet</p>
    <p style="font-size:.9rem;margin-top:6px;opacity:.7">
      Ask FloodWatch to show you a flood map and it will appear here.
    </p>
  </div>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/map", response_class=HTMLResponse)
async def serve_map():
    if MAP_FILE.exists():
        return FileResponse(MAP_FILE, media_type="text/html",
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse(_MAP_PLACEHOLDER)


@app.get("/api/map-mtime")
async def map_mtime():
    if MAP_FILE.exists():
        return {"mtime": MAP_FILE.stat().st_mtime}
    return {"mtime": None}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "FLOODWATCH_WEB_MODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        PYTHON_BIN, "-u", str(AGENT_DIR / "main.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(AGENT_DIR),
        env=env,
    )
    done = asyncio.Event()

    async def pump_output() -> None:
        try:
            while not done.is_set():
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=120.0)
                except asyncio.TimeoutError:
                    continue
                if not raw:
                    done.set()
                    await websocket.send_json({"type": "done"})
                    break
                text = raw.decode(errors="replace")
                stripped = text.strip()
                if stripped == "__INPUT__:":
                    await websocket.send_json({"type": "waiting_for_input"})
                else:
                    await websocket.send_json({"type": "output", "text": text})
                    if "Map saved:" in text or "markers plotted" in text:
                        await websocket.send_json({"type": "map_updated"})
        except Exception:
            done.set()

    async def pump_input() -> None:
        try:
            while not done.is_set():
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    done.set()
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "exit":
                    done.set()
                    break
                proc.stdin.write((data.get("text", "") + "\n").encode())
                await proc.stdin.drain()
        except Exception:
            done.set()

    try:
        await asyncio.gather(pump_output(), pump_input())
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
