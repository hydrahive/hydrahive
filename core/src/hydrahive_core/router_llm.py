from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .llm_config_validation import (
    LlmConfigValueError,
    clean_provider_base_url,
    clean_provider_secret,
)
from .settings import settings

LLM_CONFIG_FILE = str(settings.llm_config)


class LlmProviderConfig(BaseModel):
    provider: str
    # #616: api_key Optional — None (weggelassen) bedeutet "nicht ändern",
    # "" bedeutet "explizit löschen", alles andere setzt den Key.
    # So kann base_url geändert werden, ohne den bestehenden Key zu verlieren.
    api_key: str | None = None
    enabled: bool = True
    # #616/#682: optionaler Endpoint-Override (MiniMax, NVIDIA).
    # None (weggelassen) = nicht ändern, "" = Override löschen (→ Provider-
    # Default), alles andere setzt die URL nach Validierung.
    base_url: str | None = None


class CoachConfigRequest(BaseModel):
    model: str = ""
    enabled: bool = False


class SystemDefaultRequest(BaseModel):
    model: str = ""


class ClaudeOAuthTokenRequest(BaseModel):
    api_key: str


class OpenAICodexTokenRequest(BaseModel):
    access_token: str
    account_id: str
    refresh_token: str = ""


class OAuthExchangeRequest(BaseModel):
    code: str = ""
    state: str = ""
    code_and_state: str = ""


class OpenAICodexExchangeRequest(BaseModel):
    code: str = ""
    state: str = ""
    redirect_url: str = ""


class OllamaPullRequest(BaseModel):
    model: str


class EmbeddingConfigRequest(BaseModel):
    model: str
    voyage_api_key: str = ""


_oauth_pending: dict[str, dict] = {}

# #391: mtime-basierter Config-Cache
_config_cache: dict[str, tuple[float, dict]] = {}  # path → (mtime, data)


def _has_minimax_provider_key(providers: dict | None = None) -> bool:
    """#616: True wenn ein echter MiniMax-API-Key konfiguriert ist.

    Quellen (reine Key-Prüfung, enabled/base_url zählen NICHT):
    - providers.minimax.api_key
    - MINIMAX_API_KEY in os.environ
    - MINIMAX_API_KEY=... in settings.llm_env
    """
    if providers is None:
        providers = _load_llm_config().get("providers", {})
    mm = providers.get("minimax", {}) or {}
    if (mm.get("api_key") or "").strip():
        return True
    if os.environ.get("MINIMAX_API_KEY", "").strip():
        return True
    try:
        for line in settings.llm_env.read_text().splitlines():
            if not line.startswith("MINIMAX_API_KEY="):
                continue
            if line.split("=", 1)[1].strip():
                return True
    except OSError:
        pass
    return False


def _has_nvidia_provider_key(providers: dict | None = None) -> bool:
    """#684: True wenn ein echter NVIDIA-API-Key konfiguriert ist.

    Analog zu _has_minimax_provider_key — reine Key-Prüfung, enabled/base_url
    zählen NICHT. Quellen: providers.nvidia.api_key, NVIDIA_API_KEY (env),
    NVIDIA_API_KEY=... in settings.llm_env.
    """
    if providers is None:
        providers = _load_llm_config().get("providers", {})
    nv = providers.get("nvidia", {}) or {}
    if (nv.get("api_key") or "").strip():
        return True
    if os.environ.get("NVIDIA_API_KEY", "").strip():
        return True
    try:
        for line in settings.llm_env.read_text().splitlines():
            if not line.startswith("NVIDIA_API_KEY="):
                continue
            if line.split("=", 1)[1].strip():
                return True
    except OSError:
        pass
    return False


def _cached_json_load(path: str, default: dict | None = None) -> dict:
    """Lädt JSON mit mtime-Check — nur re-read wenn Datei geändert (#391)."""
    import json as _json
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return default or {}
    cached = _config_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = _json.loads(p.read_text())
        _config_cache[path] = (mtime, data)
        return data
    except (OSError, ValueError):
        return default or {}


