"""
test_permission_classifier_cache.py — #749 bounded + thread-safe TTLCache

Deckt die Cache-Obergrenze + LRU-Eviction + grundlegende Lock-Nutzung ab.
classify_llm selbst (async, ruft litellm) ist hier nicht Ziel — der Cache
ist das Regressions-Risiko.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.permission_classifier import (
    _CACHE_MAXSIZE,
    _CACHE_TTL,
    _CLASSIFIER_CACHE,
    _CLASSIFIER_CACHE_LOCK,
    RiskLevel,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Jeder Test startet mit leerem Cache."""
    with _CLASSIFIER_CACHE_LOCK:
        _CLASSIFIER_CACHE.clear()
    yield
    with _CLASSIFIER_CACHE_LOCK:
        _CLASSIFIER_CACHE.clear()


def test_maxsize_constants_sane():
    assert _CACHE_MAXSIZE == 10_000
    assert _CACHE_TTL == 300


def test_cache_respects_maxsize():
    """Mehr als MAXSIZE Einträge einfügen → LRU-Eviction, Cache bleibt bounded."""
    with _CLASSIFIER_CACHE_LOCK:
        for i in range(_CACHE_MAXSIZE + 5_000):
            _CLASSIFIER_CACHE[f"tool_{i}"] = RiskLevel.ALLOW
    with _CLASSIFIER_CACHE_LOCK:
        assert len(_CLASSIFIER_CACHE) <= _CACHE_MAXSIZE, (
            f"Cache überschreitet MAXSIZE: {len(_CLASSIFIER_CACHE)} > {_CACHE_MAXSIZE}"
        )


def test_cache_lru_evicts_oldest():
    """Nach Überlauf sind die ersten Einträge weg (LRU-Verhalten)."""
    with _CLASSIFIER_CACHE_LOCK:
        for i in range(_CACHE_MAXSIZE + 100):
            _CLASSIFIER_CACHE[f"tool_{i}"] = RiskLevel.CONFIRM
    # Die ersten 100 müssten evicted sein
    with _CLASSIFIER_CACHE_LOCK:
        for i in range(50):
            assert _CLASSIFIER_CACHE.get(f"tool_{i}") is None
        # Die letzten müssen noch da sein
        assert _CLASSIFIER_CACHE.get(f"tool_{_CACHE_MAXSIZE + 99}") == RiskLevel.CONFIRM


def test_cache_get_miss_returns_none():
    assert _CLASSIFIER_CACHE.get("nonexistent_key") is None


def test_cache_concurrent_writes_no_crash():
    """
    Basic Thread-Safety: 10 Threads schreiben gleichzeitig 1000 Keys.
    Ohne Lock würde cachetools' TTLCache intern crashen oder inkonsistent
    werden. Wir wollen: kein Exception, Cache bleibt bounded.
    """
    errors: list[Exception] = []

    def worker(start: int) -> None:
        try:
            for i in range(1000):
                key = f"t{start}_{i}"
                with _CLASSIFIER_CACHE_LOCK:
                    _CLASSIFIER_CACHE[key] = RiskLevel.DENY
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Thread-Crash: {errors}"
    with _CLASSIFIER_CACHE_LOCK:
        assert len(_CLASSIFIER_CACHE) <= _CACHE_MAXSIZE
