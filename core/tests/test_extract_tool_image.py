"""test_extract_tool_image.py — Inline-Bild-Rendering aus Tool-Results (#773 Followup).

Prüft `_extract_tool_image` aus orchestrator_stream für beide Shapes:
- Legacy ``image_base64`` (Browser/Screenshot-Tools)
- Jobs-basierte ``artifacts`` mit download_url + image-Mime (image_generate).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_stream import _extract_tool_image


# ─────────────────────────────────────────── Legacy-Pfad: image_base64


def test_image_base64_ergibt_data_uri_event():
    result = {"image_base64": "AAAA", "format": "png"}
    raw = _extract_tool_image(result, "browser_screenshot")
    assert raw is not None
    data = json.loads(raw)
    assert data["type"] == "tool_image"  # Frontend-Parser matched darauf
    assert data["tool_image"] == "data:image/png;base64,AAAA"
    assert data["tool_name"] == "browser_screenshot"


def test_artifacts_event_hat_type_feld():
    """Regression: ohne `type: tool_image` im JSON findet der SSE-Parser
    das Event nicht und UI zeigt kein Bild an."""
    result = {
        "artifacts": [{"mime": "image/png", "download_url": "/x.png"}],
    }
    data = json.loads(_extract_tool_image(result, "image_generate"))
    assert data["type"] == "tool_image"


def test_image_base64_format_default_png():
    result = {"image_base64": "BBBB"}
    raw = _extract_tool_image(result, "foo")
    assert json.loads(raw)["tool_image"].startswith("data:image/png;base64,")


# ─────────────────────────────────────────── Jobs-Pfad: artifacts (neu in #773)


def test_artifacts_mit_image_mime_ergeben_url_event():
    """image_generate-Output: erstes image-Artifact wird inline angezeigt."""
    result = {
        "job_id":   "job_xyz",
        "status":   "completed",
        "artifacts": [{
            "filename":     "image_0.png",
            "mime":         "image/png",
            "size":         12345,
            "download_url": "/me/jobs/job_xyz/artifacts/image_0.png",
        }],
    }
    raw = _extract_tool_image(result, "image_generate")
    assert raw is not None
    data = json.loads(raw)
    assert data["tool_image"] == "/me/jobs/job_xyz/artifacts/image_0.png"
    assert data["tool_name"] == "image_generate"


def test_artifacts_nimmt_erstes_image_artifact():
    result = {
        "artifacts": [
            {"mime": "text/plain", "download_url": "/x"},
            {"mime": "image/jpeg", "download_url": "/second.jpg"},
            {"mime": "image/png",  "download_url": "/third.png"},
        ],
    }
    data = json.loads(_extract_tool_image(result, "t"))
    assert data["tool_image"] == "/second.jpg"


def test_artifacts_ohne_image_mime_gibt_none():
    """Video/Music-Jobs: wir rendern NICHT als Bild. Sound/Video-Cards
    sind ein separater Feature-Scope."""
    result = {
        "artifacts": [{
            "mime": "video/mp4",
            "download_url": "/me/jobs/x/artifacts/v.mp4",
        }],
    }
    assert _extract_tool_image(result, "video_generate") is None


def test_artifacts_leer_gibt_none():
    assert _extract_tool_image({"artifacts": []}, "t") is None


def test_artifacts_ohne_url_gibt_none():
    result = {"artifacts": [{"mime": "image/png"}]}  # kein download_url
    assert _extract_tool_image(result, "t") is None


# ─────────────────────────────────────────── Robustheit


def test_non_dict_result_gibt_none():
    assert _extract_tool_image("some string", "t") is None
    assert _extract_tool_image(None, "t") is None
    assert _extract_tool_image(["list"], "t") is None


def test_dict_ohne_bild_felder_gibt_none():
    assert _extract_tool_image({"status": "ok", "data": 1}, "t") is None


def test_malformed_artifacts_entry_wird_uebersprungen():
    """Defensiv: ein einzelner kaputter Artifact-Eintrag darf den ganzen
    Extractor nicht crashen lassen."""
    result = {
        "artifacts": [
            "not-a-dict",
            None,
            {"mime": "image/png", "download_url": "/ok.png"},
        ],
    }
    data = json.loads(_extract_tool_image(result, "t"))
    assert data["tool_image"] == "/ok.png"
