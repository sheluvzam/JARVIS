"""Central tunables for the mind-viz mock backend.

These are echoed into the skeleton response's `config` block so a
threshold/top-k misconfiguration is visible in the payload, not hidden in
code.
"""

# Cosine-similarity edge wiring (Tier 5).
SIMILARITY_THRESHOLD = 0.35
SIMILARITY_TOP_K = 3

# Mock dataset shape (Tier 5).
MOCK_SEED = 42
EMBEDDING_DIM = 32
AGENT_COUNT = 6
CENTRAL_TOOL_COUNT = 18
TOOLS_PER_AGENT = (2, 5)  # inclusive random range
MEMORY_COUNT = 320

# Serving cap: intentionally lower than MEMORY_COUNT so `memories_shown`
# vs `memories_total` can demonstrate real truncation in the HUD.
MAX_MEMORIES_SERVED = 300

# WebSocket live-event simulator (Tier 6).
EVENT_INTERVAL_SECONDS = (3.0, 9.0)  # inclusive random range
WS_SEND_TIMEOUT_SECONDS = 2.0
WS_RECEIVE_TIMEOUT_SECONDS = 5.0
WS_QUEUE_MAXSIZE = 50
WS_PRUNE_INTERVAL_SECONDS = 15.0
WS_STALE_AFTER_SECONDS = 60.0
WS_HEARTBEAT_INTERVAL_SECONDS = 20.0
