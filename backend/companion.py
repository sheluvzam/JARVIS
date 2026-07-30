"""Desktop companion bridge: request/response RPC over a single WebSocket
connection to a locally-run companion process (see companion/), the only
thing with real access to the user's screen, files, and OS — this backend
runs in its own cloud sandbox and can't reach a home machine directly, so
the companion dials out and this class dispatches commands to it.

Every dispatched command and its result is logged — this is the audit
trail that substitutes for per-action confirmation (the user explicitly
chose auto-approve; see agent_roster.py's agent-desktop prompt for the
honesty rule that goes with it).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import uuid

from fastapi import WebSocket

from backend import config

logger = logging.getLogger("jarvis.companion")


def get_or_create_token() -> str:
    """Generated once, persisted to disk — a restart must not silently
    invalidate an already-paired companion."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.COMPANION_TOKEN_PATH.exists():
        token = config.COMPANION_TOKEN_PATH.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    config.COMPANION_TOKEN_PATH.write_text(token)
    return token


class CompanionError(Exception):
    """No companion connected, or it didn't reply in time. Callers (the
    jarvis_desktop MCP tools) turn this into a plain-text tool result
    rather than letting it hang or crash the turn."""


class CompanionBridge:
    """Tracks at most one live companion connection — the user has one
    machine. A new pairing replaces whatever was connected before."""

    def __init__(self, token: str):
        self._token = token
        self._websocket: WebSocket | None = None
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return self._websocket is not None

    @property
    def token(self) -> str:
        return self._token

    def check_token(self, token: str) -> bool:
        return secrets.compare_digest(token, self._token)

    def attach(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    def detach(self, websocket: WebSocket) -> None:
        if self._websocket is not websocket:
            return  # a newer pairing already replaced this one
        self._websocket = None
        # Anything still waiting on this connection will never get a reply
        # now — fail it immediately instead of hanging until its timeout.
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CompanionError("companion disconnected mid-command"))
        self._pending.clear()

    async def send_command(
        self, action: str, args: dict, timeout: float = config.COMPANION_COMMAND_TIMEOUT_SECONDS
    ) -> dict:
        if self._websocket is None:
            raise CompanionError("no companion device is connected — desktop actions aren't available right now")

        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        logger.info("companion command dispatched: action=%s args=%s request_id=%s", action, args, request_id)
        try:
            await self._websocket.send_json(
                {"type": "command", "request_id": request_id, "action": action, "args": args}
            )
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info("companion command result: request_id=%s ok=%s", request_id, result.get("ok"))
            return result
        except asyncio.TimeoutError:
            raise CompanionError(f"companion did not respond to '{action}' within {timeout:.0f}s") from None
        finally:
            self._pending.pop(request_id, None)

    def handle_result(self, request_id: str, ok: bool, data: dict | None, error: str | None) -> None:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return  # stale, duplicate, or already-timed-out reply — ignore
        future.set_result({"ok": ok, "data": data, "error": error})
