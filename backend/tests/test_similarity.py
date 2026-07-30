"""Unit tests for backend/similarity.py — the numpy cosine-similarity
helpers behind every edge shown in the mind-viz (mock and real alike).
"""
from __future__ import annotations

import numpy as np

from backend.similarity import cosine_similarity_matrix, edges_for_new_vector, l2_normalize, top_k_edges


def test_l2_normalize_unit_length():
    vectors = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    normalized = l2_normalize(vectors)
    norms = np.linalg.norm(normalized, axis=1)
    assert np.allclose(norms, 1.0)


def test_l2_normalize_zero_vector_does_not_divide_by_zero():
    vectors = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)
    normalized = l2_normalize(vectors)
    assert np.array_equal(normalized[0], np.array([0.0, 0.0], dtype=np.float32))
    assert np.all(np.isfinite(normalized))


def test_cosine_similarity_matrix_identical_vectors_score_one():
    unit = l2_normalize(np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    sim = cosine_similarity_matrix(unit)
    assert np.isclose(sim[0, 1], 1.0)
    assert np.isclose(sim[0, 2], 0.0, atol=1e-6)


def test_top_k_edges_excludes_self_similarity():
    ids = ["a", "b", "c"]
    sim_matrix = np.array(
        [
            [1.0, 0.9, 0.9],
            [0.9, 1.0, 0.9],
            [0.9, 0.9, 1.0],
        ]
    )
    edges = top_k_edges(sim_matrix, ids, k=2, threshold=0.0)
    for a, b, _w in edges:
        assert a != b


def test_top_k_edges_respects_threshold():
    ids = ["a", "b", "c"]
    sim_matrix = np.array(
        [
            [1.0, 0.5, 0.1],
            [0.5, 1.0, 0.1],
            [0.1, 0.1, 1.0],
        ]
    )
    edges = top_k_edges(sim_matrix, ids, k=2, threshold=0.3)
    pairs = {frozenset((a, b)) for a, b, _w in edges}
    assert frozenset(("a", "b")) in pairs
    assert frozenset(("a", "c")) not in pairs
    assert frozenset(("b", "c")) not in pairs


def test_top_k_edges_limits_to_k_per_row():
    ids = ["a", "b", "c", "d", "e"]
    # a is fairly similar to b, c, d, e (descending), but each of b/c/d/e
    # has its own closer "buddy" elsewhere, so with k=1 only a's single
    # nearest neighbor (b) should survive as an edge touching a — no other
    # row's own top-1 pick lands back on a.
    sim_matrix = np.array(
        [
            [1.00, 0.90, 0.89, 0.88, 0.87],
            [0.90, 1.00, 0.95, 0.05, 0.05],
            [0.89, 0.95, 1.00, 0.05, 0.05],
            [0.88, 0.05, 0.05, 1.00, 0.95],
            [0.87, 0.05, 0.05, 0.95, 1.00],
        ]
    )
    edges = top_k_edges(sim_matrix, ids, k=1, threshold=0.0)
    touching_a = [e for e in edges if "a" in (e[0], e[1])]
    assert len(touching_a) == 1
    a, b, weight = touching_a[0]
    assert {a, b} == {"a", "b"}
    assert np.isclose(weight, 0.90)
    assert len(edges) == 3


def test_top_k_edges_dedupes_mutual_pairs():
    ids = ["a", "b"]
    sim_matrix = np.array([[1.0, 0.8], [0.8, 1.0]])
    edges = top_k_edges(sim_matrix, ids, k=1, threshold=0.0)
    assert len(edges) == 1
    a, b, weight = edges[0]
    assert {a, b} == {"a", "b"}
    assert np.isclose(weight, 0.8)


def test_edges_for_new_vector_empty_existing_returns_no_edges():
    new_unit = np.array([1.0, 0.0], dtype=np.float32)
    existing_units = np.zeros((0, 2), dtype=np.float32)
    edges = edges_for_new_vector(new_unit, existing_units, [], "new", k=3, threshold=0.0)
    assert edges == []


def test_edges_for_new_vector_ranks_by_similarity():
    new_unit = np.array([1.0, 0.0], dtype=np.float32)
    existing_units = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]], dtype=np.float32))
    existing_ids = ["close", "far", "medium"]
    edges = edges_for_new_vector(new_unit, existing_units, existing_ids, "new", k=1, threshold=0.0)
    assert len(edges) == 1
    _new_id, other_id, _weight = edges[0]
    assert other_id == "close"


def test_edges_for_new_vector_respects_threshold():
    new_unit = np.array([1.0, 0.0], dtype=np.float32)
    existing_units = l2_normalize(np.array([[0.0, 1.0]], dtype=np.float32))
    edges = edges_for_new_vector(new_unit, existing_units, ["far"], "new", k=3, threshold=0.5)
    assert edges == []
