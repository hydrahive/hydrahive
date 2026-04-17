"""#679: MiniMax Image-Client + Runner — HTTP-Mock-Tests.

Testet _minimax_image_api_key-Präzedenz, request_image-HTTP-Paths
(success, 401, 429, 500, timeout, malformed body), und
build_image_runner mit echtem JobService auf tmp_path.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import minimax_image
from hydrahive_core.jobs_service import JobService
from hydrahive_core.minimax_image import (
    MinimaxImageError,
    _minimax_image_api_key,
    build_image_runner,
    request_image,
)


_PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


# ────────────────────────────────────────────── Key-Lookup


def _fake_settings(cfg_path: Path, env_path: Path):
    """Stub statt Pydantic-Settings — Properties lassen sich auf pydantic
    BaseSettings nicht per monkeypatch überschreiben. Wir tauschen das ganze
    ``settings``-Objekt in minimax_image's Modul-Scope."""
    from types import SimpleNamespace
    return SimpleNamespace(llm_config=cfg_path, llm_env=env_path)


class TestApiKeyPrecedence:
    def test_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        assert _minimax_image_api_key() == "env-key"

    def test_config_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({
            "providers": {"minimax": {"api_key": "cfg-key"}}
        }))
        monkeypatch.setattr(
            minimax_image, "settings",
            _fake_settings(cfg_file, tmp_path / "no_env"),
        )
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_image_api_key() == "cfg-key"

    def test_llm_env_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({"providers": {}}))
        env_file = tmp_path / "llm_env"
        env_file.write_text("OTHER=1\nMINIMAX_API_KEY=file-key\n")
        monkeypatch.setattr(
            minimax_image, "settings", _fake_settings(cfg_file, env_file),
        )
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_image_api_key() == "file-key"

    def test_none_when_absent(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setattr(
            minimax_image, "settings",
            _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"),
        )
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_image_api_key() is None


# ────────────────────────────────────────────── request_image Matrix


class _FakeResponse:
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("invalid json")


class _FakeClient:
    """Minimaler httpx.AsyncClient-Stand-in. Ein .post() pro Aufruf."""

    def __init__(self, response: _FakeResponse | None = None, raise_exc: Exception | None = None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    async def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_request_image_success():
    client = _FakeClient(_FakeResponse(200, {"data": {"image_base64": [_PNG_B64]}}))
    out = await request_image(
        prompt="cat", aspect_ratio="1:1", model="image-01",
        api_key="k", base_url="https://api.minimax.io/v1",
        client=client,
    )
    assert out == [_PNG_BYTES]
    call = client.calls[0]
    assert call["url"] == "https://api.minimax.io/v1/image_generation"
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["model"] == "image-01"
    assert call["json"]["response_format"] == "base64"
    assert call["json"]["n"] == 1


@pytest.mark.asyncio
async def test_request_image_base_url_trailing_slash():
    client = _FakeClient(_FakeResponse(200, {"data": {"image_base64": [_PNG_B64]}}))
    await request_image(
        prompt="x", aspect_ratio="1:1", model="image-01",
        api_key="k", base_url="https://api.minimax.io/v1/",
        client=client,
    )
    assert client.calls[0]["url"] == "https://api.minimax.io/v1/image_generation"


@pytest.mark.asyncio
async def test_request_image_401():
    client = _FakeClient(_FakeResponse(401, {"detail": "invalid key"}))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="bad", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "401" in str(ei.value)
    assert "bad" not in str(ei.value)  # kein Key im Fehlertext


@pytest.mark.asyncio
async def test_request_image_429():
    client = _FakeClient(_FakeResponse(429, {}))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "rate limit" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_image_500():
    client = _FakeClient(_FakeResponse(500, {}))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "500" in str(ei.value)


@pytest.mark.asyncio
async def test_request_image_timeout():
    client = _FakeClient(raise_exc=httpx.ConnectTimeout("timeout"))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "timeout" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_image_base_resp_error_message():
    client = _FakeClient(_FakeResponse(200, {
        "base_resp": {"status_code": 2013, "status_msg": "invalid prompt"},
    }))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "invalid prompt" in str(ei.value)


@pytest.mark.asyncio
async def test_request_image_missing_image_base64():
    client = _FakeClient(_FakeResponse(200, {"data": {"other": "nope"}}))
    with pytest.raises(MinimaxImageError) as ei:
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )
    assert "image_base64" in str(ei.value)


@pytest.mark.asyncio
async def test_request_image_bad_base64():
    client = _FakeClient(_FakeResponse(200, {"data": {"image_base64": ["not-base64!"]}}))
    with pytest.raises(MinimaxImageError):
        await request_image(
            prompt="x", aspect_ratio="1:1", model="image-01",
            api_key="k", base_url="https://api.minimax.io/v1",
            client=client,
        )


# ────────────────────────────────────────────── build_image_runner


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.mark.asyncio
async def test_runner_happy_path(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_request(**kwargs):
        return [_PNG_BYTES]

    runner = build_image_runner(
        prompt="cat", aspect_ratio="1:1", model="image-01",
        _request_image=fake_request,
    )
    meta = svc.submit(
        type="image", provider="minimax", runner=runner,
        input_summary={"prompt": "cat"}, created_by=None,
    )
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "succeeded"
    assert final.progress_percent == 100
    assert len(final.artifacts) == 1
    assert final.artifacts[0]["filename"] == "image_0.png"
    assert final.artifacts[0]["mime"] == "image/png"
    # Artifact-Datei existiert und enthält genau die Mock-Bytes.
    path = svc.artifact_path(meta.job_id, "image_0.png")
    assert path.read_bytes() == _PNG_BYTES


@pytest.mark.asyncio
async def test_runner_without_key_fails_without_artifact(svc, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_image, "settings",
        _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    async def fake_request(**kwargs):  # pragma: no cover — runner bricht vorher ab
        raise AssertionError("should not be called when no key")

    runner = build_image_runner(
        prompt="x", aspect_ratio="1:1", model="image-01",
        _request_image=fake_request,
    )
    meta = svc.submit(type="image", provider="minimax", runner=runner, created_by=None)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "Key" in (final.error or "") or "key" in (final.error or "").lower()
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_cancelled_before_request(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    invocations = {"count": 0}

    async def fake_request(**kwargs):
        invocations["count"] += 1
        return [_PNG_BYTES]

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_runner_wrapper(ctx):
        # Simulate a pre-check cancellation: signal, dann warten, dann Runner
        # ausführen — wir cancellen BEVOR release gesetzt wird.
        started.set()
        await release.wait()
        inner = build_image_runner(
            prompt="x", aspect_ratio="1:1", model="image-01",
            _request_image=fake_request,
        )
        await inner(ctx)

    meta = svc.submit(type="image", provider="minimax", runner=slow_runner_wrapper, created_by=None)
    await started.wait()
    svc.cancel(meta.job_id)
    release.set()
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "cancelled"
    assert invocations["count"] == 0  # fake_request nie aufgerufen
    assert final.artifacts == []
