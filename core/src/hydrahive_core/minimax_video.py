"""minimax_video.py — MiniMax Text-to-Video-API + Job-Runner (#688 Phase 2).

MiniMax Video ist asynchron:

  POST /video_generation            → {task_id}
  GET  /query/video_generation?...  → {status, file_id?, error_message?}
  GET  /files/retrieve?file_id=...  → {file: {download_url}}
  GET  <download_url>               → MP4-Bytes (no auth)

Das :func:`build_video_runner` orchestriert den gesamten Loop. Das Tool
``video_generate`` submittet den Runner via JobService und returnt SOFORT
mit ``{job_id, status=queued, poll_url, message}`` — kein Await.

Phase 2 ist maximal konservativ:
- Nur ``model=MiniMax-Hailuo-2.3`` (Text-to-Video).
- Nur ``duration=6`` und ``resolution=1080P`` (einzige doku-bestätigte Werte).
- Nur n=1 Video pro Call.
- Keine image-to-video, first-and-last-frame, subject-reference.
- Max-Dauer: 15 Minuten.
- Poll-Intervall: 10 s (laut offizieller MiniMax-Doku).

Share pattern mit :mod:`minimax_image`: identisches Key-Lookup, identische
Base-URL (beide nutzen ``orchestrator_llm._minimax_media_base_url`` —
Media-Endpoint ``/v1``, getrennt vom Chat-Endpoint ``/anthropic``), kein
neuer Config-Eintrag, kein neuer Runtime-Dir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from .jobs_service import JobContext

logger = logging.getLogger(__name__)


# Endpoints — base_url enthält bereits /v1 (siehe _minimax_media_base_url in orchestrator_llm).
MINIMAX_VIDEO_CREATE_ENDPOINT   = "/video_generation"
MINIMAX_VIDEO_STATUS_ENDPOINT   = "/query/video_generation"
MINIMAX_FILES_RETRIEVE_ENDPOINT = "/files/retrieve"

DEFAULT_MODEL     = "MiniMax-Hailuo-2.3"
DEFAULT_DURATION  = 6
DEFAULT_RESOLUTION = "1080P"

# Phase-2 Whitelists — alles außerhalb wird abgelehnt, damit wir keine
# undefined MiniMax-400er durchreichen.
ALLOWED_MODELS:      frozenset[str] = frozenset({DEFAULT_MODEL})
ALLOWED_DURATIONS:   frozenset[int] = frozenset({6})
ALLOWED_RESOLUTIONS: frozenset[str] = frozenset({"1080P"})

POLL_INTERVAL_SECONDS = 10.0
MAX_POLL_SECONDS      = 15 * 60   # 15 min hard cap
# Progress-Heuristik: 60 Polls ≈ 10 min erwartete Dauer.
_EXPECTED_POLLS       = 60

MAX_INPUT_SUMMARY_PROMPT = 200
DEFAULT_HTTP_TIMEOUT     = 30.0


class MinimaxVideoError(Exception):
    """Generic client error. Message ist user-facing — ohne Key/URL/Path."""


# ─────────────────────────────────────────────── Key + Base-URL


def _minimax_video_api_key() -> str | None:
    """Identisch zum LLM-/Image-Lookup: Env > providers.minimax.api_key > llm_env."""
    env_key = (os.environ.get("MINIMAX_API_KEY") or "").strip()
    if env_key:
        return env_key

    try:
        from .router_llm import _cached_json_load
        cfg = _cached_json_load(str(settings.llm_config), {"providers": {}})
        cfg_key = (cfg.get("providers", {}).get("minimax", {}).get("api_key") or "").strip()
        if cfg_key:
            return cfg_key
    except Exception as exc:  # pragma: no cover
        logger.debug("minimax_video: llm_config lookup failed: %s", exc)

    try:
        env_file = settings.llm_env
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("MINIMAX_API_KEY="):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
    except OSError:  # pragma: no cover
        pass

    return None


def _minimax_video_base_url() -> str:
    # #773 Followup: Media-Endpoint = /v1, nicht /anthropic (Chat).
    from .orchestrator_llm import _minimax_media_base_url
    return _minimax_media_base_url()


# ─────────────────────────────────────────────── HTTP-Primitives


async def _post_json(
    client: httpx.AsyncClient, url: str, api_key: str, payload: dict,
) -> dict:
    try:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
        )
    except httpx.TimeoutException:
        raise MinimaxVideoError("request timeout") from None
    except httpx.HTTPError as exc:
        raise MinimaxVideoError(f"request failed: {type(exc).__name__}") from None

    if resp.status_code == 401:
        raise MinimaxVideoError("MiniMax API-Key abgelehnt (401)")
    if resp.status_code == 429:
        raise MinimaxVideoError("MiniMax rate limit (429) — später erneut")
    if resp.status_code >= 400:
        raise MinimaxVideoError(f"MiniMax API HTTP {resp.status_code}")

    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        raise MinimaxVideoError("MiniMax API returned non-JSON body") from None


async def _get_json(
    client: httpx.AsyncClient, url: str, api_key: str, params: dict,
) -> dict:
    try:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
        )
    except httpx.TimeoutException:
        raise MinimaxVideoError("request timeout") from None
    except httpx.HTTPError as exc:
        raise MinimaxVideoError(f"request failed: {type(exc).__name__}") from None

    if resp.status_code == 401:
        raise MinimaxVideoError("MiniMax API-Key abgelehnt (401)")
    if resp.status_code == 429:
        raise MinimaxVideoError("MiniMax rate limit (429) — später erneut")
    if resp.status_code >= 400:
        raise MinimaxVideoError(f"MiniMax API HTTP {resp.status_code}")

    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        raise MinimaxVideoError("MiniMax API returned non-JSON body") from None


def _maybe_base_resp_error(body: dict) -> str | None:
    """MiniMax liefert oft {base_resp: {status_msg, status_code}} auch bei 200."""
    if not isinstance(body, dict):
        return None
    br = body.get("base_resp")
    if isinstance(br, dict):
        msg = str(br.get("status_msg") or "").strip()
        if msg and msg.lower() not in ("success", "ok"):
            return msg[:200]
    return None


# ─────────────────────────────────────────────── High-level API


async def start_video_task(
    *,
    prompt: str,
    model: str,
    duration: int,
    resolution: str,
    api_key: str,
    base_url: str,
    client: httpx.AsyncClient,
) -> str:
    """POST /video_generation → task_id."""
    url = base_url.rstrip("/") + MINIMAX_VIDEO_CREATE_ENDPOINT
    body = await _post_json(client, url, api_key, {
        "prompt":     prompt,
        "model":      model,
        "duration":   duration,
        "resolution": resolution,
    })
    err = _maybe_base_resp_error(body)
    if err:
        raise MinimaxVideoError(f"MiniMax: {err}")
    task_id = body.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise MinimaxVideoError("MiniMax response missing task_id")
    return task_id


async def get_video_task_status(
    *,
    task_id: str,
    api_key: str,
    base_url: str,
    client: httpx.AsyncClient,
) -> dict:
    """GET /query/video_generation → raw status dict.

    Response-Felder (laut offizieller Doku):
        status:         "Success" | "Fail" | processing-string
        file_id:        present bei Success
        error_message:  present bei Fail
    """
    url = base_url.rstrip("/") + MINIMAX_VIDEO_STATUS_ENDPOINT
    body = await _get_json(client, url, api_key, {"task_id": task_id})
    # base_resp-Fehler auch bei 200 möglich, aber Status-Query kann legitim
    # einen "processing" base_resp liefern — nur bei echten Fehlerstati hart
    # werfen. Wir geben body 1:1 zurück und lassen den Runner entscheiden.
    return body if isinstance(body, dict) else {}


async def retrieve_video_file(
    *,
    file_id: str,
    api_key: str,
    base_url: str,
    client: httpx.AsyncClient,
) -> str:
    """GET /files/retrieve → download_url (pre-signed, kein Auth für Download)."""
    url = base_url.rstrip("/") + MINIMAX_FILES_RETRIEVE_ENDPOINT
    body = await _get_json(client, url, api_key, {"file_id": file_id})
    err = _maybe_base_resp_error(body)
    if err:
        raise MinimaxVideoError(f"MiniMax: {err}")
    file_obj = body.get("file") if isinstance(body, dict) else None
    download_url = (file_obj or {}).get("download_url") if isinstance(file_obj, dict) else None
    if not isinstance(download_url, str) or not download_url:
        raise MinimaxVideoError("MiniMax response missing download_url")
    return download_url


async def download_video(
    *,
    download_url: str,
    client: httpx.AsyncClient,
) -> bytes:
    """GET download_url → video bytes. Kein Auth-Header (pre-signed)."""
    try:
        resp = await client.get(download_url)
    except httpx.TimeoutException:
        raise MinimaxVideoError("video download timeout") from None
    except httpx.HTTPError as exc:
        raise MinimaxVideoError(f"video download failed: {type(exc).__name__}") from None

    if resp.status_code >= 400:
        raise MinimaxVideoError(f"video download HTTP {resp.status_code}")
    return resp.content


# ─────────────────────────────────────────────── Runner-Factory


RunnerFn = Callable[["JobContext"], Awaitable[None]]

_TERMINAL_SUCCESS = {"success"}
_TERMINAL_FAILURE = {"fail", "failed"}


def _classify_status(raw_status: str) -> str:
    """Normalisiert MiniMax-Status-Strings auf {success, failure, processing}."""
    v = (raw_status or "").strip().lower()
    if v in _TERMINAL_SUCCESS:
        return "success"
    if v in _TERMINAL_FAILURE:
        return "failure"
    return "processing"


def _compute_poll_progress(poll_count: int) -> int:
    """Lineare 10–90-Heuristik; ab ~60 Polls sättigt's bei 90."""
    if poll_count <= 0:
        return 10
    pct = 10 + int((poll_count / _EXPECTED_POLLS) * 80)
    return min(90, max(10, pct))


