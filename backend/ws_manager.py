"""Timeout-guarded, non-blocking WebSocket broadcast manager.

`broadcast()` never awaits a socket send itself — each connection gets its
own bounded queue and a dedicated sender task, so one slow or dead observer
can never block delivery to the rest, or block whatever triggered the
broadcast (the live-event simulator's main loop). This is the direct fix
for "an observer send is blocking the main path."
"""
from __future__ import annotations

import asyncio
import time

from fastapi import WebSocket, WebSocketDisconnect

from backend import config


class _Connection:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=config.WS_QUEUE_MAXSIZE)
        self.last_seen = time.monotonic()
        self.sender_task: asyncio.Task | None = None
        self.alive = True


class ConnectionManager:
    def __init__(self):
        self._connections: dict[int, _Connection] = {}
        self._background_tasks: list[asyncio.Task] = []

    def start(self):
        self._background_tasks = [
            asyncio.create_task(self._prune_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]

    async def stop(self):
        for task in self._background_tasks:
            task.cancel()
        for key in list(self._connections):
            await self._close(key)

    async def connect(self, websocket: WebSocket) -> int:
        await websocket.accept()
        key = id(websocket)
        self._connections[key] = _Connection(websocket)
        self._connections[key].sender_task = asyncio.create_task(self._sender_loop(key))
        return key

    def send_to(self, key: int, message: dict):
        conn = self._connections.get(key)
        if conn is None:
            return
        try:
            conn.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass

    def broadcast(self, message: dict):
        for conn in list(self._connections.values()):
            try:
                conn.queue.put_nowait(message)
            except asyncio.QueueFull:
                pass  # drop-new on a full queue rather than blocking or growing unbounded

    async def run_receive_loop(self, key: int, websocket: WebSocket):
        # This socket is read-only from the client's perspective — the
        # receive loop exists only to detect disconnects promptly, never to
        # accept client commands.
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=config.WS_RECEIVE_TIMEOUT_SECONDS)
                    conn = self._connections.get(key)
                    if conn:
                        conn.last_seen = time.monotonic()
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            await self._close(key)

    async def _sender_loop(self, key: int):
        conn = self._connections[key]
        while True:
            message = await conn.queue.get()
            try:
                await asyncio.wait_for(conn.websocket.send_json(message), timeout=config.WS_SEND_TIMEOUT_SECONDS)
                conn.last_seen = time.monotonic()
            except Exception:
                await self._close(key)
                return

    async def _prune_loop(self):
        while True:
            await asyncio.sleep(config.WS_PRUNE_INTERVAL_SECONDS)
            now = time.monotonic()
            for key, conn in list(self._connections.items()):
                if now - conn.last_seen > config.WS_STALE_AFTER_SECONDS:
                    await self._close(key)

    async def _heartbeat_loop(self):
        import datetime as _dt

        while True:
            await asyncio.sleep(config.WS_HEARTBEAT_INTERVAL_SECONDS)
            self.broadcast({"type": "heartbeat", "server_time": _dt.datetime.now(_dt.timezone.utc).isoformat()})

    async def _close(self, key: int):
        conn = self._connections.pop(key, None)
        if conn is None or not conn.alive:
            return
        conn.alive = False
        if conn.sender_task is not None and conn.sender_task is not asyncio.current_task():
            conn.sender_task.cancel()
        try:
            await conn.websocket.close()
        except Exception:
            pass
