"""FastAPI app: serves the mind-viz + chat static frontends plus their
REST/WS API.

Single process, single origin (static mounted under /mind and /chat, API
under /api and /ws) — no CORS middleware needed.

JARVIS_MODE (backend/config.py, env `JARVIS_MODE`) selects real vs. mock:
- "real" (default): RealMindStore (SQLite + real embeddings) + the actual
  Claude Agent SDK agent core. Needs ANTHROPIC_API_KEY set (or, in a Claude
  Code dev sandbox, ambient session auth).
- "mock": MockMindStore + the synthetic live_event_simulator, exactly as
  before — no API key needed, useful for frontend regression checks. No
  chat WS in this mode.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.agent_core import AgentSession, build_claude_agent_options
from backend.events import live_event_simulator
from backend.mock_store import MockMindStore
from backend.schemas import SkeletonResponse
from backend.ws_manager import ConnectionManager

STATIC_MIND_DIR = Path(__file__).resolve().parent.parent / "static" / "mind"
STATIC_CHAT_DIR = Path(__file__).resolve().parent.parent / "static" / "chat"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ws_manager = ConnectionManager()
    app.state.ws_manager.start()
    app.state.agent_options = None
    event_task = None

    if config.JARVIS_MODE == "mock":
        # Constructed exactly once — the REST handler and the WS live-event
        # simulator both read/write this one instance, so a new memory's
        # edges are always visible to whatever serves the next read.
        app.state.mind_store = MockMindStore()
        event_task = asyncio.create_task(live_event_simulator(app.state.mind_store, app.state.ws_manager))
    else:
        # Fail loudly at startup if embeddings are broken, not silently on
        # someone's first chat message.
        from backend import embeddings

        embeddings.warmup()
        from backend.memory_store import RealMindStore

        app.state.mind_store = RealMindStore(config.DB_PATH, embeddings)
        app.state.agent_options = build_claude_agent_options(
            app.state.mind_store, app.state.ws_manager, config.SANDBOX_DIR
        )

    try:
        yield
    finally:
        if event_task is not None:
            event_task.cancel()
        await app.state.ws_manager.stop()


app = FastAPI(title="jarvis", lifespan=lifespan)


@app.get("/api/mind/skeleton", response_model=SkeletonResponse)
def get_skeleton() -> dict:
    return app.state.mind_store.get_skeleton()


@app.websocket("/ws/mind/live")
async def ws_mind_live(websocket: WebSocket):
    manager: ConnectionManager = app.state.ws_manager
    key = await manager.connect(websocket)
    manager.send_to(key, {"type": "hello", "server_time": datetime.now(timezone.utc).isoformat()})
    await manager.run_receive_loop(key, websocket)


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    # Not routed through ConnectionManager — that class is built for
    # read-only fan-out broadcast to potentially many observers. Chat is
    # 1:1, bidirectional, sequential-turn, a mismatch for that abstraction.
    await websocket.accept()

    if app.state.agent_options is None:
        await websocket.send_json({"type": "error", "message": "chat is unavailable in mock mode"})
        await websocket.close()
        return

    session = AgentSession(app.state.agent_options)
    try:
        await session.connect()
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"failed to start agent session: {exc}"})
        await websocket.close()
        return

    busy = False
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            frame = await websocket.receive_json()
            if frame.get("type") != "user_message":
                continue
            if busy:
                await websocket.send_json({"type": "error", "message": "a turn is already in progress"})
                continue

            busy = True
            try:
                async for message in session.send(frame.get("text", "")):
                    if isinstance(message, AssistantMessage):
                        text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
                        if text:
                            await websocket.send_json({"type": "assistant_message", "text": text})
                    elif isinstance(message, ResultMessage):
                        await websocket.send_json({"type": "turn_complete"})
            except Exception as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
            finally:
                busy = False
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()


# Static frontends last — their catch-alls (html=True) must not shadow API
# routes.
app.mount("/mind", StaticFiles(directory=STATIC_MIND_DIR, html=True), name="mind-static")
app.mount("/chat", StaticFiles(directory=STATIC_CHAT_DIR, html=True), name="chat-static")
