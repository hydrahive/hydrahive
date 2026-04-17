"""#689: MusicGenerateTool — Validation + Dispatch + blockierender Flow."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import minimax_music
from hydrahive_core.jobs_service import JobService
from hydrahive_core.tool_registry import MusicGenerateTool


_MP3 = b"\xff\xfbFakeMP3"


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.fixture
def tool(svc) -> MusicGenerateTool:
    return MusicGenerateTool(job_service=svc)


@pytest.fixture
def _patch_request(monkeypatch):
    async def fake(**kw):
        return _MP3
    monkeypatch.setattr(minimax_music, "request_music", fake)


# ────────────────────────────────────────────── Validation


@pytest.mark.asyncio
async def test_empty_prompt_returns_error(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="")
    assert out == {"error": "prompt ist leer"}


@pytest.mark.asyncio
async def test_prompt_too_long(tool):
    out = await tool.execute(agent_id="a", project_id="p", prompt="x" * 501)
    assert "prompt zu lang" in out.get("error", "")


@pytest.mark.asyncio
async def test_lyrics_too_long(tool):
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="x",
        lyrics="y" * 3001,
    )
    assert "lyrics zu lang" in out.get("error", "")


@pytest.mark.asyncio
async def test_lyrics_and_instrumental_conflict(tool):
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="x",
        lyrics="la la", instrumental=True,
    )
    assert "widersprechen" in out.get("error", "")


@pytest.mark.asyncio
async def test_unknown_model_rejected(tool):
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="x", model="suno-v3",
    )
    assert "model" in out.get("error", "").lower()


# ────────────────────────────────────────────── Key-Check


@pytest.mark.asyncio
async def test_no_key_returns_error_without_submit(tool, svc, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_music, "settings",
        SimpleNamespace(llm_config=tmp_path / "nope.json", llm_env=tmp_path / "nope_env"),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    out = await tool.execute(agent_id="a", project_id="p", prompt="cat")
    assert "API-Key fehlt" in out.get("error", "")
    assert svc.list() == []


@pytest.mark.asyncio
async def test_tool_without_job_service_returns_error(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    bare = MusicGenerateTool()
    out = await bare.execute(agent_id="a", project_id="p", prompt="cat")
    assert "JobService" in out.get("error", "")


# ────────────────────────────────────────────── Happy Path (blockierend)


@pytest.mark.asyncio
async def test_tool_returns_succeeded_with_artifact(tool, svc, monkeypatch, _patch_request):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    out = await tool.execute(
        agent_id="a1", project_id="p1",
        prompt="reggae groove",
        lyrics="rise up, shine bright",
    )
    assert out["status"] == "succeeded"
    assert out["job_id"].startswith("job_")
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert art["filename"] == "music_0.mp3"
    assert art["mime"] == "audio/mpeg"
    assert art["download_url"] == f"/me/jobs/{out['job_id']}/artifacts/music_0.mp3"
    assert "error" not in out

    # Meta-Check: input_summary und Felder
    meta = svc.get(out["job_id"])
    assert meta.type == "music"
    assert meta.provider == "minimax"
    assert meta.input_summary["prompt"] == "reggae groove"
    assert meta.input_summary["lyrics"] == "rise up, shine bright"
    assert meta.input_summary["instrumental"] is False
    assert meta.input_summary["model"] == "music-2.6"
    assert meta.agent_id == "a1"
    assert meta.project_id == "p1"
    assert meta.created_by is None


@pytest.mark.asyncio
async def test_input_summary_truncates_prompt_and_lyrics(tool, svc, monkeypatch, _patch_request):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    out = await tool.execute(
        agent_id="a", project_id="p",
        prompt="p" * 450, lyrics="l" * 1200,
    )
    meta = svc.get(out["job_id"])
    assert len(meta.input_summary["prompt"]) <= 200
    assert len(meta.input_summary["lyrics"]) <= 200


# ────────────────────────────────────────────── Dispatch (Runner-Aufruf)


@pytest.mark.asyncio
async def test_dispatch_user_lyrics(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    captured = {}

    async def fake_request(**kw):
        captured.update(kw)
        return _MP3

    monkeypatch.setattr(minimax_music, "request_music", fake_request)
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="synthwave", lyrics="neon",
    )
    assert out["status"] == "succeeded"
    assert captured["lyrics"] == "neon"
    assert captured["lyrics_optimizer"] is False
    assert captured["is_instrumental"] is False


@pytest.mark.asyncio
async def test_dispatch_lyrics_optimizer(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    captured = {}

    async def fake_request(**kw):
        captured.update(kw)
        return _MP3

    monkeypatch.setattr(minimax_music, "request_music", fake_request)
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="chill", lyrics="",
    )
    assert out["status"] == "succeeded"
    assert captured["lyrics_optimizer"] is True
    assert captured["is_instrumental"] is False
    assert captured["lyrics"] == ""


@pytest.mark.asyncio
async def test_dispatch_instrumental(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    captured = {}

    async def fake_request(**kw):
        captured.update(kw)
        return _MP3

    monkeypatch.setattr(minimax_music, "request_music", fake_request)
    out = await tool.execute(
        agent_id="a", project_id="p", prompt="piano", instrumental=True,
    )
    assert out["status"] == "succeeded"
    assert captured["is_instrumental"] is True
    assert captured["lyrics_optimizer"] is False
    assert captured["lyrics"] == ""


# ────────────────────────────────────────────── Runner-Error


@pytest.mark.asyncio
async def test_runner_error_surfaces_as_failed(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_request(**kw):
        raise minimax_music.MinimaxMusicError("MiniMax: Guthaben erschöpft (1008)")

    monkeypatch.setattr(minimax_music, "request_music", fake_request)
    out = await tool.execute(agent_id="a", project_id="p", prompt="x")
    assert out["status"] == "failed"
    assert "Guthaben" in out.get("error", "")
    assert "Traceback" not in out.get("error", "")
    assert out["artifacts"] == []


# ────────────────────────────────────────────── Schema


def test_tool_schema_phase3_defaults():
    tool = MusicGenerateTool()
    params = tool.parameters
    assert params["required"] == ["prompt"]
    assert params["properties"]["model"]["enum"] == ["music-2.6"]
    assert params["properties"]["instrumental"]["type"] == "boolean"
    # Keine sample_rate/bitrate exposed
    assert "sample_rate" not in params["properties"]
    assert "bitrate" not in params["properties"]
    assert "output_format" not in params["properties"]


def test_tool_flags():
    tool = MusicGenerateTool()
    assert tool.is_destructive is False
    assert tool.is_read_only is False
    assert tool.parallel_safe is True
