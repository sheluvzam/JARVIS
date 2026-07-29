"""numpy cosine-similarity helpers backing the memory web's edges."""
from __future__ import annotations

import numpy as np


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def cosine_similarity_matrix(unit_vectors: np.ndarray) -> np.ndarray:
    return unit_vectors @ unit_vectors.T


def top_k_edges(
    sim_matrix: np.ndarray,
    ids: list[str],
    k: int,
    threshold: float,
) -> list[tuple[str, str, float]]:
    """Top-k neighbors per row above `threshold`, self-similarity excluded,
    mutual top-k pairs deduped (canonicalized as sorted id pair)."""
    n = sim_matrix.shape[0]
    matrix = sim_matrix.copy()
    np.fill_diagonal(matrix, -np.inf)

    seen: dict[tuple[str, str], float] = {}
    for i in range(n):
        row = matrix[i]
        top_indices = np.argsort(row)[::-1][:k]
        for j in top_indices:
            weight = float(row[j])
            if weight < threshold:
                continue
            pair = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
            seen[pair] = max(seen.get(pair, -1.0), weight)

    return [(a, b, w) for (a, b), w in seen.items()]


def edges_for_new_vector(
    new_unit: np.ndarray,
    existing_units: np.ndarray,
    existing_ids: list[str],
    new_id: str,
    k: int,
    threshold: float,
) -> list[tuple[str, str, float]]:
    """One-vs-many similarity for a freshly added vector, so a brand-new
    memory gets real edges immediately rather than waiting for a full
    recompute."""
    if existing_units.shape[0] == 0:
        return []
    sims = existing_units @ new_unit
    top_indices = np.argsort(sims)[::-1][:k]
    edges = []
    for j in top_indices:
        weight = float(sims[j])
        if weight < threshold:
            continue
        edges.append((new_id, existing_ids[j], weight))
    return edges
