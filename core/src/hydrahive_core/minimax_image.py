"""minimax_image.py — MiniMax Text-to-Image-API-Client + Job-Runner (#679 Phase 1).

MiniMax Image-Generation ist synchron: ein POST auf
``${base}/v1/image_generation`` liefert base64-kodierte Bilder direkt.
Wir wrappen das trotzdem ins #687-Job-Framework, damit Artifacts,
Cancellation, History und das spätere Video/Music-Pattern konsistent sind.

Nicht in Phase 1:
- Mehrere Bilder pro Request (n > 1).
- Image-to-Image / subject_reference.
- Multi-Provider-Dispatch.

Auth teilt sich die Quelle mit dem LLM-Pfad. Base-URL läuft über den
Media-spezifischen Resolver ``orchestrator_llm._minimax_media_base_url``
(getrennt vom Chat-Endpoint ``/anthropic``, weil Image/Video/Music bei
MiniMax unter ``/v1`` liegen — #773 Followup).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from .jobs_service import JobContext

logger = logging.getLogger(__name__)


MINIMAX_IMAGE_ENDPOINT = "/image_generation"  # base_url enthält bereits /v1
DEFAULT_MODEL = "image-01"
DEFAULT_ASPECT_RATIO = "1:1"

# Vollständige Liste aus der offiziellen MiniMax-Doku.
ALLOWED_ASPECT_RATIOS: frozenset[str] = frozenset({
    "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9",
})

# Konservativer Prompt-Cap für input_summary. Der volle Prompt geht an
# MiniMax, aber nicht in die Job-Meta (die bei /me/jobs sichtbar ist).
MAX_INPUT_SUMMARY_PROMPT = 200

# Default-Timeout für den synchronen Image-Call. MiniMax liefert meistens
# innerhalb von 5–15 s, aber wir lassen Luft für große Modelle.
DEFAULT_TIMEOUT_SECONDS = 90


class MinimaxImageError(Exception):
    """Client-seitiger Fehler (Auth, Request-Shape, API-Rückgabe).

    Message ist user-facing — enthält weder API-Key noch Request-Body-Inhalte.
    """


# ─────────────────────────────────────────────── Key-/Base-URL-Lookup


def _minimax_image_api_key() -> str | None:
    """Delegiert an :func:`orchestrator_llm._minimax_api_key`."""
    from .orchestrator_llm import _minimax_api_key
    return _minimax_api_key()


    return None


def _minimax_image_base_url() -> str:
    """#773 Followup: Image-Endpoint = /v1 (nicht /anthropic wie der Chat).

    Zusammenlegen mit ``_minimax_base_url`` war der Live-Bug: der Chat-
    Resolver liefert ``/anthropic`` (Token-Plan), daraus wurde
    ``/anthropic/image_generation`` → 404. Image nutzt jetzt
    ``_minimax_media_base_url`` mit Default ``/v1``.
    """
    from .orchestrator_llm import _minimax_media_base_url

    return _minimax_media_base_url()


# ─────────────────────────────────────────────── HTTP-Client


async def request_image(
    *,
    prompt: str,
    aspect_ratio: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> list[bytes]:
    """POST an MiniMax Image-Gen. Returnt eine Liste PNG-Bytes (Phase 1: genau 1).

    Wirft :class:`MinimaxImageError` bei HTTP-Fehlern, Timeouts oder
    malformed Response. Der Error-Text ist generisch — keine API-Key-Bytes
    und kein Prompt-Text in der Message.
    """
    url = base_url.rstrip("/") + MINIMAX_IMAGE_ENDPOINT
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "base64",
        "n": 1,
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise MinimaxImageError("image request timeout") from None
        except httpx.HTTPError as exc:
            raise MinimaxImageError(f"image request failed: {type(exc).__name__}") from None

        if resp.status_code == 401:
            raise MinimaxImageError("MiniMax API-Key abgelehnt (401)")
        if resp.status_code == 429:
            raise MinimaxImageError("MiniMax rate limit (429) — später erneut")
        if resp.status_code >= 400:
            raise MinimaxImageError(f"MiniMax API HTTP {resp.status_code}")

        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            raise MinimaxImageError("MiniMax API returned non-JSON body") from None

        data = body.get("data") if isinstance(body, dict) else None
        images_b64 = (data or {}).get("image_base64") if isinstance(data, dict) else None
        if not isinstance(images_b64, list) or not images_b64:
            # MiniMax-Fehler-Shapes haben oft ein {"base_resp":{"status_code":...,
            # "status_msg":...}}-Feld. Wir geben status_msg weiter, aber gekürzt.
            if isinstance(body, dict):
                br = body.get("base_resp") or {}
                msg = str(br.get("status_msg") or "").strip()
                if msg:
                    raise MinimaxImageError(f"MiniMax: {msg[:200]}")
            raise MinimaxImageError("MiniMax response missing image_base64")

        out: list[bytes] = []
        for item in images_b64:
            if not isinstance(item, str) or not item:
                continue
            try:
                out.append(base64.b64decode(item, validate=True))
            except Exception:
                raise MinimaxImageError("MiniMax image_base64 decode failed") from None
        if not out:
            raise MinimaxImageError("MiniMax response image_base64 all empty")
        return out
    finally:
        if owns_client and client is not None:
            await client.aclose()


# ─────────────────────────────────────────────── Runner-Factory


RunnerFn = Callable[["JobContext"], Awaitable[None]]


def build_image_runner(
    *,
    prompt: str,
    aspect_ratio: str,
    model: str,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    _request_image=None,
) -> RunnerFn:
    """Baut einen Runner, der ctx.update_progress + ctx.record_artifact nutzt.

    ``http_client_factory`` und ``_request_image`` sind Test-Hooks —
    Production-Code übergibt beide nicht.
    """
    request_fn = _request_image or request_image

    async def runner(ctx: "JobContext") -> None:
        ctx.update_progress(5, "Authenticating")
        api_key = _minimax_image_api_key()
        if not api_key:
            raise MinimaxImageError("MiniMax API-Key fehlt")
        base_url = _minimax_image_base_url()

        ctx.check_cancelled()
        ctx.update_progress(20, "Requesting image from MiniMax")

        client = http_client_factory() if http_client_factory else None
        try:
            images = await request_fn(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                model=model,
                api_key=api_key,
                base_url=base_url,
                client=client,
            )
        finally:
            if client is not None:
                await client.aclose()

        ctx.check_cancelled()
        ctx.update_progress(80, "Saving artifact")

        for idx, data in enumerate(images):
            filename = f"image_{idx}.png"
            ctx.record_artifact(data, filename, "image/png")

        ctx.update_progress(100, "done")

    return runner
