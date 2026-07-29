"""Real, honest agent/tool roster — every entry here corresponds to a
capability that actually works, not a fictional catalog (contrast
`mock_store.py`'s synthetic 6-agent/20-tool roster). This is the single
source of truth consumed both by `memory_store.py` (serves it as
AgentNodeDTO/ToolNodeDTO rows) and `agent_core.py` (builds the matching
`ClaudeAgentOptions.agents` from it) — the visualized roster and the real
one can never drift apart.

Agent dict `id`s double as the sub-agent's SDK name (what `agent_type`
reports on hook events — confirmed via `claude_agent_sdk.types
.SubagentStartHookInput`/`PreToolUseHookInput` both carrying `agent_id`/
`agent_type` fields directly), so hook-driven status attribution in
`agent_core.py` needs no separate translation table.
"""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition

AGENTS: list[dict] = [
    {
        "id": "agent-research",
        "label": "Research Agent",
        "role": "Research",
        "tool_ids": ["tool-research-websearch", "tool-research-webfetch"],
    },
    {
        "id": "agent-coding",
        "label": "Coding Agent",
        "role": "Coding",
        "tool_ids": [
            "tool-coding-read",
            "tool-coding-write",
            "tool-coding-edit",
            "tool-coding-bash",
            "tool-coding-glob",
            "tool-coding-grep",
        ],
    },
    {
        "id": "agent-memory",
        "label": "Memory Agent",
        "role": "Memory",
        "tool_ids": ["tool-memory-remember", "tool-memory-recall"],
    },
]

# `sdk_tool_name` is what goes into ClaudeAgentOptions/AgentDefinition
# `tools=[...]`; `label` is the short display name the mind-viz shows.
TOOLS: list[dict] = [
    {
        "id": "tool-research-websearch",
        "label": "WebSearch",
        "category": "research",
        "owner_agent_id": "agent-research",
        "sdk_tool_name": "WebSearch",
    },
    {
        "id": "tool-research-webfetch",
        "label": "WebFetch",
        "category": "research",
        "owner_agent_id": "agent-research",
        "sdk_tool_name": "WebFetch",
    },
    {
        "id": "tool-coding-read",
        "label": "Read",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Read",
    },
    {
        "id": "tool-coding-write",
        "label": "Write",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Write",
    },
    {
        "id": "tool-coding-edit",
        "label": "Edit",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Edit",
    },
    {
        "id": "tool-coding-bash",
        "label": "Bash",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Bash",
    },
    {
        "id": "tool-coding-glob",
        "label": "Glob",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Glob",
    },
    {
        "id": "tool-coding-grep",
        "label": "Grep",
        "category": "execution",
        "owner_agent_id": "agent-coding",
        "sdk_tool_name": "Grep",
    },
    {
        "id": "tool-memory-remember",
        "label": "remember",
        "category": "memory",
        "owner_agent_id": "agent-memory",
        "sdk_tool_name": "mcp__jarvis_memory__remember",
    },
    {
        "id": "tool-memory-recall",
        "label": "recall",
        "category": "memory",
        "owner_agent_id": "agent-memory",
        "sdk_tool_name": "mcp__jarvis_memory__recall",
    },
]

TOOLS_BY_ID: dict[str, dict] = {t["id"]: t for t in TOOLS}

# SDK tool name -> our tool_id, for hook-driven usage counting. Derived
# from TOOLS, not hand-duplicated, so it can't drift out of sync.
SDK_TOOL_NAME_TO_TOOL_ID: dict[str, str] = {t["sdk_tool_name"]: t["id"] for t in TOOLS}

# The top-level orchestrator only delegates via the SDK's built-in "Agent"
# tool (confirmed by observation — not "Task", which showed up in an
# unrelated session's default tool list and turned out not to be what a
# custom `agents=` roster actually uses) — it never calls a real tool
# directly. Combined with SubagentStart/SubagentStop hooks (which carry
# agent_id/agent_type natively), this makes every real tool call
# attributable to exactly one sub-agent.
#
# Every tool a sub-agent can reach must ALSO be in the top-level
# `allowed_tools` — confirmed by observation: `AgentDefinition.tools` alone
# scopes *which* allowed tools a sub-agent may use, it doesn't itself grant
# the permission. Tools listed here skip the interactive permission prompt
# entirely (needed since `permission_mode="bypassPermissions"` is refused
# by the CLI when running as root, which this sandbox does, and
# `acceptEdits` alone doesn't cover custom MCP tools).
TOP_LEVEL_ALLOWED_TOOLS = ["Agent"] + [t["sdk_tool_name"] for t in TOOLS]

_AGENT_PROMPTS = {
    "agent-research": (
        "You are JARVIS's research sub-agent. Use WebSearch/WebFetch to find "
        "and verify information, then hand back a concise, sourced summary."
    ),
    "agent-coding": (
        "You are JARVIS's coding sub-agent. Read, write, and run code inside "
        "your sandboxed working directory. Explain what you changed and why."
    ),
    "agent-memory": (
        "You are JARVIS's memory. Call `remember` for anything durable the "
        "user tells you — a preference, a fact, a decision — and `recall` to "
        "search past memories before answering questions about what's been "
        "said before. Don't remember routine chit-chat. "
        "This is a single-user store — never include the user's name, "
        "email, or any 'User (email: ...) ...' framing in the content you "
        "store, even if the delegation prompt you were given includes it. "
        "Write just the fact itself, tersely (e.g. 'Prefers pytest over "
        "unittest for testing', not 'User (email: x@y.com) prefers "
        "pytest...'). Boilerplate like that is identical across every "
        "memory and swamps real topical similarity, making unrelated "
        "memories look falsely related."
    ),
}

_AGENT_DESCRIPTIONS = {
    "agent-research": "Finds and verifies information on the web.",
    "agent-coding": "Reads, writes, and runs code in a sandboxed directory.",
    "agent-memory": "Stores and recalls JARVIS's durable memories.",
}


def build_agent_definitions() -> dict[str, AgentDefinition]:
    definitions: dict[str, AgentDefinition] = {}
    for agent in AGENTS:
        sdk_tool_names = [TOOLS_BY_ID[tool_id]["sdk_tool_name"] for tool_id in agent["tool_ids"]]
        definitions[agent["id"]] = AgentDefinition(
            description=_AGENT_DESCRIPTIONS[agent["id"]],
            prompt=_AGENT_PROMPTS[agent["id"]],
            tools=sdk_tool_names,
        )
    return definitions
