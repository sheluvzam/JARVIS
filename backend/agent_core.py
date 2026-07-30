"""Claude Agent SDK wiring: the `remember`/`recall` MCP tools, the
`ClaudeAgentOptions` that ties the real roster (`agent_roster.py`) to real
tools, hook-driven `agent_status`/tool-usage tracking, and a thin
per-connection session wrapper for the chat WebSocket.

Confirmed against the installed `claude-agent-sdk` (0.2.128) rather than
guessed: `SubagentStart`/`SubagentStop`/`PreToolUse`/`PostToolUse` hook
inputs all carry `agent_id`/`agent_type` directly, and sub-agent delegation
goes through the SDK's built-in `Agent` tool (not `Task`, which showed up
in an unrelated session's default tool list and turned out not to be what
a custom `agents=` roster actually uses — see agent_roster.py's
TOP_LEVEL_ALLOWED_TOOLS comment).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    create_sdk_mcp_server,
    tool,
)

from backend import agent_roster
from backend.companion import CompanionBridge, CompanionError

# Default per-broadcast activity level while a sub-agent is working — the
# real signal is *which* agent lit up and when, not a precise magnitude.
ACTIVE_LEVEL = 0.85
IDLE_LEVEL = 0.05


def _jsonable_memory(memory: dict) -> dict:
    return {**memory, "created_at": memory["created_at"].isoformat()}


def build_memory_mcp_server(mind_store, ws_manager):
    """The Memory sub-agent's real tools — these are what actually write to
    and read from RealMindStore, and what actually broadcast the WS events
    the mind-viz page reacts to live."""

    @tool("remember", "Store a durable memory — a fact, preference, or decision — for later recall.", {"content": str, "tags": list})
    async def _remember(args: dict) -> dict:
        content = args["content"]
        tags = [str(t) for t in (args.get("tags") or [])]
        memory, edges = await mind_store.remember(content, tags=tags)
        ws_manager.broadcast(
            {"type": "memory_created", "memory": _jsonable_memory(memory), "similarity_edges": edges}
        )
        return {"content": [{"type": "text", "text": f"Remembered: {memory['content_preview']}"}]}

    @tool("recall", "Search past memories by semantic similarity to a query.", {"query": str, "top_k": int})
    async def _recall(args: dict) -> dict:
        query = args["query"]
        top_k = int(args.get("top_k") or 5)
        results = await mind_store.recall(query, top_k=top_k)
        for memory, score in results:
            ws_manager.broadcast(
                {
                    "type": "memory_recalled",
                    "event_id": uuid.uuid4().hex,
                    "memory_id": memory["id"],
                    "strength": score,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        if not results:
            text = "No relevant memories found."
        else:
            text = "\n".join(f"- {m['content_preview']} (similarity {s:.2f})" for m, s in results)
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(name="jarvis_memory", version="1.0.0", tools=[_remember, _recall])


MAX_FILE_CONTENT_CHARS = 20_000


def _text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def build_desktop_mcp_server(companion: CompanionBridge):
    """The Desktop sub-agent's real tools — each one dispatches, over
    CompanionBridge, to whatever local program is currently paired (see
    companion/). If nothing is paired, every call below fails the same
    honest way: a clear 'no companion device is connected' text result,
    not a hang or a crash."""

    async def _dispatch(action: str, args: dict) -> dict:
        try:
            return await companion.send_command(action, args)
        except CompanionError as exc:
            return {"ok": False, "data": None, "error": str(exc)}

    @tool("screenshot", "Capture the current screen.", {})
    async def _screenshot(args: dict) -> dict:
        result = await _dispatch("screenshot", {})
        if not result["ok"]:
            return _text_result(f"Screenshot failed: {result['error']}")
        data = result["data"]
        return {
            "content": [
                {"type": "image", "data": data["image_base64"], "mimeType": data.get("mime_type", "image/png")}
            ]
        }

    @tool("get_active_window", "Get the title and application of the currently focused window.", {})
    async def _get_active_window(args: dict) -> dict:
        result = await _dispatch("get_active_window", {})
        if not result["ok"]:
            return _text_result(f"get_active_window failed: {result['error']}")
        data = result["data"]
        return _text_result(f"Active window: \"{data.get('title', '')}\" ({data.get('app', 'unknown app')})")

    @tool("list_files", "List files and folders at a path on the user's machine.", {"path": str})
    async def _list_files(args: dict) -> dict:
        result = await _dispatch("list_files", {"path": args["path"]})
        if not result["ok"]:
            return _text_result(f"list_files failed: {result['error']}")
        entries = result["data"].get("entries", [])
        if not entries:
            return _text_result(f"No entries found at {args['path']}.")
        return _text_result("\n".join(entries))

    @tool("read_file", "Read a text file's contents from the user's machine.", {"path": str})
    async def _read_file(args: dict) -> dict:
        result = await _dispatch("read_file", {"path": args["path"]})
        if not result["ok"]:
            return _text_result(f"read_file failed: {result['error']}")
        content = result["data"].get("content", "")
        if len(content) > MAX_FILE_CONTENT_CHARS:
            content = content[:MAX_FILE_CONTENT_CHARS] + f"\n… truncated ({len(content)} chars total)"
        return _text_result(content)

    @tool("run_command", "Run a shell command on the user's machine (also how to launch applications).", {"command": str})
    async def _run_command(args: dict) -> dict:
        result = await _dispatch("run_command", {"command": args["command"]})
        if not result["ok"]:
            return _text_result(f"run_command failed: {result['error']}")
        data = result["data"]
        return _text_result(
            f"exit code: {data.get('exit_code')}\nstdout:\n{data.get('stdout', '')}\nstderr:\n{data.get('stderr', '')}"
        )

    @tool("move_mouse", "Move the mouse cursor to screen coordinates.", {"x": int, "y": int})
    async def _move_mouse(args: dict) -> dict:
        result = await _dispatch("move_mouse", {"x": args["x"], "y": args["y"]})
        if not result["ok"]:
            return _text_result(f"move_mouse failed: {result['error']}")
        return _text_result(f"Moved mouse to ({args['x']}, {args['y']}).")

    @tool("click", "Click the mouse at screen coordinates.", {"x": int, "y": int, "button": str})
    async def _click(args: dict) -> dict:
        button = args.get("button") or "left"
        result = await _dispatch("click", {"x": args["x"], "y": args["y"], "button": button})
        if not result["ok"]:
            return _text_result(f"click failed: {result['error']}")
        return _text_result(f"Clicked {button} button at ({args['x']}, {args['y']}).")

    @tool("type_text", "Type text at the current cursor/focus position.", {"text": str})
    async def _type_text(args: dict) -> dict:
        result = await _dispatch("type_text", {"text": args["text"]})
        if not result["ok"]:
            return _text_result(f"type_text failed: {result['error']}")
        return _text_result(f"Typed {len(args['text'])} characters.")

    @tool("key_press", "Press a keyboard key or key combination (e.g. 'enter', 'ctrl+c').", {"key": str})
    async def _key_press(args: dict) -> dict:
        result = await _dispatch("key_press", {"key": args["key"]})
        if not result["ok"]:
            return _text_result(f"key_press failed: {result['error']}")
        return _text_result(f"Pressed {args['key']}.")

    return create_sdk_mcp_server(
        name="jarvis_desktop",
        version="1.0.0",
        tools=[
            _screenshot,
            _get_active_window,
            _list_files,
            _read_file,
            _run_command,
            _move_mouse,
            _click,
            _type_text,
            _key_press,
        ],
    )


def build_hooks(mind_store, ws_manager) -> dict[str, list[HookMatcher]]:
    async def _on_subagent_start(input_data, tool_use_id, context):
        agent_id = input_data["agent_type"]
        mind_store.set_agent_status(agent_id, "active", ACTIVE_LEVEL)
        ws_manager.broadcast({"type": "agent_status", "agent_id": agent_id, "status": "active", "activity_level": ACTIVE_LEVEL})
        return {}

    async def _on_subagent_stop(input_data, tool_use_id, context):
        agent_id = input_data["agent_type"]
        mind_store.set_agent_status(agent_id, "idle", IDLE_LEVEL)
        ws_manager.broadcast({"type": "agent_status", "agent_id": agent_id, "status": "idle", "activity_level": IDLE_LEVEL})
        return {}

    async def _on_tool_use(input_data, tool_use_id, context):
        # Registered for both PostToolUse and PostToolUseFailure — usage
        # counting should reflect that a tool was actually invoked
        # regardless of whether the call succeeded (confirmed missing during
        # testing: WebFetch calls that errored/retried weren't counted when
        # this only listened on PostToolUse).
        tool_id = agent_roster.SDK_TOOL_NAME_TO_TOOL_ID.get(input_data["tool_name"])
        if tool_id:
            mind_store.record_tool_use(tool_id)
        return {}

    return {
        "SubagentStart": [HookMatcher(matcher=None, hooks=[_on_subagent_start])],
        "SubagentStop": [HookMatcher(matcher=None, hooks=[_on_subagent_stop])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[_on_tool_use])],
        "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[_on_tool_use])],
    }


def build_claude_agent_options(
    mind_store, ws_manager, workspace_dir: Path, companion: CompanionBridge
) -> ClaudeAgentOptions:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    memory_server = build_memory_mcp_server(mind_store, ws_manager)
    desktop_server = build_desktop_mcp_server(companion)
    return ClaudeAgentOptions(
        mcp_servers={"jarvis_memory": memory_server, "jarvis_desktop": desktop_server},
        agents=agent_roster.build_agent_definitions(),
        allowed_tools=agent_roster.TOP_LEVEL_ALLOWED_TOOLS,
        cwd=str(workspace_dir),
        system_prompt=(
            "You are JARVIS. You orchestrate four sub-agents — Research, "
            "Coding, Memory, and Desktop — and have no tools of your own "
            "besides the Agent tool, which delegates to them. Delegate real "
            "work to the appropriate sub-agent rather than attempting it "
            "yourself. "
            "Critical rule: if the user asks you to remember, note, or keep "
            "track of anything, you MUST delegate to the Memory sub-agent via "
            "the Agent tool before replying — never just reply as if you "
            "remembered something without actually calling Memory to store "
            "it. A confirmation you didn't earn by actually storing the fact "
            "is a lie to the user. Likewise check Memory for relevant context "
            "before answering questions about what's been said before. "
            "Research already saves its own durable findings to memory "
            "directly — don't also delegate a research summary to Memory "
            "afterward, that would store it twice. "
            "Desktop controls the user's real, physical computer through a "
            "companion device — not a sandbox. Only delegate to it for "
            "things that actually require touching their screen, files, or "
            "OS; if a companion device isn't connected it will tell you so, "
            "and you should relay that plainly rather than pretending the "
            "action happened."
        ),
        hooks=build_hooks(mind_store, ws_manager),
        # "bypassPermissions" (--dangerously-skip-permissions) is refused by
        # the CLI when running as root, which this sandbox does — confirmed
        # by hitting that exact refusal. "acceptEdits" plus every tool this
        # roster uses already being in `allowed_tools` (agent_roster.py) is
        # what actually avoids interactive prompting in practice; this is a
        # demo-grade choice (no approval UI exists in this chat surface),
        # same spirit as the sandboxed cwd in agent_roster.py, not a
        # production trust boundary.
        permission_mode="acceptEdits",
    )


class AgentSession:
    """Thin wrapper around ClaudeSDKClient — isolates the WS route in
    app.py from the SDK's exact multi-turn session shape. One instance per
    chat WebSocket connection, built fresh from the shared
    ClaudeAgentOptions so every session has the same roster/tools/hooks but
    its own conversation state."""

    def __init__(self, options: ClaudeAgentOptions):
        self._client = ClaudeSDKClient(options=options)

    async def connect(self) -> None:
        await self._client.connect()

    async def send(self, text: str):
        await self._client.query(text)
        async for message in self._client.receive_response():
            yield message

    async def close(self) -> None:
        await self._client.disconnect()