def _load_llm_config() -> dict:
    return _cached_json_load(LLM_CONFIG_FILE, {"providers": {}})


def _save_llm_config(config: dict) -> None:
    import json as _json

    p = Path(LLM_CONFIG_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(config, indent=2), encoding="utf-8")
    p.chmod(0o600)


def _merge_provider_config(
    existing: dict,
    *,
    api_key: str | None,
    enabled: bool,
    base_url: str | None,
) -> dict:
    """Mergt einen LLM-Provider-Request in den gespeicherten Provider-Eintrag.

    Tri-state-Semantik:
    - ``api_key`` / ``base_url`` = None: Feld nicht im Request → nicht ändern.
    - ``base_url`` = "": Override explizit löschen (Provider-Default gilt wieder).
    - Wert: Feld validiert setzen.

    Die Validierung muss der Aufrufer bereits gemacht haben — diese Funktion
    ist reine State-Transition.
    """
    merged = dict(existing)
    merged["enabled"] = enabled
    if api_key is not None:
        merged["api_key"] = api_key
    elif "api_key" not in merged:
        merged["api_key"] = ""
    if base_url is None:
        pass  # Feld nicht im Request → Override unverändert
    elif base_url == "":
        merged.pop("base_url", None)
    else:
        merged["base_url"] = base_url
    return merged


