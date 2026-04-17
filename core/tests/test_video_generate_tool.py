"""#688: VideoGenerateTool — Validation + submit-and-return + no-wait-semantik."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import minimax_video
from hydrahive_core.jobs_service import JobService
from hydrahive_core.tool_registry import VideoGenerateTool


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.fixture
def tool(svc) -> VideoGenerateTool:
    return VideoGenerateTool(job_service=svc)


# ────────────────────────────────────────────── Input-Validation


@pytest.mark.asyncio
async def test_empty_prompt_returns_error(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="")
    assert out == {"error": "prompt ist leer"}


@pytest.mark.asyncio
async def test_invalid_duration_returns_error(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="x", duration=10)
    assert "duration" in out.get("error", "")
    assert out.get("allowed") == [6]


@pytest.mark.asyncio
async def test_invalid_resolution_returns_error(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="x", resolution="720P")
    assert "resolution" in out.get("error", "")
    assert out.get("allowed") == ["1080P"]


@pytest.mark.asyncio
async def test_unknown_model_rejected(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="x", model="sora-1")
    assert "model" in out.get("error", "").lower()


# ────────────────────────────────────────────── Key-Check vor Submit


@pytest.mark.asyncio
async def test_no_key_returns_error_without_submit(tool, svc, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_video, "settings",
        SimpleNamespace(llm_config=tmp_path / "nope.json", llm_env=tmp_path / "nope_env"),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    out = await tool.execute(agent_id="a", project_id="p", prompt="cat")
    assert "API-Key fehlt" in out.get("error", "")
    # Kein Job-Müll.
    assert svc.list() == []


@pytest.mark.asyncio
async def test_tool_without_job_service_returns_error(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    bare = VideoGenerateTool()
    out = await bare.execute(agent_id="a", project_id="p", prompt="cat")
    assert "JobService" in out.get("error", "")


# ────────────────────────────────────────────── Submit-and-Return


@pytest.mark.asyncio
async def test_tool_returns_immediately_with_job_id(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    # Runner soll nicht sofort fertig werden — Tool muss trotzdem sofort
    # returnen. Wir ersetzen build_video_runner durch einen langen Runner.
    blocking = asyncio.Event()

    async def slow_runner(ctx):
        ctx.update_progress(10, "waiting")
        await blocking.wait()

    monkeypatch.setattr(
        minimax_video, "build_video_runner",
        lambda **kw: slow_runner,
    )

    out = await asyncio.wait_for(
        tool.execute(
            agent_id="a1", project_id="p1",
            prompt="a dog on the moon",
            _request_user="alice",
        ),
        timeout=0.5,   # Tool muss deutlich unter 500 ms zurück sein
    )

    assert out["job_id"].startswith("job_")
    assert out["status"] in ("queued", "running")   # Race-tolerant
    assert out["poll_url"] == f"/me/jobs/{out['job_id']}"
    assert "Video generation started" in out["message"]
    assert "5–15" in out["message"]
    assert "error" not in out

    # Job ist wirklich in Progress, nicht abgeschlossen.
    meta = svc.get(out["job_id"])
    assert meta.type == "video"
    assert meta.provider == "minimax"
    assert meta.status in ("queued", "running")
    assert meta.input_summary["prompt"] == "a dog on the moon"
    assert meta.input_summary["duration"] == 6
    assert meta.input_summary["resolution"] == "1080P"
    assert meta.input_summary["model"] == "MiniMax-Hailuo-2.3"
    assert meta.agent_id == "a1"
    assert meta.project_id == "p1"
    assert meta.created_by == "alice"

    # Runner abrupt beenden, damit tmp_path-cleanup sauber läuft.
    blocking.set()
    await asyncio.wait_for(svc._tasks[out["job_id"]], timeout=1)


@pytest.mark.asyncio
async def test_tool_input_summary_truncates_long_prompt(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    blocking = asyncio.Event()
    async def slow_runner(ctx): await blocking.wait()
    monkeypatch.setattr(minimax_video, "build_video_runner", lambda **kw: slow_runner)

    long_prompt = "x" * 800
    out = await tool.execute(agent_id="a", project_id="p", prompt=long_prompt)
    meta = svc.get(out["job_id"])
    assert len(meta.input_summary["prompt"]) <= 200

    blocking.set()
    await asyncio.wait_for(svc._tasks[out["job_id"]], timeout=1)


@pytest.mark.asyncio
async def test_tool_cancel_via_jobs_api(tool, svc, monkeypatch):
    """svc.cancel() funktioniert nach submit — Runner sieht check_cancelled."""
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    started = asyncio.Event()

    async def polling_runner(ctx):
        started.set()
        for _ in range(100):
            ctx.check_cancelled()
            await asyncio.sleep(0.02)

    monkeypatch.setattr(minimax_video, "build_video_runner", lambda **kw: polling_runner)

    out = await tool.execute(agent_id="a", project_id="p", prompt="cat")
    await started.wait()
    svc.cancel(out["job_id"])
    await asyncio.wait_for(svc._tasks[out["job_id"]], timeout=3)
    meta = svc.get(out["job_id"])
    assert meta.status == "cancelled"


# ────────────────────────────────────────────── Tool-Metadata


def test_tool_schema_phase2_defaults():
    tool = VideoGenerateTool()
    params = tool.parameters
    assert params["required"] == ["prompt"]
    assert params["properties"]["duration"]["enum"] == [6]
    assert params["properties"]["resolution"]["enum"] == ["1080P"]
    assert params["properties"]["model"]["enum"] == ["MiniMax-Hailuo-2.3"]


def test_tool_flags():
    tool = VideoGenerateTool()
    assert tool.is_destructive is False
    assert tool.is_read_only is False
    assert tool.parallel_safe is True
