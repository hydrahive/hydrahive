"""minimax_music.py — MiniMax Music-Generation-API + Job-Runner (#689 Phase 3).

Anders als #688 Video, aber wie #679 Image: die Music-API ist SYNCHRON —
ein einziger POST liefert das fertige Audio direkt im Response-Body als
hex-encoded bytes. Kein task_id, kein Polling, kein Retrieve.

Flow:

    POST /music_generation
    Request:  {model, prompt, lyrics?, lyrics_optimizer?, is_instrumental?,
               audio_setting, output_format="hex"}
    Response: {data: {status:2, audio: "<hex>"}, base_resp: {status_code, status_msg}}

Der Tool-Wrapper ist deshalb blockierend (analog ImageGenerateTool):
``await svc._tasks[job_id]`` → final state in Tool-Response.

Phase 3 maximal konservativ:
- Nur ``model=music-2.6`` (einziges doku-bestätigtes).
- ``output_format=hex`` (keine 24h-URL-TTL, ein HTTP-Call total).
- ``audio_setting`` hart ``{44100, 256000, "mp3"}`` (Doku-Beispielwerte).
- Dispatch: user-lyrics ODER lyrics_optimizer ODER is_instrumental, nie kombiniert.
- Prompt-Cap 500, Lyrics-Cap 3000 Zeichen.
- MP3 als Audio-Format.

``base_resp.status_code``-Mapping für die sechs dokumentierten Fehlerfälle
(1002 rate limit, 1004/2049 auth, 1008 balance, 1026 content, 2013 params)
damit User-facing-Messages konsistent und ohne Key/Prompt-Leak sind.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from .jobs_service import JobContext

logger = logging.getLogger(__name__)


MINIMAX_MUSIC_ENDPOINT = "/music_generation"

DEFAULT_MODEL     = "music-2.6"
ALLOWED_MODELS: frozenset[str] = frozenset({DEFAULT_MODEL})

AUDIO_FORMAT   = "mp3"
AUDIO_MIME     = "audio/mpeg"
AUDIO_FILENAME = "music_0.mp3"

# Doku-Beispielwerte; wir exponen sie nicht, sondern fixieren sie.
AUDIO_SETTING_DEFAULT: dict = {
    "sample_rate": 44100,
    "bitrate":     256000,
    "format":      AUDIO_FORMAT,
}

MAX_PROMPT_LEN = 500
MAX_LYRICS_LEN = 3000

MAX_INPUT_SUMMARY_PROMPT = 200
MAX_INPUT_SUMMARY_LYRICS = 200

DEFAULT_HTTP_TIMEOUT = 120.0


class MinimaxMusicError(Exception):
    """Generischer Client-Fehler. User-facing — ohne Key/Prompt/Pfad."""


# base_resp.status_code → user-facing message (offizielle MiniMax-Doku).
_BASE_RESP_MESSAGES: dict[int, str] = {
    1002: "MiniMax rate limit (1002) — später erneut",
    1004: "MiniMax API-Key abgelehnt (1004)",
    1008: "MiniMax: Guthaben erschöpft (1008)",
    1026: "MiniMax: content policy violation (1026)",
    2013: "MiniMax: invalid parameters (2013)",
    2049: "MiniMax API-Key abgelehnt (2049)",
}


# ─────────────────────────────────────────────── Key + Base-URL


def _minimax_music_api_key() -> str | None:
    """Delegiert an :func:`orchestrator_llm._minimax_api_key`."""
    from .orchestrator_llm import _minimax_api_key
    return _minimax_api_key()


def _minimax_music_base_url() -> str:
    # #773 Followup: Media-Endpoint = /v1, nicht /anthropic (Chat).
    from .orchestrator_llm import _minimax_media_base_url
    return _minimax_media_base_url()


# ─────────────────────────────────────────────── Error-Helper


def _raise_for_base_resp(body: dict) -> None:
    """Wirft MinimaxMusicError wenn base_resp.status_code != 0."""
    if not isinstance(body, dict):
        return
    br = body.get("base_resp")
    if not isinstance(br, dict):
        return
    code = br.get("status_code")
    try:
        code_int = int(code) if code is not None else 0
    except (TypeError, ValueError):
        code_int = -1
    if code_int == 0:
        return
    mapped = _BASE_RESP_MESSAGES.get(code_int)
    if mapped:
        raise MinimaxMusicError(mapped)
    msg = str(br.get("status_msg") or "").strip()
    if msg:
        raise MinimaxMusicError(f"MiniMax ({code_int}): {msg[:200]}")
    raise MinimaxMusicError(f"MiniMax error ({code_int})")


# ─────────────────────────────────────────────── HTTP-Client


async def request_music(
    *,
    prompt: str,
    lyrics: str,
    lyrics_optimizer: bool,
    is_instrumental: bool,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """POST /music_generation → MP3-Bytes aus hex-encoded data.audio.

    Caller ist verantwortlich für Dispatch-Regel (lyrics vs optimizer vs
    instrumental). Diese Funktion macht nur den HTTP-Call + Decode.

    Returnt die Audio-Bytes. Wirft :class:`MinimaxMusicError` bei HTTP-Fehlern,
    base_resp-Fehlern, malformed Response oder hex-Decode-Fehlern.
    """
    url = base_url.rstrip("/") + MINIMAX_MUSIC_ENDPOINT

    payload: dict = {
        "model":          model,
        "prompt":         prompt,
        "output_format":  "hex",
        "audio_setting":  dict(AUDIO_SETTING_DEFAULT),
    }
    if is_instrumental:
        payload["is_instrumental"] = True
    if lyrics:
        payload["lyrics"] = lyrics
    if lyrics_optimizer:
        payload["lyrics_optimizer"] = True

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            raise MinimaxMusicError("music request timeout") from None
        except httpx.HTTPError as exc:
            raise MinimaxMusicError(f"music request failed: {type(exc).__name__}") from None

        # HTTP-Level-Errors zuerst (MiniMax mischt manchmal 200 + base_resp-Error).
        if resp.status_code == 401:
            raise MinimaxMusicError("MiniMax API-Key abgelehnt (HTTP 401)")
        if resp.status_code == 429:
            raise MinimaxMusicError("MiniMax rate limit (HTTP 429)")
        if resp.status_code >= 400:
            raise MinimaxMusicError(f"MiniMax API HTTP {resp.status_code}")

        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            raise MinimaxMusicError("MiniMax API returned non-JSON body") from None

        _raise_for_base_resp(body)

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise MinimaxMusicError("MiniMax response missing data")

        status = data.get("status")
        # status=2 → completed; andere Werte signalisieren unfertig/Fehler.
        # Doku-Beispiel zeigt nur 2 als Success bei Sync-Response.
        if status not in (2, "2"):
            raise MinimaxMusicError(
                f"MiniMax returned non-completed status ({status})"
            )

        hex_audio = data.get("audio")
        if not isinstance(hex_audio, str) or not hex_audio:
            raise MinimaxMusicError("MiniMax response missing data.audio")

        try:
            audio_bytes = bytes.fromhex(hex_audio)
        except ValueError:
            raise MinimaxMusicError("MiniMax data.audio hex decode failed") from None

        if not audio_bytes:
            raise MinimaxMusicError("MiniMax returned empty audio")

        return audio_bytes
    finally:
        if owns_client and client is not None:
            await client.aclose()


# ─────────────────────────────────────────────── Runner-Factory


RunnerFn = Callable[["JobContext"], Awaitable[None]]


def build_music_runner(
    *,
    prompt: str,
    lyrics: str,
    instrumental: bool,
    model: str,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    _request_music=None,
) -> RunnerFn:
    """Baut den Runner für einen Music-Job.

    Test-Hook ``_request_music`` ersetzt den HTTP-Call in Unit-Tests —
    Production übergibt nichts.

    Dispatch-Regel (Caller des Tools hat bereits validiert):
    - ``lyrics`` gesetzt → `lyrics=<text>`, `lyrics_optimizer=False`.
    - Kein lyrics, ``instrumental=False`` → `lyrics_optimizer=True`.
    - ``instrumental=True``, keine lyrics → `is_instrumental=True`.
    - ``instrumental=True`` + lyrics → bereits vom Tool rejected.
    """
    request_fn = _request_music or request_music

    lyrics_optimizer = (not instrumental) and (not lyrics)
    use_lyrics       = lyrics if lyrics else ""

    async def runner(ctx: "JobContext") -> None:
        ctx.update_progress(5, "Authenticating")
        api_key = _minimax_music_api_key()
        if not api_key:
            raise MinimaxMusicError("MiniMax API-Key fehlt")
        base_url = _minimax_music_base_url()

        ctx.check_cancelled()
        ctx.update_progress(20, "Requesting music from MiniMax")

        client = http_client_factory() if http_client_factory else None
        try:
            audio = await request_fn(
                prompt=prompt,
                lyrics=use_lyrics,
                lyrics_optimizer=lyrics_optimizer,
                is_instrumental=instrumental,
                model=model,
                api_key=api_key,
                base_url=base_url,
                client=client,
            )
        finally:
            if client is not None:
                await client.aclose()

        # Cancel-Check NACH HTTP-Response, VOR record_artifact. Wenn der User
        # während des Sync-Calls cancelled, kommt der Remote-Call trotzdem
        # durch (Kosten fallen an), aber wir sparen uns das Artifact-Write.
        ctx.check_cancelled()
        ctx.update_progress(80, "Saving artifact")

        ctx.record_artifact(audio, AUDIO_FILENAME, AUDIO_MIME)
        ctx.update_progress(100, "done")

    return runner
