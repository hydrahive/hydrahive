"""#805 Phase A — MiniMax Token-Plan Usage, Unit-Tests ohne echten HTTP."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def real_response():
    """Echte MiniMax-Response-Shape (live 21.04.2026, gekürzt)."""
    return {
        "model_remains": [
            {
                "model_name": "MiniMax-M2.7",
                "start_time": 1776765600000,
                "end_time": 1776783600000,
                "remains_time": 8420480,
                "current_interval_total_count": 15000,
                "current_interval_usage_count": 151,
                "current_weekly_total_count": 150000,
                "current_weekly_usage_count": 1170,
            },
            {
                "model_name": "speech-hd",
                "current_interval_total_count": 11000,
                "current_interval_usage_count": 2505,
                "current_weekly_total_count": 77000,
                "current_weekly_usage_count": 2505,
            },
            {
                "model_name": "MiniMax-Hailuo-2.3-Fast-6s-768p",
                "current_interval_total_count": 2,
                "current_interval_usage_count": 0,
                "current_weekly_total_count": 100,
                "current_weekly_usage_count": 0,
            },
            {
                "model_name": "music-2.6",
                "current_interval_total_count": 100,
                "current_interval_usage_count": 9,
                "current_weekly_total_count": 500,
                "current_weekly_usage_count": 9,
            },
            {
                "model_name": "image-01",
                "current_interval_total_count": 120,
                "current_interval_usage_count": 12,
                "current_weekly_total_count": 840,
                "current_weekly_usage_count": 12,
            },
        ]
    }


def _build_async_client_mock(response_obj):
    """Baut einen httpx.AsyncClient-Mock, dessen get() das response_obj liefert."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.get = AsyncMock(return_value=response_obj)
    return client


def _reset_cache():
    import hydrahive_core.minimax_usage as mu
    mu._cache = {"data": None, "fetched_at": 0.0}


# ────────────────────────────────────────────── Short-Name Mapping


def test_unknown_model_maps_to_misc():
    from hydrahive_core.minimax_usage import _short_name
    assert _short_name("some-unknown-xyz") == "misc"
    assert _short_name("") == "misc"


def test_all_prefixes():
    from hydrahive_core.minimax_usage import _short_name
    assert _short_name("MiniMax-M2.7") == "text"
    assert _short_name("MiniMax-M*") == "text"
    assert _short_name("speech-hd") == "tts"
    assert _short_name("speech-02") == "tts"
    assert _short_name("music-2.6") == "music"
    assert _short_name("music-2.5") == "music"
    assert _short_name("MiniMax-Hailuo-2.3-6s-768p") == "video"
    assert _short_name("Hailuo-2.3-Fast") == "video"
    assert _short_name("image-01") == "image"


# ────────────────────────────────────────────── Normalize


def test_normalize_computes_correct_pct():
    """pct ist used/total*100, NICHT total/total*100 (bug-Regression)."""
    from hydrahive_core.minimax_usage import _normalize_model
    m = _normalize_model({
        "model_name": "music-2.6",
        "current_interval_total_count": 100,
        "current_interval_usage_count": 9,
        "current_weekly_total_count": 500,
        "current_weekly_usage_count": 9,
    })
    assert m["interval_pct"] == 9.0
    assert m["weekly_pct"] == 1.8
    assert m["interval_total"] == 100
    assert m["interval_used"] == 9


def test_normalize_zero_total_avoids_div_by_zero():
    from hydrahive_core.minimax_usage import _normalize_model
    m = _normalize_model({"model_name": "x", "current_interval_total_count": 0})
    assert m["interval_pct"] == 0
    assert m["weekly_pct"] == 0


# ────────────────────────────────────────────── Happy Path


@pytest.mark.asyncio
async def test_happy_path(real_response):
    """Volle Response → available=True, alle Modelle normalisiert, Mapping korrekt."""
    _reset_cache()
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(return_value=real_response)
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()

    assert result["available"] is True
    assert "fetched_at" in result
    assert len(result["models"]) == 5
    labels = {m["label"]: m for m in result["models"]}
    assert labels["MiniMax-M2.7"]["name"] == "text"
    assert labels["speech-hd"]["name"] == "tts"
    assert labels["MiniMax-Hailuo-2.3-Fast-6s-768p"]["name"] == "video"
    assert labels["music-2.6"]["name"] == "music"
    assert labels["image-01"]["name"] == "image"
    # Bug-Regression: pct muss ≠ 100 sein wenn used<total
    assert labels["music-2.6"]["interval_pct"] == 9.0


# ────────────────────────────────────────────── Cache


@pytest.mark.asyncio
async def test_cache_hit_within_ttl(real_response):
    """Zweiter Call innerhalb 30 s → kein zweiter HTTP-Request."""
    _reset_cache()
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(return_value=real_response)
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        r1 = await fetch_minimax_token_remains()
        r2 = await fetch_minimax_token_remains()

    assert r1["available"] is True
    assert r2["available"] is True
    assert client_mock.get.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(real_response):
    """Call > 30 s später → neuer HTTP-Request."""
    import hydrahive_core.minimax_usage as mu
    _reset_cache()
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(return_value=real_response)
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        await fetch_minimax_token_remains()
        # Cache künstlich auf "vor 31 s" setzen
        mu._cache["fetched_at"] = time.time() - 31.0
        await fetch_minimax_token_remains()

    assert client_mock.get.call_count == 2


# ────────────────────────────────────────────── Error-Pfade


@pytest.mark.asyncio
async def test_no_api_key_returns_unavailable():
    _reset_cache()
    with patch("hydrahive_core.minimax_usage._minimax_api_key", return_value=None):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()
    assert result["available"] is False
    assert result["reason"] == "no_api_key"


@pytest.mark.asyncio
async def test_http_401_returns_invalid_api_key():
    _reset_cache()
    resp = MagicMock(status_code=401)
    resp.json = MagicMock(return_value={})
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="bogus"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()

    assert result["available"] is False
    assert result["reason"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_http_500_returns_upstream_error():
    _reset_cache()
    resp = MagicMock(status_code=500)
    resp.json = MagicMock(return_value={})
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()

    assert result["available"] is False
    assert result["reason"] == "upstream_error"


@pytest.mark.asyncio
async def test_timeout_returns_network_error():
    _reset_cache()
    import httpx as _httpx
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    async def _raise(*a, **kw):
        raise _httpx.ConnectTimeout("timeout")

    client.get = _raise

    with patch("httpx.AsyncClient", return_value=client), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()

    assert result["available"] is False
    assert result["reason"] == "network_error"


@pytest.mark.asyncio
async def test_invalid_json_returns_unavailable():
    _reset_cache()
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(side_effect=ValueError("bad json"))
    client_mock = _build_async_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=client_mock), \
         patch("hydrahive_core.minimax_usage._minimax_api_key", return_value="fake-key"):
        from hydrahive_core.minimax_usage import fetch_minimax_token_remains
        result = await fetch_minimax_token_remains()

    assert result["available"] is False
    assert result["reason"] == "invalid_json"