def build_video_runner(
    *,
    prompt: str,
    model: str,
    duration: int,
    resolution: str,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    poll_interval_seconds: float | None = None,
    max_poll_seconds: float | None = None,
    _start=None,
    _status=None,
    _retrieve=None,
    _download=None,
) -> RunnerFn:
    """Baut den Async-Runner. Test-Hooks `_start/_status/_retrieve/_download`
    ersetzen die HTTP-Funktionen — Production übergibt nichts.
    """
    start_fn    = _start    or start_video_task
    status_fn   = _status   or get_video_task_status
    retrieve_fn = _retrieve or retrieve_video_file
    download_fn = _download or download_video
    poll_int    = poll_interval_seconds if poll_interval_seconds is not None else POLL_INTERVAL_SECONDS
    max_secs    = max_poll_seconds if max_poll_seconds is not None else MAX_POLL_SECONDS

    async def runner(ctx: "JobContext") -> None:
        ctx.update_progress(5, "Authenticating")
        api_key = _minimax_video_api_key()
        if not api_key:
            raise MinimaxVideoError("MiniMax API-Key fehlt")
        base_url = _minimax_video_base_url()

        ctx.check_cancelled()

        client = http_client_factory() if http_client_factory else httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT)
        try:
            ctx.update_progress(10, "Submitting to MiniMax")
            task_id = await start_fn(
                prompt=prompt, model=model, duration=duration, resolution=resolution,
                api_key=api_key, base_url=base_url, client=client,
            )

            # Poll-Loop
            started_at = time.monotonic()
            poll_count = 0
            file_id: str | None = None
            while True:
                ctx.check_cancelled()
                elapsed = time.monotonic() - started_at
                if elapsed > max_secs:
                    raise MinimaxVideoError(f"Video generation timeout after {int(max_secs)}s")
                await asyncio.sleep(poll_int)
                poll_count += 1

                ctx.check_cancelled()
                status_body = await status_fn(
                    task_id=task_id, api_key=api_key, base_url=base_url, client=client,
                )
                raw_status = status_body.get("status", "")
                state = _classify_status(raw_status)

                if state == "success":
                    file_id = status_body.get("file_id")
                    if not isinstance(file_id, str) or not file_id:
                        raise MinimaxVideoError("MiniMax: Success ohne file_id")
                    break
                if state == "failure":
                    err_msg = str(status_body.get("error_message") or "MiniMax Video Fail").strip()
                    raise MinimaxVideoError(f"MiniMax: {err_msg[:200]}")

                ctx.update_progress(_compute_poll_progress(poll_count), f"Generating (poll {poll_count})")

            ctx.check_cancelled()
            ctx.update_progress(92, "Retrieving file")
            download_url = await retrieve_fn(
                file_id=file_id, api_key=api_key, base_url=base_url, client=client,
            )

            ctx.check_cancelled()
            ctx.update_progress(95, "Downloading")
            data = await download_fn(download_url=download_url, client=client)

            if not data:
                raise MinimaxVideoError("MiniMax returned empty video file")

            ctx.record_artifact(data, "video_0.mp4", "video/mp4")
            ctx.update_progress(100, "done")
        finally:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass

    return runner
