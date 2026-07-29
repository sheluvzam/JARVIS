"""Embeddings for the real memory store.

Originally planned as a local sentence-transformers model, but this
sandbox's egress policy blocks huggingface.co (confirmed via the proxy's
own status endpoint — a clean 403 policy denial, not a transient failure),
so model weights can't be downloaded here. Uses scikit-learn's
HashingVectorizer instead: a fixed-dimensionality vectorizer that needs no
fitting and no external download, ever.

Uses a character n-gram analyzer (word-boundary-aware, 3-5 chars), not
word-level bigrams — measured directly against real remembered content
during tuning (see git history / PR discussion): word-bigram similarity on
short, terse "memory" sentences produced near-zero similarity even between
genuinely related memories ("Favorite programming language is Python" vs.
"Enjoys writing Python scripts for automation" scored 0.126 — barely above
noise), while unrelated short sentences sharing common phrasing verbs
("Likes dark roast coffee..." vs. "...dog's name is Biscuit.") scored
*higher* (0.149) than the real match, purely from coincidental whole-word
overlap. Character n-grams are robust to morphological variation and don't
get fooled by shared short structural words the way whole-word bigrams do;
on the same real data they cleanly separated a genuine topic cluster
(0.14-0.22) from actual noise (0.03-0.04). Still real, data-driven
similarity — lower semantic fidelity than a neural embedding, but never
silently fake.
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
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    alternate_sign=False,
                    norm="l2",
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
