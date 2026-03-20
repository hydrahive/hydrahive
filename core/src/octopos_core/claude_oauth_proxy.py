"""
claude_oauth_proxy.py — Lokaler Proxy fuer Claude Max OAuth Token (#38)

Nimmt OpenAI-kompatible Requests, setzt anthropic-beta: oauth-2025-04-20
Header und leitet an api.anthropic.com weiter.

litellm nutzt dann:
  model:    "openai/claude-sonnet-4-6"
  api_base: "http://127.0.0.1:3456/v1"
"""

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION  = "2023-06-01"
ANTHROPIC_BETA     = "oauth-2025-04-20"
TOKEN_FILE         = "/etc/octopos/claude_oauth_token"

app = FastAPI(title="Claude OAuth Proxy", docs_url=None, redoc_url=None)


def get_token() -> str:
    """OAuth-Token aus Datei laden."""
    try:
        return Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(503, "Claude OAuth Token nicht konfiguriert. "
                                 "Bitte in der LLM-Config hinterlegen.")


def openai_to_anthropic(body: dict) -> dict:
    """OpenAI Chat-Format → Anthropic Messages-Format."""
    messages = body.get("messages", [])
    system   = ""
    filtered = []

    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            filtered.append({"role": m["role"], "content": m.get("content", "")})

    result: dict = {
        "model":      body.get("model", "claude-sonnet-4-6"),
        "max_tokens": body.get("max_tokens", 4096),
        "messages":   filtered,
    }
    if system:
        result["system"] = system
    if body.get("temperature") is not None:
        result["temperature"] = body["temperature"]
    if body.get("stream"):
        result["stream"] = True
    if body.get("tools"):
        result["tools"] = body["tools"]
    return result


def anthropic_to_openai(body: dict, model: str) -> dict:
    """Anthropic Response → OpenAI Chat-Format."""
    content = body.get("content", [{}])
    text    = content[0].get("text", "") if content else ""
    usage   = body.get("usage", {})
    return {
        "id":      body.get("id", ""),
        "object":  "chat.completion",
        "model":   model,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": text},
            "finish_reason": body.get("stop_reason", "stop"),
        }],
        "usage": {
            "prompt_tokens":     usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens":      usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "claude-sonnet-4-6",  "object": "model"},
            {"id": "claude-opus-4-6",    "object": "model"},
            {"id": "claude-haiku-4-5-20251001", "object": "model"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    token    = get_token()
    body     = await request.json()
    model    = body.get("model", "claude-sonnet-4-6")
    stream   = body.get("stream", False)
    payload  = openai_to_anthropic(body)

    headers = {
        "Authorization":   f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta":  ANTHROPIC_BETA,
        "Content-Type":    "application/json",
    }

    if stream:
        async def generate():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{ANTHROPIC_API_BASE}/v1/messages",
                    headers=headers,
                    json=payload,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{ANTHROPIC_API_BASE}/v1/messages",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text[:500])

    return anthropic_to_openai(resp.json(), model)


@app.get("/health")
async def health():
    has_token = Path(TOKEN_FILE).exists()
    return {"status": "ok", "token_configured": has_token}
