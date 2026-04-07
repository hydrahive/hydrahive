"""
orchestrator_llm.py — LLM-Call-Maschinerie

Standalone-Funktionen für LLM-Aufrufe (kein Orchestrator-State nötig):
- Failover-Logik (_should_failover, _llm_with_retry)
- Token-Loader (_load_claude_oauth_token, _load_openai_codex_token)
- Model-Resolution (_resolve_model)
- Einzelner LLM-Call (_llm_call_single)
- Failover-LLM-Call (_llm_call)
- Anthropic OAuth-Call (non-streaming)
- OpenAI Codex OAuth-Call
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import litellm

from .settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- Prompt Caching

def _apply_cache_control(messages: list[dict], is_anthropic: bool) -> list[dict]:
    """
    Fügt Anthropic Prompt Caching Cache-Breakpoints ein (max 4 erlaubt).
    - System-Message:        letzter Block → cache_control (größter Gewinn)
    - Ältere History:        alles außer den letzten 4 Nachrichten → cache_control
    Für nicht-Anthropic-Modelle werden die Messages unverändert zurückgegeben.
    """
    if not is_anthropic:
        return messages

    non_sys = [m for m in messages if m.get("role") != "system"]
    cache_cutoff = max(0, len(non_sys) - 4)   # Index ab dem NICHT gecacht wird
    non_sys_i = 0
    result = []

    for m in messages:
        role    = m.get("role", "")
        content = m.get("content", "")

        if role == "system":
            if isinstance(content, str):
                result.append({
                    "role": "system",
                    "content": [{"type": "text", "text": content,
                                 "cache_control": {"type": "ephemeral"}}],
                })
            elif isinstance(content, list) and content:
                new_c = list(content)
                if not new_c[-1].get("cache_control"):
                    new_c[-1] = {**new_c[-1], "cache_control": {"type": "ephemeral"}}
                result.append({**m, "content": new_c})
            else:
                result.append(m)

        elif role in ("user", "assistant") and non_sys_i < cache_cutoff and isinstance(content, str) and content:
            result.append({
                **m,
                "content": [{"type": "text", "text": content,
                             "cache_control": {"type": "ephemeral"}}],
            })
            non_sys_i += 1

        else:
            result.append(m)
            if role in ("user", "assistant"):
                non_sys_i += 1

    return result


# ---------------------------------------------------------------- Failover

_FAILOVER_SIGNALS = [
    "401", "authentication_error", "expired", "oauth token has expired",
    "invalid api key", "invalid x-api-key", "unauthorized",
    "402", "payment", "credit", "quota", "insufficient",
    "429", "rate_limit", "rate limit",
    "529", "overloaded", "capacity", "credit_balance",
    "your credit balance is too low",
    "exceeded your current quota",
    "this request would exceed",
    "billing",
]


def _should_failover(exc: Exception) -> bool:
    """True wenn der Fehler einen Modell-Wechsel rechtfertigt (Quota/Overload)."""
    err = str(exc).lower()
    return any(s in err for s in _FAILOVER_SIGNALS)


async def _llm_with_retry(coro_factory, max_attempts: int = 3, base_delay: float = 1.0):
    """
    Retry-Wrapper für LLM-Calls.
    - 429 Rate-Limit: sofort retry mit Backoff
    - 5xx Server-Fehler: retry
    - 401/403 Auth: kein Retry
    - Timeout: retry
    Exponential Backoff mit 10% Jitter, max 30s.
    """
    import random as _random

    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()

            # Auth-Fehler → kein Retry
            if any(x in err_str for x in ["401", "403", "unauthorized", "forbidden", "authentication"]):
                raise

            # Quota/Rate-Limit erschöpft → kein Retry, sofort an Aufrufer weitergeben (Failover)
            if any(x in err_str for x in ["rate_limit", "rate limit", "429", "quota", "credit", "billing", "payment"]):
                raise

            # Letzter Versuch → aufgeben
            if attempt == max_attempts - 1:
                raise

            # Backoff berechnen: 1s, 2s, 4s... max 30s + Jitter
            delay = min(base_delay * (2 ** attempt), 30.0)
            delay *= (1 + _random.uniform(-0.1, 0.1))  # 10% Jitter

            logger.warning(
                "LLM-Fehler (Versuch %d/%d): %s — retry in %.1fs",
                attempt + 1, max_attempts, str(e)[:80], delay
            )
            await asyncio.sleep(delay)

    raise last_exc


# ---------------------------------------------------------------- Token-Loader

def _load_claude_oauth_token() -> str:
    """Laedt Claude OAuth Token — prüft zuerst ANTHROPIC_API_KEY (langlebiger
    Terminal-Token), dann claude_oauth_token (Console-OAuth, ggf. mit Refresh)."""
    import json as _json, time as _time

    # Priorität 1: Terminal-Token aus llm_env (1 Jahr gültig, kein Refresh nötig)
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key and env_key.startswith("sk-ant-oat01-"):
        return env_key

    # Priorität 2: Console-OAuth-Token (kurzlebig, mit Refresh)
    token_file = settings.claude_oauth_token
    try:
        raw = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    # Legacy-Format: nur der Token als String
    if raw.startswith("sk-ant-oat01-"):
        return raw

    # Neues JSON-Format mit refresh_token
    try:
        data = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        return ""

    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    expires_at = data.get("expires_at", 0)

    # Token noch gültig (5 Min Puffer)?
    if access_token and _time.time() < expires_at - 300:
        return access_token

    # Refresh nötig
    if not refresh_token:
        return access_token or ""

    try:
        refreshed = _refresh_claude_token(refresh_token, token_file)
        if refreshed:
            return refreshed
    except Exception as _e:
        _logger = logging.getLogger(__name__)
        _logger.warning("Claude OAuth refresh fehlgeschlagen: %s", _e)

    return access_token or ""


def _refresh_claude_token(refresh_token: str, token_file: Path) -> str:
    """Synchroner Token-Refresh via Anthropic OAuth endpoint."""
    import json as _json, time as _time
    import urllib.request

    body = _json.dumps({
        "grant_type": "refresh_token",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "refresh_token": refresh_token,
    }).encode()

    req = urllib.request.Request(
        "https://console.anthropic.com/v1/oauth/token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read())

    new_access = data.get("access_token", "")
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)

    if not new_access:
        return ""

    token_file.write_text(
        _json.dumps({
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_at": int(_time.time()) + expires_in,
        }, indent=2),
        encoding="utf-8",
    )
    token_file.chmod(0o600)

    _logger = logging.getLogger(__name__)
    _logger.info("Claude OAuth Token refreshed (expires in %ds)", expires_in)
    return new_access


def _load_openai_codex_token() -> dict | None:
    """Laedt OpenAI Codex OAuth Token (ChatGPT Plus/Pro)."""
    import json as _json
    token_file = settings.openai_codex_token
    try:
        data = _json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token") and data.get("account_id"):
            return data
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- Provider-Check

def check_llm_provider_available(models: list[str], ollama_base_url: str | None = None) -> str | None:
    """
    Prüft ob für die übergebenen Modelle ein Provider verfügbar ist.
    Gibt None zurück wenn OK, sonst eine nutzerfreundliche Fehlermeldung.
    ollama_base_url: wenn gesetzt (WKS-Ollama), wird dieser Endpunkt geprüft statt localhost.
    """
    import os
    import socket
    from urllib.parse import urlparse

    for model in models:
        if not model:
            continue
        is_claude  = model.startswith(("claude-", "anthropic/"))
        is_openai  = model.startswith(("gpt-", "o1-", "o3-", "openai/", "openai-codex/"))
        is_ollama  = model.startswith(("ollama/", "ollama_chat/")) or (
            not is_claude and not is_openai and "/" not in model
        )

        if is_claude:
            if _load_claude_oauth_token():
                return None
            if os.environ.get("ANTHROPIC_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                for p in ("anthropic", "claude_max"):
                    if cfg.get("providers", {}).get(p, {}).get("api_key", "").strip():
                        return None
            except Exception:
                pass

        elif is_openai:
            if _load_openai_codex_token():
                return None
            if os.environ.get("OPENAI_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                if cfg.get("providers", {}).get("openai", {}).get("api_key", "").strip():
                    return None
            except Exception:
                pass

        elif is_ollama:
            if ollama_base_url:
                # WKS-Ollama: Endpunkt auf der Workstation prüfen
                try:
                    parsed = urlparse(ollama_base_url)
                    host = parsed.hostname or "127.0.0.1"
                    port = parsed.port or 11434
                    with socket.create_connection((host, port), timeout=2):
                        return None
                except Exception:
                    pass
            else:
                try:
                    with socket.create_connection(("127.0.0.1", 11434), timeout=1):
                        return None
                except Exception:
                    pass

    return (
        "## ⚠️ Kein LLM-Provider konfiguriert\n\n"
        "Um HydraHive zu nutzen, einen Provider unter **Einstellungen → LLM** einrichten:\n\n"
        "**Cloud (kein GPU nötig — empfohlen zum Starten)**\n"
        "• **Anthropic Claude** — API-Key auf [console.anthropic.com](https://console.anthropic.com) → Einstellungen → LLM → Anthropic\n"
        "• **OpenAI GPT-4** — API-Key auf [platform.openai.com](https://platform.openai.com) → Einstellungen → LLM → OpenAI\n"
        "• **Claude Max** (Abo) — Einstellungen → LLM → Claude Max → OAuth verbinden\n\n"
        "**Lokal via Ollama (GPU empfohlen: NVIDIA RTX 3060+, 8 GB VRAM)**\n"
        "• `ollama pull mistral-nemo:12b` → Einstellungen → LLM → Ollama-URL eintragen"
    )




def _load_llm_config() -> dict:
    import json as _json
    try:
        return _json.loads(settings.llm_config.read_text())
    except (OSError, ValueError):
        return {"providers": {}}


# ---------------------------------------------------------------- Model-Resolution

def _resolve_model(model: str, ollama_base_url: str | None = None) -> tuple[str, str | None]:
    """
    Gibt (litellm_model, api_base) zurück.
    Provider-Prefix (z.B. anthropic/, openai/) → direkt weiterreichen.
    Claude/GPT-Modellnamen → passenden Provider-Prefix ergänzen.
    Kein Prefix, kein bekannter Cloud-Name → Ollama auf localhost.
    ollama_base_url: wenn gesetzt, wird statt localhost dieser Endpunkt genutzt (WKS-Ollama).
    """
    # Wenn kein ollama_base_url aber Modell Ollama: WKS-URL aus User-Config suchen
    if not ollama_base_url and (model.startswith("ollama/") or "/" not in model):
        try:
            # 1. Globale Ollama-Config
            from .router_llm import _load_llm_config
            cfg = _load_llm_config()
            ollama_base_url = cfg.get("providers", {}).get("ollama", {}).get("base_url")
        except Exception:
            pass
        if not ollama_base_url:
            try:
                # 2. Erste WKS-Config aus users.json (für WKS-Ollama Fallback)
                import json as _j
                from pathlib import Path as _P
                users = _j.loads(settings.users_config.read_text())
                for u in users.values():
                    wks = u.get("wks", {})
                    if wks.get("ip"):
                        ollama_base_url = f"http://{wks['ip']}:{wks.get('ollama_port', 11434)}"
                        break
            except Exception:
                pass
    ollama_base = ollama_base_url or "http://localhost:11434"
    # ollama/ → ollama_chat/ damit /api/chat (mit Tool Calling) statt /api/generate genutzt wird
    if model.startswith("ollama/"):
        return f"ollama_chat/{model[len('ollama/'):]}", ollama_base
    if "/" in model:
        return model, None
    # Bekannte Cloud-Modell-Prefixe automatisch ergänzen
    if model.startswith(("claude-",)):
        return f"anthropic/{model}", None
    if model.startswith(("gpt-", "o1-", "o3-")):
        return f"openai/{model}", None
    # Kein Prefix → lokales Ollama-Modell (chat)
    return f"ollama_chat/{model}", ollama_base


# ---------------------------------------------------------------- OAuth Calls

async def _anthropic_oauth_call(
    agent_cfg,
    messages:       list[dict],
    tools:          list[dict] | None,
    token:          str,
    model_override: str | None = None,
):
    """
    Direkter Anthropic SDK Call mit OAuth-Token (Claude Max Subscription).
    Setzt anthropic-beta: oauth-2025-04-20 Header wie OpenClaw.
    Gibt ein litellm-kompatibles Response-Objekt zurück.
    """
    import anthropic as _anthropic
    import json as _json
    from types import SimpleNamespace

    # api_key="" verhindert dass der SDK ANTHROPIC_API_KEY aus env liest
    client = _anthropic.AsyncAnthropic(
        api_key="",
        auth_token=token,
        timeout=300.0,
        default_headers={
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14,prompt-caching-2024-07-31",
            "user-agent":     "claude-cli/2.1.62",
            "x-app":          "cli",
        },
    )

    # System-Message extrahieren + OpenAI→Anthropic-Format konvertieren
    system_msg = ""
    filtered   = []
    for m in messages:
        role = m.get("role", "")
        if role == "system":
            system_msg = m.get("content", "")
            continue

        # OpenAI tool-result → Anthropic tool_result
        if role == "tool":
            tool_result_block = {
                "type":        "tool_result",
                "tool_use_id": m.get("tool_call_id", "unknown"),
                "content":     m.get("content", ""),
            }
            if filtered and filtered[-1]["role"] == "user" and isinstance(filtered[-1].get("content"), list):
                filtered[-1]["content"].append(tool_result_block)
            else:
                filtered.append({"role": "user", "content": [tool_result_block]})
            continue

        # OpenAI assistant mit tool_calls → Anthropic tool_use
        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls:
            asst_content = []
            if m.get("content"):
                asst_content.append({"type": "text", "text": m["content"]})
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    inp = _json.loads(fn.get("arguments", "{}"))
                except Exception:
                    inp = {}
                asst_content.append({
                    "type":  "tool_use",
                    "id":    tc.get("id", "unknown"),
                    "name":  fn.get("name", "unknown"),
                    "input": inp,
                })
            filtered.append({"role": "assistant", "content": asst_content})
            continue

        # Normaler Text-Message
        filtered.append({"role": role, "content": m.get("content") or ""})

    # Consecutive gleiche Rollen mergen (Anthropic-Constraint) — nur für Text-Messages
    merged: list[dict] = []
    for m in filtered:
        if (merged and merged[-1]["role"] == m["role"]
                and isinstance(m.get("content"), str)
                and isinstance(merged[-1].get("content"), str)):
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    filtered = merged

    # Modell-Name normalisieren (openai/claude-... → claude-...)
    model = model_override or agent_cfg.llm.model
    for prefix in ("openai/", "anthropic/", "claude/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    if not model.startswith("claude-"):
        model = "claude-haiku-4-5"

    # OAuth erfordert Claude-Code-Identity als ersten System-Block
    # Letzter Block bekommt cache_control → gesamter System-Prompt wird gecacht
    oauth_system = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}]
    if system_msg:
        oauth_system.append({"type": "text", "text": system_msg,
                              "cache_control": {"type": "ephemeral"}})

    # Ältere History-Messages cachen (alle außer den letzten 4 User/Assistant-Turns)
    # Max 3 History-Blöcke (+ 1 System-Block = 4 total, Anthropic-Limit)
    cache_cutoff = max(0, len(filtered) - 4)
    history_cache_count = 0
    for idx, fm in enumerate(filtered):
        if history_cache_count >= 3:
            break
        if idx < cache_cutoff and fm.get("role") in ("user", "assistant"):
            content = fm.get("content", "")
            if isinstance(content, str) and content:
                filtered[idx] = {**fm, "content": [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]}
                history_cache_count += 1
            elif isinstance(content, list) and content and not content[-1].get("cache_control"):
                new_c = list(content)
                new_c[-1] = {**new_c[-1], "cache_control": {"type": "ephemeral"}}
                filtered[idx] = {**fm, "content": new_c}
                history_cache_count += 1

    kwargs: dict = {
        "model":       model,
        "max_tokens":  agent_cfg.llm.max_tokens,
        "messages":    filtered,
        "temperature": agent_cfg.llm.temperature,
        "system":      oauth_system,
    }
    if tools:
        kwargs["tools"] = [
            {
                "name":         t["function"]["name"],
                "description":  t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

    resp = await _llm_with_retry(lambda: client.messages.create(**kwargs))

    # Cache-Usage loggen (zeigt ob Prompt Caching aktiv ist)
    if hasattr(resp, "usage") and resp.usage:
        u = resp.usage
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read  = getattr(u, "cache_read_input_tokens", 0) or 0
        input_tok   = getattr(u, "input_tokens", 0) or 0
        logger.info(
            "cache [%s] input=%d cache_write=%d cache_read=%d (≈%.0f%% gecacht)",
            model, input_tok, cache_write, cache_read,
            100 * cache_read / max(input_tok, 1),
        )

    # Anthropic Response → litellm-kompatibles SimpleNamespace
    text = ""
    tool_calls_out = []
    for block in resp.content:
        if block.type == "text":
            text = block.text
        elif block.type == "tool_use":
            tool_calls_out.append(SimpleNamespace(
                id=block.id,
                type="function",
                function=SimpleNamespace(
                    name=block.name,
                    arguments=_json.dumps(block.input),
                )
            ))

    message = SimpleNamespace(
        role="assistant",
        content=text,
        tool_calls=tool_calls_out if tool_calls_out else None,
    )
    choice  = SimpleNamespace(message=message, finish_reason=resp.stop_reason)

    # Usage-Daten weiterreichen (für Usage-Seite und Rate-Limiter)
    usage_ns = None
    if hasattr(resp, "usage") and resp.usage:
        u = resp.usage
        usage_ns = SimpleNamespace(
            input_tokens          = getattr(u, "input_tokens", 0) or 0,
            output_tokens         = getattr(u, "output_tokens", 0) or 0,
            prompt_tokens         = getattr(u, "input_tokens", 0) or 0,
            completion_tokens     = getattr(u, "output_tokens", 0) or 0,
            cache_creation_input_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens     = getattr(u, "cache_read_input_tokens", 0) or 0,
        )

    return SimpleNamespace(choices=[choice], model=model, usage=usage_ns)


async def _openai_codex_call(
    agent_cfg,
    messages:    list[dict],
    tools:       list[dict] | None,
    token_data:  dict,
    model_name:  str,
    force_tools: bool = True,
):
    """
    ChatGPT Plus/Pro via Codex OAuth.
    Endpoint: chatgpt.com/backend-api/codex/responses (OpenAI Responses API).
    Die Codex API erfordert stream=true — wir sammeln alle Chunks zu einem Response.
    """
    import json as _json
    import httpx as _httpx
    from types import SimpleNamespace

    model_id = model_name
    if model_id.startswith("openai-codex/"):
        model_id = model_id[len("openai-codex/"):]

    access_token = token_data["access_token"]
    account_id   = token_data["account_id"]

    def _codex_item_id(tool_call: dict) -> str:
        item_id = str(tool_call.get("item_id") or "").strip()
        if item_id.startswith("fc_"):
            return item_id
        call_id = str(tool_call.get("id") or "").strip()
        if call_id.startswith("fc_"):
            return call_id
        if call_id.startswith("call_"):
            return "fc_" + call_id[len("call_"):]
        if call_id:
            return "fc_" + call_id.replace(" ", "_")
        return "fc_unknown"

    system_prompt = ""
    input_items: list = []
    for m in messages:
        role    = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "system":
            system_prompt = content
            continue
        if role == "tool":
            input_items.append({
                "type":    "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output":  content,
            })
            continue
        tc_list = m.get("tool_calls")
        if role == "assistant" and tc_list:
            if content:
                input_items.append({
                    "role":    "assistant",
                    "content": [{"type": "output_text", "text": content}],
                })
            for tc in tc_list:
                fn = tc.get("function", {})
                input_items.append({
                    "type":      "function_call",
                    "id":        _codex_item_id(tc),
                    "call_id":   tc.get("id", ""),
                    "name":      fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
            continue
        input_items.append({
            "role":    role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": content}],
        })

    resp_tools = None
    if tools:
        resp_tools = [
            {
                "type":        "function",
                "name":        t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters":  t["function"].get("parameters", {"type": "object", "properties": {}}),
                "strict":      None,
            }
            for t in tools
        ]

    payload: dict = {
        "model":               model_id,
        "input":               input_items,
        "store":               False,
        "stream":              True,
        "text":                {"verbosity": "medium"},
        "include":             ["reasoning.encrypted_content"],
        "parallel_tool_calls": True,
    }
    instructions = system_prompt
    if resp_tools:
        tool_names = ", ".join(t["name"] for t in resp_tools)
        tool_hint  = (
            f"\n\nDu hast {len(resp_tools)} Tools zur Verfügung: {tool_names}. "
            "Nutze sie aktiv und direkt — führe Befehle aus statt sie zu erklären. "
            "Frage nicht nach Erlaubnis, handle autonom."
        )
        instructions = (instructions + tool_hint) if instructions else tool_hint
    if instructions:
        payload["instructions"] = instructions
    if resp_tools:
        payload["tools"]       = resp_tools
        payload["tool_choice"] = "required" if force_tools else "auto"

    headers = {
        "Authorization":      f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta":        "responses=experimental",
        "originator":         "pi",
        "Content-Type":       "application/json",
    }

    text = ""
    accumulated_fn: dict[str, dict] = {}  # item_id → {id, call_id, name, arguments}

    async with _httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", "https://chatgpt.com/backend-api/codex/responses",
            headers=headers, json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"Codex API {resp.status_code}: {body.decode()[:300]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    ev = _json.loads(data_str)
                except Exception:
                    continue
                ev_type = ev.get("type", "")

                if ev_type == "response.output_text.delta":
                    text += ev.get("delta", "")

                elif ev_type == "response.output_item.added":
                    item = ev.get("item", {})
                    if item.get("type") == "function_call":
                        item_id  = item.get("id", "")
                        call_id  = item.get("call_id", "")
                        accumulated_fn[item_id] = {
                            "id":        item_id,
                            "call_id":   call_id,
                            "name":      item.get("name", ""),
                            "arguments": "",
                        }

                elif ev_type == "response.function_call_arguments.delta":
                    cid = ev.get("item_id", ev.get("call_id", ""))
                    if cid in accumulated_fn:
                        accumulated_fn[cid]["arguments"] += ev.get("delta", "")

                elif ev_type == "response.function_call_arguments.done":
                    cid = ev.get("item_id", ev.get("call_id", ""))
                    if cid in accumulated_fn:
                        accumulated_fn[cid]["arguments"] = ev.get("arguments", accumulated_fn[cid]["arguments"])

    tool_calls_out = [
        SimpleNamespace(
            id=fn["call_id"],
            item_id=fn["id"],
            type="function",
            function=SimpleNamespace(
                name=fn["name"],
                arguments=fn["arguments"] or "{}",
            ),
        )
        for fn in accumulated_fn.values()
    ]

    message = SimpleNamespace(
        role="assistant",
        content=text,
        tool_calls=tool_calls_out if tool_calls_out else None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model=model_id)


# ---------------------------------------------------------------- LLM Call Chain

async def _llm_call_single(
    model_name: str,
    agent_cfg,
    messages:   list[dict],
    tools:      list[dict] | None,
):
    """Ein einzelnes Modell aufrufen — ohne Failover-Logik."""
    # OpenAI Codex (ChatGPT Plus OAuth)
    if model_name.startswith("openai-codex/"):
        codex_token = _load_openai_codex_token()
        if codex_token:
            return await _openai_codex_call(agent_cfg, messages, tools, codex_token, model_name)

    # Claude: Terminal-Token (ANTHROPIC_API_KEY) → litellm, Console-OAuth → OAuth-Call
    is_claude = model_name.startswith(("claude-", "anthropic/"))
    if is_claude:
        env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_key and env_key.startswith("sk-ant-oat01-"):
            # Langlebiger Terminal-Token → über litellm als api_key (kein OAuth-Pfad)
            pass
        else:
            oauth_token = _load_claude_oauth_token()
            if oauth_token:
                return await _anthropic_oauth_call(agent_cfg, messages, tools, oauth_token, model_name)

    model, api_base = _resolve_model(model_name, agent_cfg.llm.ollama_base_url)
    is_anthropic = model.startswith(("anthropic/", "claude-"))
    cached_messages = _apply_cache_control(messages, is_anthropic)
    kwargs: dict = {
        "model":       model,
        "messages":    cached_messages,
        "temperature": agent_cfg.llm.temperature,
        "max_tokens":  agent_cfg.llm.max_tokens,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if tools:
        kwargs["tools"]       = tools
        kwargs["tool_choice"] = "auto"

    resp = await _llm_with_retry(lambda: litellm.acompletion(**kwargs, drop_params=True, timeout=300))

    # Cache-Usage loggen (#351: Anthropic + OpenAI Prompt Caching)
    try:
        u = getattr(resp, "usage", None)
        if u:
            # Anthropic: cache_creation_input_tokens / cache_read_input_tokens
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            cache_read  = getattr(u, "cache_read_input_tokens", 0) or 0
            input_tok   = getattr(u, "prompt_tokens", 0) or 0
            # OpenAI: prompt_tokens_details.cached_tokens
            details = getattr(u, "prompt_tokens_details", None)
            if details and not cache_read:
                cache_read = getattr(details, "cached_tokens", 0) or 0
            if cache_write or cache_read:
                logger.info(
                    "cache [%s] input=%d cache_write=%d cache_read=%d (≈%.0f%% gecacht)",
                    model, input_tok, cache_write, cache_read,
                    100 * cache_read / max(input_tok, 1),
                )
    except Exception:
        pass

    return resp


async def _llm_call(
    agent_cfg,
    messages: list[dict],
    tools:    list[dict] | None,
):
    """
    LLM-Call mit Failover: versucht primary model, dann fallback_models.
    Bei Quota/Overload-Fehler wird automatisch zum nächsten Modell gewechselt.
    """
    models = [agent_cfg.llm.model] + agent_cfg.llm.fallback_models
    last_exc: Exception = RuntimeError("Kein Modell konfiguriert")
    for i, model_name in enumerate(models):
        try:
            return await _llm_call_single(model_name, agent_cfg, messages, tools)
        except Exception as e:
            last_exc = e
            if i < len(models) - 1 and _should_failover(e):
                logger.warning(
                    "Modell '%s' nicht verfügbar (%s) — Failover auf '%s'",
                    model_name, str(e)[:80], models[i + 1],
                )
                continue
            raise
    raise last_exc
