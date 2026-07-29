"""Embeddings for the real memory store.

Originally planned as a local sentence-transformers model, but this
sandbox's egress policy blocks huggingface.co (confirmed via the proxy's
own status endpoint — a clean 403 policy denial, not a transient failure),
so model weights can't be downloaded here. Uses scikit-learn's
HashingVectorizer instead: a fixed-dimensionality vectorizer that needs no
fitting and no external download, ever. This is real, data-driven text
similarity (hashed bag-of-words + bigrams, stopwords filtered) — lower
semantic fidelity than a neural embedding, but never silently fake:
relatedness comes from actual shared vocabulary in the memory content, and
it works reliably in this network-constrained environment.
"""
from __future__ import annotations

import threading

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

from backend import config

_vectorizer: HashingVectorizer | None = None
_lock = threading.Lock()


def _get_vectorizer() -> HashingVectorizer:
    global _vectorizer
    if _vectorizer is None:
        with _lock:
            if _vectorizer is None:
                _vectorizer = HashingVectorizer(
                    n_features=config.REAL_EMBEDDING_DIM,
                    stop_words="english",
                    alternate_sign=False,
                    norm="l2",
                    ngram_range=(1, 2),
                )
    return _vectorizer


def warmup() -> None:
    """Forces initialization so a broken install fails loudly at server
    startup rather than hanging/erroring on someone's first chat message."""
    embed("warmup")


def embed(text: str) -> np.ndarray:
    """L2-normalized float32 vector of dimension config.REAL_EMBEDDING_DIM."""
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> np.ndarray:
    vectors = _get_vectorizer().transform(texts)
    return vectors.toarray().astype(np.float32)


def dimension() -> int:
    return config.REAL_EMBEDDING_DIM
