"""
hooks.py — 4-Type Hook System (#472)

Hook-Typen:
  command  — Shell-Kommando, JSON auf stdin, Exit 0 = allow
  http     — POST an Webhook-URL, HTTP 200 = allow
  prompt   — LLM-Evaluation (einfaches Ja/Nein)
  agent    — Mini-Agent Delegation (Stub)

Konfiguration in agent.yaml:
  hooks:
    before_tool:
      - type: command
        cmd: "/usr/local/bin/validate.sh"
        if: "shell_exec"          # optional: nur für dieses Tool
        timeout: 5                # Sekunden (Default: 5)
        async: false              # Default: blocking
      - type: http
        url: "http://localhost:9999/hook"
        async: true               # non-blocking, fire-and-forget
    after_tool:
      - type: command
        cmd: "/usr/local/bin/log.sh"
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5  # Sekunden


class HookType(str, Enum):
    COMMAND = "command"
    HTTP = "http"
    PROMPT = "prompt"
    AGENT = "agent"


@dataclass
class HookConfig:
    """Einzelne Hook-Definition aus agent.yaml."""
    type: HookType
    cmd: str | None = None          # für command
    url: str | None = None          # für http
    prompt_template: str | None = None  # für prompt
    if_tool: str | None = None      # Tool-Filter (optional)
    timeout: float = DEFAULT_TIMEOUT
    is_async: bool = False          # async = fire-and-forget

    @classmethod
    def from_dict(cls, raw: dict) -> HookConfig:
        """Parsed eine einzelne Hook-Config aus dem YAML-Dict."""
        try:
            hook_type = HookType(raw.get("type", ""))
        except ValueError:
            raise ValueError(f"Unbekannter Hook-Typ: {raw.get('type')}")

        return cls(
            type=hook_type,
            cmd=raw.get("cmd"),
            url=raw.get("url"),
            prompt_template=raw.get("prompt_template"),
            if_tool=raw.get("if"),
            timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
            is_async=bool(raw.get("async", False)),
        )


def parse_hooks_config(raw_hooks: dict | None) -> dict[str, list[HookConfig]]:
    """
    Parsed den 'hooks'-Block aus agent.yaml.
    Returns: {"before_tool": [...], "after_tool": [...]}
    """
    if not raw_hooks or not isinstance(raw_hooks, dict):
        return {}

    result: dict[str, list[HookConfig]] = {}
    for event_name, hook_list in raw_hooks.items():
        if not isinstance(hook_list, list):
            logger.warning("hooks.%s ist keine Liste — übersprungen", event_name)
            continue
        configs = []
        for i, raw in enumerate(hook_list):
            if not isinstance(raw, dict):
                logger.warning("hooks.%s[%d] ist kein Dict — übersprungen", event_name, i)
                continue
            try:
                configs.append(HookConfig.from_dict(raw))
            except ValueError as e:
                logger.warning("hooks.%s[%d] ungültig: %s", event_name, i, e)
        if configs:
            result[event_name] = configs
    return result


async def run_hooks(
    event: str,
    context: dict[str, Any],
    hooks: list[HookConfig],
) -> bool:
    """
    Führt alle Hooks für ein Event aus.
    Returns True wenn alle Hooks erlauben, False wenn einer blockiert.

    context enthält mindestens:
      - tool_name: str
      - tool_input: dict
      - agent_id: str
      - project_id: str
    Für after_tool zusätzlich:
      - result: Any
    """
    tool_name = context.get("tool_name", "")

    for hook in hooks:
        # Tool-Filter prüfen
        if hook.if_tool and hook.if_tool != tool_name:
            continue

        if hook.is_async:
            # Fire-and-forget: startet Task im Hintergrund, blockiert nicht
            asyncio.create_task(_run_single_hook(hook, context))
            continue

        # Blocking: Hook muss erlauben
        allowed = await _run_single_hook(hook, context)
        if not allowed:
            logger.info(
                "Hook blockiert: event=%s type=%s tool=%s",
                event, hook.type.value, tool_name,
            )
            return False

    return True


async def _run_single_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """
    Führt einen einzelnen Hook aus. Returns True = allow, False = block.
    Fehler/Timeout = allow (fail-open, damit kaputte Hooks nicht das System lahmlegen).
    """
    try:
        result = await asyncio.wait_for(
            _dispatch_hook(hook, context),
            timeout=hook.timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(
            "Hook Timeout (%ss): type=%s cmd/url=%s",
            hook.timeout, hook.type.value, hook.cmd or hook.url or "?",
        )
        return True  # fail-open
    except Exception as e:
        logger.warning("Hook Fehler: type=%s — %s", hook.type.value, e)
        return True  # fail-open


async def _dispatch_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """Dispatched an den richtigen Hook-Runner."""
    if hook.type == HookType.COMMAND:
        return await _run_command_hook(hook, context)
    elif hook.type == HookType.HTTP:
        return await _run_http_hook(hook, context)
    elif hook.type == HookType.PROMPT:
        return await _run_prompt_hook(hook, context)
    elif hook.type == HookType.AGENT:
        return await _run_agent_hook(hook, context)
    else:
        logger.warning("Unbekannter Hook-Typ: %s", hook.type)
        return True


# ── Command Hook ────────────────────────────────────────────────────────────

async def _run_command_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """
    Startet Shell-Kommando, sendet Context als JSON auf stdin.
    Exit 0 = allow, non-zero = block.
    """
    if not hook.cmd:
        logger.warning("Command-Hook ohne 'cmd' — übersprungen (allow)")
        return True

    payload = json.dumps(context, default=str, ensure_ascii=False)

    proc = await asyncio.create_subprocess_shell(
        hook.cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=payload.encode("utf-8"))

    if proc.returncode == 0:
        logger.debug("Command-Hook erlaubt: %s", hook.cmd)
        return True
    else:
        reason = stderr.decode("utf-8", errors="replace").strip() or f"Exit {proc.returncode}"
        logger.info("Command-Hook blockiert: %s — %s", hook.cmd, reason)
        return False


# ── HTTP Hook ───────────────────────────────────────────────────────────────

async def _run_http_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """
    POST an Webhook-URL. HTTP 200 = allow, alles andere = block.
    Nutzt httpx (bereits im Projekt verfügbar).
    """
    if not hook.url:
        logger.warning("HTTP-Hook ohne 'url' — übersprungen (allow)")
        return True

    try:
        import httpx
    except ImportError:
        logger.error("httpx nicht verfügbar für HTTP-Hook")
        return True  # fail-open

    payload = json.dumps(context, default=str, ensure_ascii=False)

    async with httpx.AsyncClient(timeout=hook.timeout) as client:
        resp = await client.post(
            hook.url,
            content=payload,
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code == 200:
        logger.debug("HTTP-Hook erlaubt: %s", hook.url)
        return True
    else:
        logger.info("HTTP-Hook blockiert: %s — Status %d", hook.url, resp.status_code)
        return False


# ── Prompt Hook ─────────────────────────────────────────────────────────────

async def _run_prompt_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """
    Sendet Hook-Context an LLM mit einfacher Ja/Nein-Frage.
    Basale Implementierung: prüft ob Tool-Name in einer Blocklist steht.
    Vollständige LLM-Evaluation in Phase 2.
    """
    template = hook.prompt_template or (
        "Soll der Tool-Aufruf '{tool_name}' mit den Argumenten {tool_input} erlaubt werden? "
        "Antworte nur mit 'ja' oder 'nein'."
    )

    prompt_text = template.format(
        tool_name=context.get("tool_name", "?"),
        tool_input=json.dumps(context.get("tool_input", {}), default=str, ensure_ascii=False),
        agent_id=context.get("agent_id", "?"),
        project_id=context.get("project_id", "?"),
    )

    # Phase 1: Einfache LLM-Abfrage über litellm
    try:
        import litellm
        response = await litellm.acompletion(
            model="gpt-4o-mini",  # Günstiges Model für Hook-Evaluation
            messages=[
                {"role": "system", "content": "Du bist ein Security-Hook. Antworte nur mit 'ja' oder 'nein'."},
                {"role": "user", "content": prompt_text},
            ],
            max_tokens=10,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip().lower()
        allowed = answer in ("ja", "yes", "allow", "erlaubt")
        logger.info("Prompt-Hook: %s → %s", prompt_text[:80], "allow" if allowed else "block")
        return allowed
    except Exception as e:
        logger.warning("Prompt-Hook LLM-Fehler: %s — fail-open", e)
        return True  # fail-open


# ── Agent Hook (Stub) ──────────────────────────────────────────────────────

async def _run_agent_hook(hook: HookConfig, context: dict[str, Any]) -> bool:
    """
    Stub für Agent-Hook. Delegiert an Mini-Agent für Verification.
    Phase 2: Vollständige Implementierung mit eigenem Agent-Context.
    """
    logger.info(
        "Agent-Hook (Stub): tool=%s — allow (nicht implementiert)",
        context.get("tool_name", "?"),
    )
    return True  # Stub: immer erlauben
