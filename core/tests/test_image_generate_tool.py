"""#679: ImageGenerateTool — Scope-Tests (Validation, Key-Check, Integration).

Tool blockt synchron bis der JobService-Task fertig ist; wir injizieren einen
tmp JobService + gemockten request_image, kein Netzwerk.
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import minimax_image
from hydrahive_core.jobs_service import JobService
from hydrahive_core.tool_registry import ImageGenerateTool


_PNG = b"\x89PNG\r\n\x1a\nOK"


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.fixture
def tool(svc) -> ImageGenerateTool:
    return ImageGenerateTool(job_service=svc)


@pytest.fixture
def _patch_request(monkeypatch):
    """Mockt hydrahive_core.minimax_image.request_image. Lifecycle-safe
    weil der Tool-Pfad die Funktion lazy importiert."""
    async def fake(**kwargs):
        return [_PNG]
    monkeypatch.setattr(minimax_image, "request_image", fake)


# ────────────────────────────────────────────── Input-Validation


@pytest.mark.asyncio
async def test_empty_prompt_returns_error(tool):
    out = await tool.execute(agent_id="a1", project_id="p1", prompt="")
    assert out == {"error": "prompt ist leer"}


@pytest.mark.asyncio
async def test_invalid_aspect_ratio_returns_error(tool):
    out = await tool.execute(
        agent_id="a1", project_id="p1",
        prompt="cat", aspect_ratio="99:1",
    )
    assert "aspect_ratio" in out.get("error", "")
    assert "1:1" in out.get("allowed", [])


@pytest.mark.asyncio
async def test_unknown_model_rejected(tool):
    out = await tool.execute(
        agent_id="a1", project_id="p1",
        prompt="cat", model="gpt-image-1",
    )
    assert "model" in out.get("error", "").lower()
    assert "nicht unterstützt" in out.get("error", "")


# ────────────────────────────────────────────── Key-Check (vor Submit)


@pytest.mark.asyncio
async def test_no_key_returns_error_without_submit(tool, svc, monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_image, "settings",
        SimpleNamespace(
            llm_config=tmp_path / "nope.json",
            llm_env=tmp_path / "nope_env",
        ),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    out = await tool.execute(agent_id="a1", project_id="p1", prompt="cat")
    assert "API-Key fehlt" in out.get("error", "")
    # Wichtig: kein Job-Müll — list() muss leer sein.
    assert svc.list() == []


@pytest.mark.asyncio
async def test_tool_without_job_service_returns_error(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    bare = ImageGenerateTool()   # kein job_service injiziert
    out = await bare.execute(agent_id="a1", project_id="p1", prompt="cat")
    assert "JobService" in out.get("error", "")


# ────────────────────────────────────────────── Happy Path


@pytest.mark.asyncio
async def test_tool_returns_job_id_and_artifact_urls(tool, svc, monkeypatch, _patch_request):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    out = await tool.execute(
        agent_id="a1", project_id="p1",
        prompt="a cat in space", aspect_ratio="16:9",
    )
    assert out["status"] == "succeeded"
    assert out["job_id"].startswith("job_")
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert art["filename"] == "image_0.png"
    assert art["mime"] == "image/png"
    assert art["download_url"] == f"/me/jobs/{out['job_id']}/artifacts/image_0.png"
    assert "error" not in out

    # Meta in Jobs-Store: input_summary korrekt gekürzt/normiert.
    meta = svc.get(out["job_id"])
    assert meta.type == "image"
    assert meta.provider == "minimax"
    assert meta.input_summary["aspect_ratio"] == "16:9"
    assert meta.input_summary["prompt"] == "a cat in space"
    assert meta.agent_id == "a1"
    assert meta.project_id == "p1"
    assert meta.created_by is None   # Phase 1


@pytest.mark.asyncio
async def test_input_summary_truncates_long_prompt(tool, svc, monkeypatch, _patch_request):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    long_prompt = "cat " * 200  # 800 chars
    out = await tool.execute(agent_id="a", project_id="p", prompt=long_prompt)
    assert out["status"] == "succeeded"
    meta = svc.get(out["job_id"])
    assert len(meta.input_summary["prompt"]) <= 200


# ────────────────────────────────────────────── Runner-Failure


@pytest.mark.asyncio
async def test_runner_error_surfaces_as_failed(tool, svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_request(**kwargs):
        raise minimax_image.MinimaxImageError("MiniMax rate limit (429) — später erneut")

    monkeypatch.setattr(minimax_image, "request_image", fake_request)

    out = await tool.execute(agent_id="a", project_id="p", prompt="cat")
    assert out["status"] == "failed"
    assert "rate limit" in out.get("error", "").lower()
    # Kein Traceback im error.
    assert "Traceback" not in out.get("error", "")
    assert "File \"" not in out.get("error", "")
    # Keine Artifacts geschrieben.
    assert out["artifacts"] == []


# ────────────────────────────────────────────── Tool-Metadata


def test_tool_schema_only_exposes_image_01():
    tool = ImageGenerateTool()
    params = tool.parameters
    assert "prompt" in params["properties"]
    assert params["required"] == ["prompt"]
    model_enum = params["properties"]["model"]["enum"]
    assert model_enum == ["image-01"]
    assert "1:1" in params["properties"]["aspect_ratio"]["enum"]
    assert "16:9" in params["properties"]["aspect_ratio"]["enum"]


def test_tool_is_not_destructive_not_read_only():
    tool = ImageGenerateTool()
    assert tool.is_destructive is False
    assert tool.is_read_only is False
    assert tool.parallel_safe is True
