"""Unit tests for backend/companion.py's CompanionBridge — pure asyncio
request/response matching logic, no real socket involved (a `_FakeSocket`
stub stands in for the WebSocket, matching how the real jarvis_desktop MCP
tools only ever call `.send_command()`, never touch the socket directly).

No pytest-asyncio dependency: each test drives its own coroutine with
asyncio.run() to keep the CI dependency list minimal.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.companion import CompanionBridge, CompanionError, get_or_create_token


class _FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


def test_check_token_matches_and_rejects():
    bridge = CompanionBridge(token="correct-token")
    assert bridge.check_token("correct-token") is True
    assert bridge.check_token("wrong-token") is False


def test_send_command_without_companion_raises():
    async def scenario():
        bridge = CompanionBridge(token="t")
        with pytest.raises(CompanionError, match="no companion device is connected"):
            await bridge.send_command("screenshot", {})

    asyncio.run(scenario())


def test_send_command_resolves_on_matching_result():
    async def scenario():
        bridge = CompanionBridge(token="t")
        socket = _FakeSocket()
        bridge.attach(socket)

        async def respond_shortly():
            await asyncio.sleep(0)  # let send_command's frame land in socket.sent
            request_id = socket.sent[0]["request_id"]
            bridge.handle_result(request_id, ok=True, data={"path": "C:\\Users\\me"}, error=None)

        responder = asyncio.create_task(respond_shortly())
        result = await bridge.send_command("list_files", {"path": "C:\\Users\\me"}, timeout=2.0)
        await responder

        assert result == {"ok": True, "data": {"path": "C:\\Users\\me"}, "error": None}
        assert socket.sent[0]["action"] == "list_files"
        assert socket.sent[0]["args"] == {"path": "C:\\Users\\me"}

    asyncio.run(scenario())


def test_send_command_times_out_when_no_reply():
    async def scenario():
        bridge = CompanionBridge(token="t")
        bridge.attach(_FakeSocket())  # connected, but never replies
        with pytest.raises(CompanionError, match="did not respond"):
            await bridge.send_command("screenshot", {}, timeout=0.05)

    asyncio.run(scenario())


def test_pending_request_cleaned_up_after_timeout():
    async def scenario():
        bridge = CompanionBridge(token="t")
        bridge.attach(_FakeSocket())
        with pytest.raises(CompanionError):
            await bridge.send_command("screenshot", {}, timeout=0.05)
        assert bridge._pending == {}

    asyncio.run(scenario())


def test_detach_fails_pending_commands_immediately():
    async def scenario():
        bridge = CompanionBridge(token="t")
        socket = _FakeSocket()
        bridge.attach(socket)

        async def detach_shortly():
            await asyncio.sleep(0)
            bridge.detach(socket)

        detacher = asyncio.create_task(detach_shortly())
        with pytest.raises(CompanionError, match="disconnected mid-command"):
            await bridge.send_command("click", {"x": 1, "y": 1}, timeout=5.0)
        await detacher

    asyncio.run(scenario())


def test_detach_by_stale_socket_is_a_no_op():
    bridge = CompanionBridge(token="t")
    first = _FakeSocket()
    second = _FakeSocket()
    bridge.attach(first)
    bridge.attach(second)  # a new pairing replaced the old connection
    bridge.detach(first)  # stale reference to the old socket
    assert bridge.connected is True  # second connection must be unaffected


def test_handle_result_ignores_unknown_request_id():
    bridge = CompanionBridge(token="t")
    bridge.handle_result("no-such-request", ok=True, data=None, error=None)  # must not raise


def test_get_or_create_token_is_stable_across_calls(tmp_path, monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "COMPANION_TOKEN_PATH", tmp_path / "companion_token.txt")

    first = get_or_create_token()
    second = get_or_create_token()
    assert first == second
    assert len(first) > 20
