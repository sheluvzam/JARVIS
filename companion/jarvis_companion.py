"""JARVIS desktop companion — runs on your own Windows machine, dials out
to the JARVIS backend, and executes the commands its Desktop sub-agent
sends: screenshots, file reads, shell commands, mouse/keyboard control.

This process IS the real access boundary. There's no confirmation step on
the backend side before a command runs (see README.md) — closing this
process is the only kill switch. Keep the terminal window it's running in
visible so you can see what it's doing and stop it if something looks
wrong.

Setup: pip install -r requirements.txt, copy config.example.json to
config.json, fill in backend_url and token (the token is printed by the
JARVIS backend at startup), then run: python jarvis_companion.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path

import mss
import pyautogui
import websockets

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
RECONNECT_DELAY_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 25


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"Missing {CONFIG_PATH} — copy config.example.json to config.json and fill it in.")
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text())


def _get_active_window_info() -> dict:
    title = ""
    app = None
    try:
        import pygetwindow

        window = pygetwindow.getActiveWindow()
        if window is not None:
            title = window.title
    except Exception:
        pass
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app = psutil.Process(pid).name()
    except Exception:
        pass
    return {"title": title, "app": app}


def _screenshot() -> dict:
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])
        png_bytes = mss.tools.to_png(raw.rgb, raw.size)
    return {"image_base64": base64.b64encode(png_bytes).decode("ascii"), "mime_type": "image/png"}


def _list_files(path: str) -> dict:
    entries = []
    for child in sorted(Path(path).iterdir()):
        kind = "DIR " if child.is_dir() else "FILE"
        size = "" if child.is_dir() else f" ({child.stat().st_size} bytes)"
        entries.append(f"{kind} {child.name}{size}")
    return {"entries": entries}


def _read_file(path: str) -> dict:
    return {"content": Path(path).read_text(encoding="utf-8", errors="replace")}


def _run_command(command: str) -> dict:
    proc = subprocess.run(
        ["cmd", "/c", command], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
    )
    return {"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode}


def _move_mouse(x: int, y: int) -> dict:
    pyautogui.moveTo(x, y)
    return {}


def _click(x: int, y: int, button: str = "left") -> dict:
    pyautogui.click(x=x, y=y, button=button)
    return {}


def _type_text(text: str) -> dict:
    pyautogui.write(text)
    return {}


def _key_press(key: str) -> dict:
    keys = [k.strip() for k in key.split("+")]
    if len(keys) > 1:
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(keys[0])
    return {}


# Every action the backend can dispatch — one entry per jarvis_desktop MCP
# tool in backend/agent_core.py. Kept as plain sync functions and run via
# asyncio.to_thread below, since mss/pyautogui/subprocess are all blocking.
ACTIONS = {
    "screenshot": lambda args: _screenshot(),
    "get_active_window": lambda args: _get_active_window_info(),
    "list_files": lambda args: _list_files(args["path"]),
    "read_file": lambda args: _read_file(args["path"]),
    "run_command": lambda args: _run_command(args["command"]),
    "move_mouse": lambda args: _move_mouse(args["x"], args["y"]),
    "click": lambda args: _click(args["x"], args["y"], args.get("button", "left")),
    "type_text": lambda args: _type_text(args["text"]),
    "key_press": lambda args: _key_press(args["key"]),
}


async def handle_command(ws, frame: dict) -> None:
    request_id = frame["request_id"]
    action = frame["action"]
    args = frame.get("args", {})
    handler = ACTIONS.get(action)
    if handler is None:
        await ws.send(json.dumps({"type": "result", "request_id": request_id, "ok": False, "error": f"unknown action: {action}"}))
        return

    print(f"[jarvis-companion] executing: {action}({args})")
    try:
        data = await asyncio.to_thread(handler, args)
        await ws.send(json.dumps({"type": "result", "request_id": request_id, "ok": True, "data": data}))
    except Exception as exc:
        print(f"[jarvis-companion] {action} failed: {exc}")
        await ws.send(json.dumps({"type": "result", "request_id": request_id, "ok": False, "error": str(exc)}))


async def run_once(config: dict) -> None:
    async with websockets.connect(config["backend_url"]) as ws:
        await ws.send(json.dumps({"type": "pair", "token": config["token"]}))
        reply = json.loads(await ws.recv())
        if reply.get("type") != "paired":
            print(f"[jarvis-companion] pairing failed: {reply.get('message', reply)}")
            return
        print("[jarvis-companion] paired with backend — waiting for commands. Ctrl+C to stop.")

        async for raw in ws:
            frame = json.loads(raw)
            if frame.get("type") == "command":
                asyncio.create_task(handle_command(ws, frame))


async def main() -> None:
    config = load_config()
    while True:
        try:
            await run_once(config)
        except (websockets.ConnectionClosed, OSError) as exc:
            print(f"[jarvis-companion] connection lost ({exc}), retrying in {RECONNECT_DELAY_SECONDS}s…")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[jarvis-companion] stopped.")
