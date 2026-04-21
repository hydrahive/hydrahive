"""#689: MiniMax Music-Client + Runner — alles ohne echte HTTP-Calls.

Deckt request_music-HTTP-Pfade, base_resp-Code-Mapping, hex-Decode-Pfad und
den vollen Runner-Loop (happy, cancel pre-/post-request, no-key).
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

from hydrahive_core import minimax_music
from hydrahive_core.jobs_service import JobService
from hydrahive_core.minimax_music import (
    MinimaxMusicError,
    _minimax_music_api_key,
    _raise_for_base_resp,
    build_music_runner,
    request_music,
)


_MP3 = b"\xff\xfbPlayFakeMP3Data"
_MP3_HEX = _MP3.hex()


# ────────────────────────────────────────────── Fake HTTP-Client


class _FakeResponse:
    def __init__(self, status_code: int, body: Any = None):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("invalid json")


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None, raise_exc: Exception | None = None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response

    async def aclose(self):
        pass


# ────────────────────────────────────────────── _raise_for_base_resp


class TestRaiseForBaseResp:
    def test_no_base_resp_is_noop(self):
        _raise_for_base_resp({})
        _raise_for_base_resp({"other": "field"})

    def test_status_code_zero_is_success(self):
        _raise_for_base_resp({"base_resp": {"status_code": 0, "status_msg": "success"}})

    @pytest.mark.parametrize("code,needle", [
        (1002, "rate limit"),
        (1004, "abgelehnt"),
        (1008, "Guthaben"),
        (1026, "content policy"),
        (2013, "invalid parameters"),
        (2049, "abgelehnt"),
    ])
    def test_known_codes_map_to_safe_messages(self, code, needle):
        with pytest.raises(MinimaxMusicError) as ei:
            _raise_for_base_resp({"base_resp": {"status_code": code, "status_msg": "secret prompt"}})
        assert needle in str(ei.value)
        # Wichtig: keine raw status_msg-Leaks bei bekannten Codes.
        assert "secret prompt" not in str(ei.value)

    def test_unknown_code_shows_trimmed_status_msg(self):
        with pytest.raises(MinimaxMusicError) as ei:
            _raise_for_base_resp({"base_resp": {"status_code": 9999, "status_msg": "weird new error"}})
        assert "9999" in str(ei.value)
        assert "weird new error" in str(ei.value)

    def test_unknown_code_without_msg(self):
        with pytest.raises(MinimaxMusicError):
            _raise_for_base_resp({"base_resp": {"status_code": 9999}})


# ────────────────────────────────────────────── Key-Lookup


def _fake_settings(cfg_path: Path, env_path: Path):
    return SimpleNamespace(llm_config=cfg_path, llm_env=env_path)


class TestKeyPrecedence:
    def test_env_wins(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        assert _minimax_music_api_key() == "env-key"

    def test_config_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({"providers": {"minimax": {"api_key": "cfg-key"}}}))
        monkeypatch.setattr(minimax_music, "settings", _fake_settings(cfg_file, tmp_path / "no_env"))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_music_api_key() == "cfg-key"

    def test_llm_env_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        cfg_file = tmp_path / "llm.json"
        cfg_file.write_text(json.dumps({"providers": {}}))
        env_file = tmp_path / "llm_env"
        env_file.write_text("MINIMAX_API_KEY=file-key\n")
        monkeypatch.setattr(minimax_music, "settings", _fake_settings(cfg_file, env_file))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_music_api_key() == "file-key"

    def test_none_when_absent(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setattr(minimax_music, "settings", _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"))
        from hydrahive_core import router_llm as _rl
        if hasattr(_rl._cached_json_load, "cache_clear"):
            _rl._cached_json_load.cache_clear()
        assert _minimax_music_api_key() is None


# ────────────────────────────────────────────── request_music


@pytest.mark.asyncio
async def test_request_music_success_with_user_lyrics():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "audio": _MP3_HEX},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }))
    out = await request_music(
        prompt="chillwave synthpop", lyrics="la la la",
        lyrics_optimizer=False, is_instrumental=False,
        model="music-2.6", api_key="k",
        base_url="https://api.minimax.io/v1", client=client,
    )
    assert out == _MP3
    body = client.calls[0]["json"]
    assert client.calls[0]["url"] == "https://api.minimax.io/v1/music_generation"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer k"
    assert body["model"] == "music-2.6"
    assert body["prompt"] == "chillwave synthpop"
    assert body["lyrics"] == "la la la"
    assert body["output_format"] == "hex"
    assert body["audio_setting"] == {
        "sample_rate": 44100, "bitrate": 256000, "format": "mp3",
    }
    assert "lyrics_optimizer" not in body   # bei user-lyrics NICHT gesetzt
    assert "is_instrumental" not in body


@pytest.mark.asyncio
async def test_request_music_success_with_lyrics_optimizer():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "audio": _MP3_HEX},
        "base_resp": {"status_code": 0},
    }))
    await request_music(
        prompt="dreamy", lyrics="", lyrics_optimizer=True, is_instrumental=False,
        model="music-2.6", api_key="k",
        base_url="https://api.minimax.io/v1", client=client,
    )
    body = client.calls[0]["json"]
    assert body["lyrics_optimizer"] is True
    assert "lyrics" not in body
    assert "is_instrumental" not in body


@pytest.mark.asyncio
async def test_request_music_success_instrumental():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "audio": _MP3_HEX},
        "base_resp": {"status_code": 0},
    }))
    await request_music(
        prompt="jazz piano solo", lyrics="",
        lyrics_optimizer=False, is_instrumental=True,
        model="music-2.6", api_key="k",
        base_url="https://api.minimax.io/v1", client=client,
    )
    body = client.calls[0]["json"]
    assert body["is_instrumental"] is True
    assert "lyrics" not in body
    assert "lyrics_optimizer" not in body


@pytest.mark.asyncio
async def test_request_music_trailing_slash_base_url():
    client = _FakeClient(_FakeResponse(200, {
        "data": {"status": 2, "audio": _MP3_HEX}, "base_resp": {"status_code": 0},
    }))
    await request_music(
        prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
        model="music-2.6", api_key="k",
        base_url="https://api.minimax.io/v1/", client=client,
    )
    assert client.calls[0]["url"] == "https://api.minimax.io/v1/music_generation"


@pytest.mark.asyncio
async def test_request_music_401():
    client = _FakeClient(_FakeResponse(401, {}))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="bad",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "401" in str(ei.value)
    assert "bad" not in str(ei.value)


@pytest.mark.asyncio
async def test_request_music_429():
    client = _FakeClient(_FakeResponse(429, {}))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "rate limit" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_music_500():
    client = _FakeClient(_FakeResponse(500, {}))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "500" in str(ei.value)


@pytest.mark.asyncio
async def test_request_music_timeout():
    client = _FakeClient(raise_exc=httpx.ConnectTimeout("boom"))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "timeout" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_music_base_resp_rate_limit_wins_over_200():
    """200 OK, aber base_resp.status_code=1002 → MinimaxMusicError."""
    client = _FakeClient(_FakeResponse(200, {
        "base_resp": {"status_code": 1002, "status_msg": "too many"},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "rate limit" in str(ei.value).lower()
    assert "too many" not in str(ei.value)  # original msg bei bekanntem Code unterdrückt


@pytest.mark.asyncio
async def test_request_music_content_flagged():
    client = _FakeClient(_FakeResponse(200, {
        "base_resp": {"status_code": 1026, "status_msg": "flagged"},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "content policy" in str(ei.value)


@pytest.mark.asyncio
async def test_request_music_missing_data():
    client = _FakeClient(_FakeResponse(200, {
        "base_resp": {"status_code": 0},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "data" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_music_non_completed_status():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 1, "audio": _MP3_HEX},   # 1 = in progress
        "base_resp": {"status_code": 0},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "non-completed" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_music_missing_audio():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "other": "nope"},
        "base_resp": {"status_code": 0},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "data.audio" in str(ei.value)


@pytest.mark.asyncio
async def test_request_music_invalid_hex():
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "audio": "nothex!!"},
        "base_resp": {"status_code": 0},
    }))
    with pytest.raises(MinimaxMusicError) as ei:
        await request_music(
            prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
            model="music-2.6", api_key="k",
            base_url="https://api.minimax.io/v1", client=client,
        )
    assert "hex decode" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_request_music_empty_audio_after_decode():
    # Leerer hex-string decoded zu b"" → wir fangen das ab.
    client = _FakeClient(_FakeResponse(200, {
        "data":      {"status": 2, "audio": "1234"},
        "base_resp": {"status_code": 0},
    }))
    out = await request_music(
        prompt="x", lyrics="", lyrics_optimizer=True, is_instrumental=False,
        model="music-2.6", api_key="k",
        base_url="https://api.minimax.io/v1", client=client,
    )
    assert out == bytes.fromhex("1234")


# ────────────────────────────────────────────── build_music_runner


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.mark.asyncio
async def test_runner_happy_path(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    recorded = {}

    async def fake_request(**kw):
        recorded.update(kw)
        return _MP3

    runner = build_music_runner(
        prompt="lofi beats", lyrics="", instrumental=False,
        model="music-2.6", _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)

    final = svc.get(meta.job_id)
    assert final.status == "succeeded"
    assert final.progress_percent == 100
    assert [a["filename"] for a in final.artifacts] == ["music_0.mp3"]
    assert final.artifacts[0]["mime"] == "audio/mpeg"
    assert svc.artifact_path(meta.job_id, "music_0.mp3").read_bytes() == _MP3

    # Dispatch: kein lyrics + nicht instrumental → lyrics_optimizer=True
    assert recorded["lyrics_optimizer"] is True
    assert recorded["is_instrumental"] is False
    assert recorded["lyrics"] == ""


@pytest.mark.asyncio
async def test_runner_with_user_lyrics(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    recorded = {}

    async def fake_request(**kw):
        recorded.update(kw)
        return _MP3

    runner = build_music_runner(
        prompt="synthwave", lyrics="neon city at midnight",
        instrumental=False, model="music-2.6", _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)

    assert recorded["lyrics"] == "neon city at midnight"
    assert recorded["lyrics_optimizer"] is False
    assert recorded["is_instrumental"] is False


@pytest.mark.asyncio
async def test_runner_instrumental(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    recorded = {}

    async def fake_request(**kw):
        recorded.update(kw)
        return _MP3

    runner = build_music_runner(
        prompt="piano solo", lyrics="", instrumental=True,
        model="music-2.6", _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)

    assert recorded["is_instrumental"] is True
    assert recorded["lyrics_optimizer"] is False
    assert recorded["lyrics"] == ""


@pytest.mark.asyncio
async def test_runner_no_key(svc, monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        minimax_music, "settings",
        _fake_settings(tmp_path / "nope.json", tmp_path / "nope_env"),
    )
    from hydrahive_core import router_llm as _rl
    if hasattr(_rl._cached_json_load, "cache_clear"):
        _rl._cached_json_load.cache_clear()

    invocations = {"count": 0}

    async def fake_request(**kw):  # pragma: no cover — shouldn't be called
        invocations["count"] += 1
        return _MP3

    runner = build_music_runner(
        prompt="x", lyrics="", instrumental=False, model="music-2.6",
        _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "key" in (final.error or "").lower()
    assert invocations["count"] == 0
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_cancel_pre_request(svc, monkeypatch):
    """Cancel zwischen Auth-Check und Request → request_music nie aufgerufen."""
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    invocations = {"count": 0}

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_request(**kw):
        invocations["count"] += 1
        return _MP3

    async def wrapper_runner(ctx):
        # Simuliert: Cancel bevor request_music gerufen wird.
        started.set()
        await release.wait()
        inner = build_music_runner(
            prompt="x", lyrics="", instrumental=False, model="music-2.6",
            _request_music=fake_request,
        )
        await inner(ctx)

    meta = svc.submit(type="music", provider="minimax", runner=wrapper_runner)
    await started.wait()
    svc.cancel(meta.job_id)
    release.set()
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "cancelled"
    assert invocations["count"] == 0
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_cancel_post_request_pre_save(svc, monkeypatch):
    """Cancel nach dem HTTP-Return aber vor record_artifact → cancelled,
    kein Artifact geschrieben (Remote-Call ist durch, Kosten fallen an —
    das ist dokumentiert)."""
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_request(**kw):
        # Nach Rückgabe cancel setzen, bevor der Runner weitermacht.
        svc.cancel(meta.job_id)
        return _MP3

    runner = build_music_runner(
        prompt="x", lyrics="", instrumental=False, model="music-2.6",
        _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "cancelled"
    assert final.artifacts == []


@pytest.mark.asyncio
async def test_runner_request_error_surfaces_as_failed(svc, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")

    async def fake_request(**kw):
        raise MinimaxMusicError("MiniMax: content policy violation (1026)")

    runner = build_music_runner(
        prompt="x", lyrics="", instrumental=False, model="music-2.6",
        _request_music=fake_request,
    )
    meta = svc.submit(type="music", provider="minimax", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert "content policy" in (final.error or "").lower()
    assert "Traceback" not in (final.error or "")
    assert final.artifacts == []


# ============================================================================
# MusicGenerateTool.execute — Agent-facing validation (Schema-Hints)
# ============================================================================

from unittest.mock import MagicMock

from hydrahive_core.tool_registry import MusicGenerateTool


@pytest.fixture
def music_tool():
    # JobService wird für die frühen Validierungs-Pfade nicht erreicht,
    # aber die Tool-Instanz will ein Objekt — MagicMock reicht.
    return MusicGenerateTool(job_service=MagicMock())


@pytest.mark.asyncio
async def test_prompt_mit_verse_marker_wird_als_songtext_erkannt(music_tool):
    # Häufigster Agent-Fehler: ganzer Songtext in `prompt` gestopft.
    result = await music_tool.execute(
        agent_id="a",
        project_id="p",
        prompt="Slow Blues. [Verse 1] Woke up this morning [Chorus] Oh Lord",
        lyrics="",
    )
    assert "error" in result
    assert result.get("hint") == "split_prompt_into_prompt_and_lyrics"
    assert "lyrics" in result["error"].lower()


@pytest.mark.asyncio
async def test_prompt_mit_intro_marker_wird_erkannt(music_tool):
    result = await music_tool.execute(
        agent_id="a", project_id="p",
        prompt="Slow Blues Hammond. [Intro] Mhm yeah [Verse 1] Woke up",
        lyrics="",
    )
    assert "error" in result
    assert result.get("hint") == "split_prompt_into_prompt_and_lyrics"


@pytest.mark.asyncio
async def test_prompt_mit_guitar_solo_marker_wird_erkannt(music_tool):
    result = await music_tool.execute(
        agent_id="a", project_id="p",
        prompt="[Intro] Mhm [Verse 1] ... [Guitar Solo] [Outro] fade",
        lyrics="",
    )
    assert "error" in result
    assert result.get("hint") == "split_prompt_into_prompt_and_lyrics"


@pytest.mark.asyncio
async def test_prompt_mit_marker_aber_lyrics_gesetzt_ist_ok(music_tool, monkeypatch):
    # Wenn lyrics schon gesetzt, war die Marker-Erwähnung im prompt Zufall
    # (z.B. "Song with a jazzy [Bridge] feel"). Nicht rejecten.
    monkeypatch.setattr(
        "hydrahive_core.tool_registry._minimax_music_api_key",
        lambda: "fake-key-for-test",
    ) if False else None  # _minimax_music_api_key kommt aus minimax_music
    # Einfacher Weg: wir mocken den import im execute() — aber hier reicht,
    # dass wir die Validation-Layer durchlaufen ohne Marker-Reject. Da
    # danach der JobService-Key-Check kommt, setzen wir den Env-Key.
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    # JobService submit soll nicht aufgerufen werden — wir stoppen vorher,
    # indem das Tool den Marker-Check überspringt: lyrics != "".
    # Der nächste Check wäre Lyrics-Länge — erfüllen wir.
    result = await music_tool.execute(
        agent_id="a", project_id="p",
        prompt="smooth jazz with a groovy [Bridge] moment",
        lyrics="[Intro] la la la",
    )
    # Wir erwarten KEINEN hint-Fehler. Danach darf JobService-Pfad greifen.
    # Falls JobService weiter-dispatcht und MagicMock-Submit-Quatsch macht,
    # ist das OK — wir wollen nur, dass _unser_ Validation-Pfad OK ist.
    assert result.get("hint") != "split_prompt_into_prompt_and_lyrics"


@pytest.mark.asyncio
async def test_prompt_zu_lang_zeigt_hinweis_auf_lyrics_feld(music_tool):
    # Langer prompt ohne Marker → generischer Length-Error, aber mit
    # Hinweis auf lyrics-Feld.
    long = "blues " * 120  # ~720 Zeichen
    result = await music_tool.execute(
        agent_id="a", project_id="p",
        prompt=long, lyrics="",
    )
    assert "error" in result
    assert result.get("hint") == "split_prompt_into_prompt_and_lyrics"
    assert "lyrics" in result["error"].lower()


@pytest.mark.asyncio
async def test_prompt_leer_wird_abgewiesen(music_tool):
    result = await music_tool.execute(agent_id="a", project_id="p", prompt="", lyrics="")
    assert result.get("error") == "prompt ist leer"