def _pkce_pair() -> tuple[str, str]:
    import base64 as _b64
    import hashlib
    import secrets

    verifier = _b64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = _b64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register_llm_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    load_users,
    audit_log,
    logger: logging.Logger,
) -> None:
    @admin_router.get("/llm/config")
    def get_llm_config():
        config = _load_llm_config()
        providers = config.get("providers", {})
        masked = {}
        for name, cfg in providers.items():
            entry = {
                "enabled": cfg.get("enabled", True),
                "api_key": "***" + cfg.get("api_key", "")[-4:] if cfg.get("api_key") else "",
                "has_key": bool(cfg.get("api_key")),
            }
            # #616: base_url nur ausgeben wenn Provider ihn nutzt (aktuell minimax)
            if cfg.get("base_url"):
                entry["base_url"] = cfg["base_url"]
            masked[name] = entry
        return {"providers": masked}

    @admin_router.get("/llm/config/coach")
    def get_coach_config():
        config = _load_llm_config()
        coach = config.get("coach", {})
        return {"model": coach.get("model", ""), "enabled": coach.get("enabled", False)}

    @admin_router.put("/llm/config/coach")
    def set_coach_config(req: CoachConfigRequest):
        config = _load_llm_config()
        config["coach"] = {
            "model": req.model.strip(),
            "enabled": req.enabled,
        }
        _save_llm_config(config)
        return {"updated": True, **config["coach"]}

    @admin_router.get("/llm/config/system_default")
    def get_system_default():
        config = _load_llm_config()
        return {"model": config.get("system_default", {}).get("model", "")}

    @admin_router.put("/llm/config/system_default")
    async def set_system_default(req: SystemDefaultRequest):
        import yaml as _yaml
        model = req.model.strip()
        config = _load_llm_config()
        if not model:
            config.pop("system_default", None)
            _save_llm_config(config)
            return {"updated": True, "model": "", "agents_updated": []}
        config["system_default"] = {"model": model}
        _save_llm_config(config)
        system_agents = ["hydrahive_support"]
        updated = []
        for agent_id in system_agents:
            yaml_path = Path(f"/agents/{agent_id}/agent.yaml")
            if yaml_path.exists():
                try:
                    data = _yaml.safe_load(yaml_path.read_text())
                    data["llm"]["model"] = model
                    yaml_path.write_text(_yaml.dump(data, default_flow_style=False, allow_unicode=True))
                    updated.append(agent_id)
                except Exception as e:
                    logger.debug("Failed to update LLM model for agent '%s': %s", agent_id, e)
        logger.info("System-Standard-LLM gesetzt: %s (aktualisiert: %s)", model, updated)
        audit_log("llm.system_default_set", details={"model": model, "updated_agents": updated})
        return {"updated": True, "model": model, "agents_updated": updated}

    @admin_router.put("/llm/config/claude_max")
    async def set_claude_oauth_token(req: ClaudeOAuthTokenRequest):
        token = req.api_key.strip()
        if not token:
            raise HTTPException(400, "api_key fehlt")
        if not token.startswith("sk-ant-oat01-"):
            raise HTTPException(400, "Ungültiger Claude OAuth Token — erwartet sk-ant-oat01-...")

        token_file = settings.claude_oauth_token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        token_file.chmod(0o600)
        logger.info("Claude OAuth Token gespeichert")
        audit_log("llm.token_set", details={"provider": "claude_max"})
        return {"updated": True, "provider": "claude_max"}

    @admin_router.put("/llm/config/{provider}")
    def set_llm_provider(provider: str, req: LlmProviderConfig):
        config = _load_llm_config()
        if "providers" not in config:
            config["providers"] = {}
        try:
            api_key = (
                clean_provider_secret(req.api_key, label=f"{provider} api_key")
                if req.api_key is not None
                else None
            )
            # #682: base_url tri-state — None=weglassen, ""=Override löschen,
            # Wert=validieren+setzen.
            if req.base_url is None:
                base_url: str | None = None
            else:
                base_url = clean_provider_base_url(req.base_url, label=f"{provider} base_url")
        except LlmConfigValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        # #616/#682: bestehenden Eintrag mit tri-state Semantik mergen.
        config["providers"][provider] = _merge_provider_config(
            config["providers"].get(provider) or {},
            api_key=api_key,
            enabled=req.enabled,
            base_url=base_url,
        )

        # Env-File nur aktualisieren wenn api_key explizit mitgeschickt wurde.
        # Andernfalls (None) bleibt der existierende Env-Eintrag unverändert.
        if api_key is not None:
            env_key_map = {
                "claude_max": "ANTHROPIC_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
            }
            env_var = env_key_map.get(provider, f"{provider.upper()}_API_KEY")
            env_file = settings.llm_env
            lines = []
            if env_file.exists():
                lines = [line for line in env_file.read_text().splitlines() if not line.startswith(f"{env_var}=")]
            if api_key:
                lines.append(f"{env_var}={api_key}")
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            env_file.chmod(0o600)

        _save_llm_config(config)
        logger.info("LLM-Provider konfiguriert: %s", provider)
        return {"updated": True, "provider": provider}

    @auth_router.get("/llm/available-models")
    async def get_available_models(auth: tuple = Depends(require_auth)):
        import httpx as _httpx

        username, _ = auth
        models: list[dict] = []
        config = _load_llm_config()
        providers = config.get("providers", {})

        anthropic_cfg = providers.get("anthropic", {})
        claude_max_cfg = providers.get("claude_max", {})
        # Check config, env var, AND llm_env file for Anthropic key
        has_anthropic = (
            anthropic_cfg.get("enabled") or anthropic_cfg.get("api_key")
            or claude_max_cfg.get("enabled") or claude_max_cfg.get("api_key")
            or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        )
        if not has_anthropic:
            try:
                _env = settings.llm_env.read_text()
                has_anthropic = "ANTHROPIC_API_KEY" in _env and "=" in _env
            except OSError:
                pass
        if has_anthropic:
            for model in ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"]:
                models.append({"id": model, "label": model, "provider": "anthropic"})

        openai_cfg = providers.get("openai", {})
        if openai_cfg.get("enabled") or openai_cfg.get("api_key"):
            for model in ["gpt-4o-mini", "gpt-4o"]:
                models.append({"id": model, "label": model, "provider": "openai"})

        # #616: MiniMax-M2 — OpenAI-kompatibler Transport, eigener Endpoint + Key
        if _has_minimax_provider_key(providers):
            for model in ["MiniMax-M2.7"]:
                models.append({"id": model, "label": model, "provider": "minimax"})

        # #684: NVIDIA NIM — OpenAI-kompatibler Transport, eigener Endpoint + Key.
        # Statische Phase-1-Liste; dynamische /v1/models-Discovery kommt später.
        if _has_nvidia_provider_key(providers):
            from .orchestrator_llm import NVIDIA_MODELS
            for model in sorted(NVIDIA_MODELS):
                models.append({"id": model, "label": model, "provider": "nvidia"})

        codex_file = settings.openai_codex_token
        if codex_file.exists():
            try:
                import json as _json

                codex_data = _json.loads(codex_file.read_text(encoding="utf-8"))
                if codex_data.get("access_token") and codex_data.get("account_id"):
                    for model in [
                        "gpt-5.2",
                        "gpt-5.1",
                        "gpt-5.1-codex-max",
                        "gpt-5.1-codex-mini",
                        "gpt-5.2-codex",
                        "gpt-5.3-codex",
                        "gpt-5.3-codex-spark",
                        "gpt-5.4",
                    ]:
                        models.append({
                            "id": f"openai-codex/{model}",
                            "label": f"Codex: {model}",
                            "provider": "openai_codex",
                        })
            except Exception as e:
                logger.debug("Failed to load OpenAI Codex models: %s", e)

        ollama_cfg = providers.get("ollama", {})
        ollama_base = ollama_cfg.get("base_url", "http://localhost:11434")
        try:
            async with _httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{ollama_base}/api/tags")
                if resp.status_code == 200:
                    for tag in resp.json().get("models", []):
                        name = tag.get("name", "")
                        if name:
                            models.append({"id": f"ollama/{name}", "label": f"ollama/{name}", "provider": "ollama"})
        except Exception as e:
            logger.debug("Failed to fetch ollama models: %s", e)

        wks = load_users().get(username, {}).get("wks", {})
        if wks.get("ip"):
            wks_url = f"http://{wks['ip']}:{wks.get('ollama_port', 11434)}"
            try:
                async with _httpx.AsyncClient(timeout=2) as client:
                    resp = await client.get(f"{wks_url}/api/tags")
                    if resp.status_code == 200:
                        for tag in resp.json().get("models", []):
                            name = tag.get("name", "")
                            if name:
                                models.append({
                                    "id": f"ollama/{name}",
                                    "label": f"WKS: {name}",
                                    "provider": "wks_ollama",
                                    "wks_base_url": wks_url,
                                })
            except Exception as e:
                logger.debug("Failed to fetch WKS ollama models: %s", e)

        return {"models": models}

    @admin_router.put("/llm/config/openai_codex")
    async def set_openai_codex_token(req: OpenAICodexTokenRequest):
        import json as _json

        data = {
            "access_token": req.access_token.strip(),
            "refresh_token": req.refresh_token,
            "account_id": req.account_id.strip(),
        }
        token_file = settings.openai_codex_token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        token_file.chmod(0o600)
        logger.info("OpenAI Codex OAuth Token gespeichert")
        audit_log("llm.token_set", details={"provider": "openai_codex"})
        return {"updated": True, "provider": "openai_codex"}

    @admin_router.get("/llm/openai_codex_status")
    def get_openai_codex_status():
        import json as _json
        import time as _time

        token_file = settings.openai_codex_token
        if not token_file.exists() or token_file.stat().st_size == 0:
            return {"configured": False, "account_id": None}
        try:
            data = _json.loads(token_file.read_text(encoding="utf-8"))
            if data.get("access_token") and data.get("account_id"):
                result = {
                    "configured": True,
                    "account_id": data["account_id"],
                    "models": [
                        "gpt-5.1",
                        "gpt-5.1-codex-max",
                        "gpt-5.1-codex-mini",
                        "gpt-5.2",
                        "gpt-5.2-codex",
                        "gpt-5.3-codex",
                        "gpt-5.3-codex-spark",
                        "gpt-5.4",
                    ],
                }
                # Rate-Limit-Info aus letztem Codex-Call
                rl_path = settings.etc_dir / "codex_ratelimits.json"
                if rl_path.exists():
                    try:
                        rl = _json.loads(rl_path.read_text(encoding="utf-8"))
                        age_s = _time.time() - rl.get("_updated_at", 0)
                        result["rate_limits"] = {
                            k: v for k, v in rl.items() if not k.startswith("_")
                        }
                        result["rate_limits_age_seconds"] = int(age_s)
                        result["rate_limits_model"] = rl.get("_model", "")
                    except Exception:
                        pass
                return result
        except Exception as e:
            logger.debug("Failed to load OpenAI Codex OAuth config: %s", e)
        return {"configured": False, "account_id": None}

    @admin_router.post("/llm/oauth/anthropic/start")
    async def start_anthropic_oauth():
        import secrets
        import time
        import urllib.parse

        verifier, challenge = _pkce_pair()
        state = verifier
        _oauth_pending[state] = {"verifier": verifier, "provider": "anthropic", "expires": time.time() + 600}
        params = {
            "code": "true",
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
            "response_type": "code",
            "redirect_uri": "https://console.anthropic.com/oauth/code/callback",
            "scope": "org:create_api_key user:profile user:inference",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        auth_url = "https://claude.ai/oauth/authorize?" + urllib.parse.urlencode(params)
        return {"auth_url": auth_url, "state": state}

    @admin_router.post("/llm/oauth/anthropic/exchange")
    async def exchange_anthropic_code(req: OAuthExchangeRequest):
        import time
        import httpx as _httpx

        code_and_state = req.code_and_state.strip()
        if code_and_state and "#" in code_and_state:
            code, state = code_and_state.split("#", 1)
        else:
            code = req.code.strip()
            state = req.state.strip()

        if not code or not state:
            raise HTTPException(400, "code und state erforderlich")

        pending = _oauth_pending.pop(state, None)
        if not pending:
            raise HTTPException(400, "Ungültiger oder abgelaufener State — bitte OAuth neu starten")
        if pending["expires"] < time.time():
            raise HTTPException(400, "OAuth-Session abgelaufen — bitte neu starten")

        async with _httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://console.anthropic.com/v1/oauth/token",
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "authorization_code",
                    "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
                    "code": code,
                    "state": state,
                    "redirect_uri": "https://console.anthropic.com/oauth/code/callback",
                    "code_verifier": pending["verifier"],
                },
            )

        if resp.status_code != 200:
            raise HTTPException(400, f"Token-Exchange fehlgeschlagen: {resp.text[:300]}")

        token_data = resp.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)
        if not access_token or not access_token.startswith("sk-ant-oat01-"):
            raise HTTPException(400, f"Kein gültiger Anthropic Token in Response: {str(token_data)[:200]}")

        import json as _json
        import time as _time
        token_file = settings.claude_oauth_token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(
            _json.dumps({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(_time.time()) + expires_in,
            }, indent=2),
            encoding="utf-8",
        )
        token_file.chmod(0o600)
        logger.info("Claude OAuth Token via PKCE gespeichert (refresh_token: %s, expires_in: %ds)",
                     "ja" if refresh_token else "nein", expires_in)
        audit_log("llm.token_set", details={"provider": "anthropic", "via": "pkce_oauth"})
        return {"updated": True, "provider": "anthropic", "has_refresh": bool(refresh_token)}

    @admin_router.post("/llm/oauth/openai_codex/start")
    async def start_openai_codex_oauth():
        import secrets
        import time
        import urllib.parse

        verifier, challenge = _pkce_pair()
        state = secrets.token_hex(16)
        _oauth_pending[state] = {"verifier": verifier, "provider": "openai_codex", "expires": time.time() + 600}
        params = {
            "response_type": "code",
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "redirect_uri": "http://localhost:1455/auth/callback",
            "scope": "openid profile email offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "pi",
        }
        auth_url = "https://auth.openai.com/oauth/authorize?" + urllib.parse.urlencode(params)
        return {"auth_url": auth_url, "state": state}

    @admin_router.post("/llm/oauth/openai_codex/exchange")
    async def exchange_openai_codex_code(req: OpenAICodexExchangeRequest):
        import base64 as _b64
        import json as _json
        import time
        from urllib.parse import parse_qs, urlparse

        import httpx as _httpx

        redirect_url = req.redirect_url.strip()
        if redirect_url:
            parsed = urlparse(redirect_url)
            qs = parse_qs(parsed.query)
            code = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
        else:
            code = req.code.strip()
            state = req.state.strip()

        if not code or not state:
            raise HTTPException(400, "code und state erforderlich (oder redirect_url mit beiden)")

        pending = _oauth_pending.pop(state, None)
        if not pending:
            raise HTTPException(400, "Ungültiger oder abgelaufener State — bitte OAuth neu starten")
        if pending["expires"] < time.time():
            raise HTTPException(400, "OAuth-Session abgelaufen")

        async with _httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://auth.openai.com/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                    "code": code,
                    "code_verifier": pending["verifier"],
                    "redirect_uri": "http://localhost:1455/auth/callback",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code != 200:
            raise HTTPException(400, f"Token-Exchange fehlgeschlagen: {resp.text[:300]}")

        token_data = resp.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        if not access_token:
            raise HTTPException(400, "Kein access_token in Response")

        account_id = ""
        try:
            payload_b64 = access_token.split(".")[1]
            payload = _json.loads(_b64.urlsafe_b64decode(payload_b64 + "=="))
            account_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
        except Exception as e:
            logger.debug("Failed to extract account_id from OpenAI token: %s", e)
        if not account_id:
            raise HTTPException(400, "account_id konnte nicht aus Token extrahiert werden")

        token_file = settings.openai_codex_token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(
            _json.dumps(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "account_id": account_id,
                    "expires": token_data.get("expires_in", 3600) + int(time.time()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        token_file.chmod(0o600)
        logger.info("OpenAI Codex Token via PKCE gespeichert (account: %s…)", account_id[:12])
        audit_log("llm.token_set", details={"provider": "openai_codex", "via": "pkce_oauth"})
        return {"updated": True, "account_id": account_id}

    @admin_router.get("/llm/claude_token_status")
    def get_claude_token_status():
        import json as _json
        import time as _time

        # Priorität 1: Terminal-Token aus ANTHROPIC_API_KEY (1 Jahr)
        env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_key and env_key.startswith("sk-ant-oat01-"):
            return {
                "configured": True,
                "source": "terminal",
                "token_age_days": None,
                "remaining_days": None,
                "warning": None,
                "ttl_days": 365,
                "has_refresh": False,
            }

        # Priorität 2: Console-OAuth-Token
        token_file = settings.claude_oauth_token
        if not token_file.exists() or token_file.stat().st_size == 0:
            return {"configured": False, "token_age_days": None, "warning": None}

        raw = token_file.read_text(encoding="utf-8").strip()

        # Plain-text Terminal-Token in Datei (1 Jahr gültig)
        if raw.startswith("sk-ant-oat01-") and "\n" not in raw:
            return {
                "configured": True,
                "source": "terminal",
                "token_age_days": None,
                "remaining_days": None,
                "warning": None,
                "ttl_days": 365,
                "has_refresh": False,
            }

        has_refresh = False
        expires_at = 0

        # Neues JSON-Format
        try:
            data = _json.loads(raw)
            has_refresh = bool(data.get("refresh_token"))
            expires_at = data.get("expires_at", 0)
        except (ValueError, _json.JSONDecodeError):
            pass

        if expires_at:
            remaining_seconds = expires_at - _time.time()
            remaining_days = remaining_seconds / 86400
        else:
            # Unbekanntes Format — konservativ schätzen
            mtime = token_file.stat().st_mtime
            age_days = (_time.time() - mtime) / 86400
            token_ttl_days = 30
            remaining_days = token_ttl_days - age_days

        warning = None
        if remaining_days <= 0 and not has_refresh:
            warning = "expired"
        elif remaining_days <= 0 and has_refresh:
            warning = "refresh_pending"
        elif remaining_days <= 3:
            warning = f"expires_soon_{int(remaining_days)}d"
        elif remaining_days <= 7:
            warning = f"expires_in_{int(remaining_days)}d"

        return {
            "configured": True,
            "source": "console_oauth",
            "remaining_days": round(remaining_days, 1),
            "warning": warning,
            "has_refresh": has_refresh,
        }

    @auth_router.get("/llm/ollama/models")
    async def get_ollama_models():
        try:
            import json as _json
            import urllib.request as _ur

            req = _ur.Request("http://127.0.0.1:11434/api/tags")
            with _ur.urlopen(req, timeout=3) as response:
                data = _json.loads(response.read())
            models = [
                {
                    "name": model["name"],
                    "size": model.get("size", 0),
                    "size_gb": round(model.get("size", 0) / 1e9, 1),
                    "modified": model.get("modified_at", ""),
                }
                for model in data.get("models", [])
            ]
            return {"available": True, "models": models, "count": len(models)}
        except Exception as e:
            return {"available": False, "models": [], "error": str(e)}

    @admin_router.post("/llm/ollama/pull")
    async def pull_ollama_model(req: OllamaPullRequest):
        import subprocess as _sub

        model = req.model.strip()
        if not model:
            raise HTTPException(400, "model fehlt")
        try:
            result = _sub.run(["ollama", "pull", model], capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error("ollama pull fehlgeschlagen für %s: %s", model, result.stderr[:200])
                raise HTTPException(500, "ollama pull fehlgeschlagen")
            logger.info("Ollama-Modell geladen: %s", model)
            return {"pulled": True, "model": model}
        except FileNotFoundError:
            raise HTTPException(503, "ollama nicht installiert")
        except _sub.TimeoutExpired:
            raise HTTPException(504, "Timeout beim Laden des Modells")

    @admin_router.get("/llm/embedding/config")
    def get_embedding_config():
        """Gibt aktuelle Embedding-Konfiguration zurück."""
        import os as _os
        config = _load_llm_config()
        model  = config.get("embedding_model", "")

        # Voyage AI Key aus llm_env lesen
        voyage_key_set = False
        llm_env = settings.llm_env
        if llm_env.exists():
            try:
                for line in llm_env.read_text().splitlines():
                    if line.startswith("VOYAGE_API_KEY=") and line.split("=", 1)[1].strip():
                        voyage_key_set = True
            except Exception as e:
                logger.debug("Failed to read llm_env for Voyage key check: %s", e)

        return {
            "model":          model,
            "voyage_key_set": voyage_key_set,
            "ollama_available": _os.path.exists("/usr/local/bin/ollama") or _os.path.exists("/usr/bin/ollama"),
        }

    @admin_router.put("/llm/embedding/config")
    async def set_embedding_config(req: EmbeddingConfigRequest):
        """Speichert Embedding-Modell und optionalen Voyage AI API-Key."""
        model     = req.model.strip()
        voyage_key = req.voyage_api_key.strip()

        if not model:
            raise HTTPException(400, "model fehlt")

        # Embedding-Modell in llm_config.json speichern
        config = _load_llm_config()
        config["embedding_model"] = model
        _save_llm_config(config)

        # Voyage API Key in llm_env speichern/aktualisieren
        if voyage_key:
            llm_env_path = settings.llm_env
            lines: list[str] = []
            if llm_env_path.exists():
                try:
                    lines = llm_env_path.read_text().splitlines()
                except Exception as e:
                    logger.debug("Failed to read existing llm_env: %s", e)
            # Bestehende VOYAGE_API_KEY-Zeile entfernen
            lines = [l for l in lines if not l.startswith("VOYAGE_API_KEY=")]
            lines.append(f"VOYAGE_API_KEY={voyage_key}")
            try:
                llm_env_path.write_text("\n".join(lines) + "\n")
                llm_env_path.chmod(0o600)
            except Exception as e:
                logger.error("llm_env schreiben fehlgeschlagen: %s", e)
                raise HTTPException(500, "Konfiguration konnte nicht gespeichert werden")

        audit_log("llm.embedding_config", target=model)
        logger.info("Embedding-Modell gesetzt: %s", model)
        return {"saved": True, "model": model, "voyage_key_updated": bool(voyage_key)}
