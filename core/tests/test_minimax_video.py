"""#688: MiniMax Video-Client + Runner — alles ohne echte HTTP-Calls.

Deckt start/status/retrieve/download Primitives plus den vollen Runner-Loop
(happy, fail, timeout, cancel). Basis sind _FakeClient + Test-Hooks im
Runner-Builder, analog zu test_minimax_image.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import minimax_video
from hydrahive_core.jobs_service import JobService
from hydrahive_core.minimax_video import (
    MinimaxVideoError,
    _classify_status,
    _compute_poll_progress,
    _minimax_video_api_key,
    build_video_runner,
    download_video,
    get_video_task_status,
    retrieve_video_file,
    start_video_task,
)


_MP4 = b"\x00\x00\x00 ftypisom FAKE"


# ────────────────────────────────────────────── Fake HTTP-Client


class _FakeResponse:
    def __init__(self, status_code: int, body: Any = None, content: bytes = b""):
        self.status_code = status_code
        self._body = body
        self.content = content

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("invalid json")


class _FakeClient:
    """Minimaler httpx.AsyncClient-Stand-in mit Call-Matcher."""

    def __init__(self):
        self.post_responses: list[_FakeResponse | Exception] = []
        self.get_responses:  list[_FakeResponse | Exception] = []
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers or {}, "json": json})
        r = self.post_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}, "params": params or {}})
        r = self.get_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def aclose(self):
        pass


# ────────────────────────────────────────────── Key-Lookup


def _fake_settings(cfg_path: Path, env_path: Path):
    return SimpleNamespace(llm_config=cfg_path, llm_env=env_path)


class TestKeyPrecedence:
    def test_env_wins(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        assert _minimax_video_api_key() == "env-key"

    def test_config_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({"providers": {"minimax": {"api_key": "cfg-key"}}}))
        monkeypatch.setattr(minimax_video, "settings", _fake_settings(cfg_file, tmp_path / "no_env"))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_video_api_key() == "cfg-key"

    def test_llm_env_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({"providers": {}}))
        env_file = tmp_path / "llm_env"
        env_file.write_text("MINIMAX_API_KEY=file-key\n")
        monkeypatch.setattr(minimax_video, "settings", _fake_settings(cfg_file, env_file))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_video_api_key() == "file-key"

    def test_none_when_absent(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setattr(minimax_video, "settings", _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_video_api_key() is None


# ────────────────────────────────────────────── Status-Classifier


@pytest.mark.parametrize("raw,expected", [
    ("Success",    "success"),
    ("success",    "success"),
    ("SUCCESS",    "success"),
    ("Fail",       "failure"),
    ("failed",     "failure"),
    ("Queueing",   "processing"),
    ("Preparing",  "processing"),
    ("Processing", "processing"),
    ("",           "processing"),
    ("weird",      "processing"),
])
def test_classify_status(raw, expected):
    assert _classify_status(raw) == expected


@pytest.mark.parametrize("n,low,high", [
    (0, 10, 10),
    (1, 10, 20),
    (30, 40, 60),
    (60, 80, 90),
    (100, 90, 90),
    (9999, 90, 90),
])
def test_compute_poll_progress_bounds(n, low, high):
    p = _compute_poll_progress(n)
    assert low <= p <= high


# ────────────────────────────────────────────── start_video_task


@pytest.mark.asyncio
async def test_start_task_success():
    client = _FakeClient()
    client.post_responses = [_FakeResponse(200, {"task_id": "task-123"})]
    task_id = await start_video_task(
        prompt="cat surfing", model="MiniMax-Hailuo-2.3", duration=6,
        resolution="1080P", api_key="k", base_url="https://api.minimax.io/v1",
        client=client,
    )
    assert task_id == "task-123"
    call = client.calls[0]
    assert call["url"] == "https://api.minimax.io/v1/video_generation"
    assert call["headers"]["Authorization"] == "Bearer k"
    body = call["json"]
    assert body["prompt"] == "cat surfing"
    assert body["model"] == "MiniMax-Hailuo-2.3"
    assert body["duration"] == 6
    assert body["resolution"] == "1080P"


@pytest.mark.asyncio
async def test_start_task_401():
    client = _FakeClient()
    client.post_responses = [_FakeResponse(401, {})]
    with pytest.raises(MinimaxVideoError) as ei:
        await start_video_task(
            prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
            api_key="bad", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "401" in str(ei.value)
    assert "bad" not in str(ei.value)


@pytest.mark.asyncio
async def test_start_task_429():
    client = _FakeClient()
    client.post_responses = [_FakeResponse(429, {})]
    with pytest.raises(MinimaxVideoError) as ei:
        await start_video_task(
            prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
            api_key="k", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "rate limit" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_start_task_timeout():
    client = _FakeClient()
    client.post_responses = [httpx.ConnectTimeout("boom")]
    with pytest.raises(MinimaxVideoError) as ei:
        await start_video_task(
            prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
            api_key="k", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "timeout" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_start_task_missing_task_id():
    client = _FakeClient()
    client.post_responses = [_FakeResponse(200, {"other": "field"})]
    with pytest.raises(MinimaxVideoError) as ei:
        await start_video_task(
            prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
            api_key="k", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "task_id" in str(ei.value)


@pytest.mark.asyncio
async def test_start_task_base_resp_error():
    client = _FakeClient()
    client.post_responses = [_FakeResponse(200, {
        "base_resp": {"status_code": 2013, "status_msg": "invalid prompt"},
    })]
    with pytest.raises(MinimaxVideoError) as ei:
        await start_video_task(
            prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
            api_key="k", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "invalid prompt" in str(ei.value)


# ────────────────────────────────────────────── get_video_task_status


@pytest.mark.asyncio
async def test_status_processing():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, {"status": "Processing"})]
    body = await get_video_task_status(
        task_id="t", api_key="k", base_url="https://api.minimax.io/v1", client=client,
    )
    assert body["status"] == "Processing"
    assert client.calls[0]["params"] == {"task_id": "t"}


@pytest.mark.asyncio
async def test_status_success_with_file_id():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, {"status": "Success", "file_id": "f-1"})]
    body = await get_video_task_status(
        task_id="t", api_key="k", base_url="https://api.minimax.io/v1", client=client,
    )
    assert body["status"] == "Success"
    assert body["file_id"] == "f-1"


@pytest.mark.asyncio
async def test_status_fail_with_error_message():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, {"status": "Fail", "error_message": "too long"})]
    body = await get_video_task_status(
        task_id="t", api_key="k", base_url="https://api.minimax.io/v1", client=client,
    )
    assert body["status"] == "Fail"
    assert body["error_message"] == "too long"


# ────────────────────────────────────────────── retrieve_video_file


@pytest.mark.asyncio
async def test_retrieve_success():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, {"file": {"download_url": "https://cdn.example.com/v.mp4?sig=x"}})]
    url = await retrieve_video_file(
        file_id="f-1", api_key="k", base_url="https://api.minimax.io/v1", client=client,
    )
    assert url == "https://cdn.example.com/v.mp4?sig=x"


@pytest.mark.asyncio
async def test_retrieve_missing_download_url():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, {"file": {"other": "x"}})]
    with pytest.raises(MinimaxVideoError) as ei:
        await retrieve_video_file(
            file_id="f-1", api_key="k", base_url="https://api.minimax.io/v1", client=client,
        )
    assert "download_url" in str(ei.value)


# ────────────────────────────────────────────── download_video


@pytest.mark.asyncio
async def test_download_success():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(200, content=_MP4)]
    data = await download_video(download_url="https://cdn/x.mp4", client=client)
    assert data == _MP4


@pytest.mark.asyncio
async def test_download_404():
    client = _FakeClient()
    client.get_responses = [_FakeResponse(404, content=b"")]
    with pytest.raises(MinimaxVideoError) as ei:
        await download_video(download_url="https://cdn/x.mp4", client=client)
    assert "404" in str(ei.value)


# ────────────────────────────────────────────── build_video_runner — full orchestration


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.mark.asyncio
async def test_runner_happy_path(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    calls: list[str] = []

    async def fake_start(**kw): calls.append("start"); return "task-1"

    poll_state = {"n": 0}

    async def fake_status(**kw):
        poll_state["n"] += 1
        calls.append(f"status-{poll_state['n']}")
        if poll_state["n"] < 2:
            return {"status": "Processing"}
        return {"status": "Success", "file_id": "f-1"}

    async def fake_retrieve(**kw):
        calls.append("retrieve")
        return "https://cdn/v.mp4"

    async def fake_download(**kw):
        calls.append("download")
        return _MP4

    runner = build_video_runner(
        prompt="cat", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.01, max_poll_seconds=5,
        _start=fake_start, _status=fake_status,
        _retrieve=fake_retrieve, _download=fake_download,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)

    final = svc.get(meta.job_id)
    assert final.status == "succeeded"
    assert final.progress_percent == 100
    assert [a["filename"] for a in final.artifacts] == ["video_0.mp4"]
    assert final.artifacts[0]["mime"] == "video/mp4"
    assert svc.artifact_path(meta.job_id, "video_0.mp4").read_bytes() == _MP4
    # Reihenfolge: start → status-1 (processing) → status-2 (success) → retrieve → download.
    assert calls == ["start", "status-1", "status-2", "retrieve", "download"]


@pytest.mark.asyncio
async def test_runner_remote_failure(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_start(**kw): return "task-1"

    async def fake_status(**kw):
        return {"status": "Fail", "error_message": "content policy violation"}

    async def fake_retrieve(**kw):  # pragma: no cover
        raise AssertionError("should not retrieve on Fail")

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.01, max_poll_seconds=5,
        _start=fake_start, _status=fake_status, _retrieve=fake_retrieve,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "content policy" in (final.error or "").lower()
    assert "Traceback" not in (final.error or "")
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_timeout(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_start(**kw): return "task-1"

    async def fake_status(**kw):
        return {"status": "Processing"}  # nie fertig

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.02, max_poll_seconds=0.1,
        _start=fake_start, _status=fake_status,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=3)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "timeout" in (final.error or "").lower()


@pytest.mark.asyncio
async def test_runner_cancel_mid_poll(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    started = asyncio.Event()
    cancelled_event = asyncio.Event()

    async def fake_start(**kw):
        started.set()
        return "task-1"

    status_calls = {"n": 0}

    async def fake_status(**kw):
        status_calls["n"] += 1
        # Nach erstem poll Cancel setzen
        if status_calls["n"] == 1:
            cancelled_event.set()
        return {"status": "Processing"}

    async def fake_retrieve(**kw):  # pragma: no cover
        raise AssertionError("retrieve should not run after cancel")

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.05, max_poll_seconds=5,
        _start=fake_start, _status=fake_status, _retrieve=fake_retrieve,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await started.wait()
    await cancelled_event.wait()
    svc.cancel(meta.job_id)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=3)
    final = svc.get(meta.job_id)
    assert final.status == "cancelled"
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_no_key(svc, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_video, "settings",
        _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    calls: list[str] = []

    async def fake_start(**kw):  # pragma: no cover
        calls.append("start")
        raise AssertionError("start should not run without key")

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.01, max_poll_seconds=5,
        _start=fake_start,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "key" in (final.error or "").lower()
    assert calls == []
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_success_without_file_id(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_start(**kw): return "task-1"

    async def fake_status(**kw):
        return {"status": "Success"}  # missing file_id

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.01, max_poll_seconds=5,
        _start=fake_start, _status=fake_status,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "file_id" in (final.error or "").lower()


@pytest.mark.asyncio
async def test_runner_empty_download(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_start(**kw): return "task-1"
    async def fake_status(**kw): return {"status": "Success", "file_id": "f-1"}
    async def fake_retrieve(**kw): return "https://cdn/x.mp4"
    async def fake_download(**kw): return b""

    runner = build_video_runner(
        prompt="x", model="MiniMax-Hailuo-2.3", duration=6, resolution="1080P",
        poll_interval_seconds=0.01, max_poll_seconds=5,
        _start=fake_start, _status=fake_status,
        _retrieve=fake_retrieve, _download=fake_download,
    )
    meta = svc.submit(type="video", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "empty" in (final.error or "").lower()
    assert final.artifacts == []
