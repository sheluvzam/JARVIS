"""FastAPI app: serves the mind-viz static frontend plus its REST/WS API.

Single process, single origin (static mounted under /mind, API under /api
and /ws) — no CORS middleware needed.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from backend.events import live_event_simulator
from backend.mock_store import MockMindStore
from backend.schemas import SkeletonResponse
from backend.ws_manager import ConnectionManager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "mind"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Constructed exactly once — the REST handler and the WS live-event
    # simulator both read/write this one instance, so a new memory's edges
    # are always visible to whatever serves the next read.
    app.state.mind_store = MockMindStore()
    app.state.ws_manager = ConnectionManager()
    app.state.ws_manager.start()
    event_task = asyncio.create_task(live_event_simulator(app.state.mind_store, app.state.ws_manager))
    try:
        yield
    finally:
        event_task.cancel()
        await app.state.ws_manager.stop()


app = FastAPI(title="mind-viz", lifespan=lifespan)


@app.get("/api/mind/skeleton", response_model=SkeletonResponse)
def get_skeleton() -> dict:
    return app.state.mind_store.get_skeleton()


@app.websocket("/ws/mind/live")
async def ws_mind_live(websocket: WebSocket):
    manager: ConnectionManager = app.state.ws_manager
    key = await manager.connect(websocket)
    manager.send_to(key, {"type": "hello", "server_time": datetime.now(timezone.utc).isoformat()})
    await manager.run_receive_loop(key, websocket)


# Static frontend last — its catch-all (html=True) must not shadow API routes.
app.mount("/mind", StaticFiles(directory=STATIC_DIR, html=True), name="mind-static")
