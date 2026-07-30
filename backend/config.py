"""Central tunables for the mind-viz backend, both mock and real modes.

These are echoed into the skeleton response's `config` block so a
threshold/top-k misconfiguration is visible in the payload, not hidden in
code.
"""
import os
from pathlib import Path

# Which store/agent backend to run: "real" (Claude Agent SDK + SQLite +
# real embeddings) or "mock" (synthetic MockMindStore, no API key needed —
# useful for frontend regression checks without burning tokens).
JARVIS_MODE = os.environ.get("JARVIS_MODE", "real")

# Cosine-similarity edge wiring for the mock store (Tier 5). Renamed from
# the original SIMILARITY_THRESHOLD/SIMILARITY_TOP_K so retuning the real
# store's constants below can never silently affect the tested mock path.
MOCK_SIMILARITY_THRESHOLD = 0.35
MOCK_SIMILARITY_TOP_K = 3

# Cosine-similarity edge wiring for the real store. Retuned against actual
# remembered content (see backend/embeddings.py's docstring for the
# measurements that motivated switching to a char-n-gram analyzer): a real
# topic cluster measured 0.14-0.22, real noise measured 0.03-0.04, so 0.13
# separates them with margin on both sides. See backend/memory_store.py's
# docstring for the diagnostic heuristic if this needs adjusting again
# (hairball = too low, dust = too high).
REAL_SIMILARITY_THRESHOLD = 0.13
REAL_SIMILARITY_TOP_K = 3

# Real memory store + agent sandbox (real mode only).
DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "jarvis.db"
SANDBOX_DIR = DATA_DIR / "sandbox"

# Embeddings (real mode). HashingVectorizer needs no fitting/model
# download — n_features is the fixed output dimensionality.
REAL_EMBEDDING_DIM = 1024

# Mock dataset shape (Tier 5).
MOCK_SEED = 42
MOCK_EMBEDDING_DIM = 32
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

# Desktop companion (real mode only). The token is generated once and
# persisted here (gitignored, like jarvis.db) rather than passed via env,
# so a restart doesn't silently invalidate an already-paired companion.
COMPANION_TOKEN_PATH = DATA_DIR / "companion_token.txt"
COMPANION_PAIRING_TIMEOUT_SECONDS = 10.0
COMPANION_COMMAND_TIMEOUT_SECONDS = 30.0
