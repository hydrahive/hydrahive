from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

LLM_CONFIG_FILE = "/etc/hydrahive/llm_config.json"


class LlmProviderConfig(BaseModel):
    provider: str
    api_key: str = ""
    enabled: bool = True


_oauth_pending: dict[str, dict] = {}


def _load_llm_config() -> dict:
    import json as _json

    try:
        return _json.loads(Path(LLM_CONFIG_FILE).read_text())
    except (OSError, ValueError):
        return {"providers": {}}


def _save_llm_config(config: dict) -> None:
    import json as _json

    p = Path(LLM_CONFIG_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(config, indent=2), encoding="utf-8")
    p.chmod(0o600)


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
            masked[name] = {
                "enabled": cfg.get("enabled", True),
                "api_key": "***" + cfg.get("api_key", "")[-4:] if cfg.get("api_key") else "",
                "has_key": bool(cfg.get("api_key")),
            }
        return {"providers": masked}

    @admin_router.get("/llm/config/coach")
    def get_coach_config():
        config = _load_llm_config()
        coach = config.get("coach", {})
        return {"model": coach.get("model", ""), "enabled": coach.get("enabled", False)}

    @admin_router.put("/llm/config/coach")
    def set_coach_config(body: dict):
        config = _load_llm_config()
        config["coach"] = {
            "model": body.get("model", "").strip(),
            "enabled": bool(body.get("enabled", False)),
        }
        _save_llm_config(config)
        return {"updated": True, **config["coach"]}

    @admin_router.get("/llm/config/system_default")
    def get_system_default():
        config = _load_llm_config()
        return {"model": config.get("system_default", {}).get("model", "")}

    @admin_router.put("/llm/config/system_default")
    async def set_system_default(body: dict):
        import yaml as _yaml
        model = body.get("model", "").strip()
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
    async def set_claude_oauth_token(body: dict):
        token = body.get("api_key", "").strip()
        if not token:
            raise HTTPException(400, "api_key fehlt")
        if not token.startswith("sk-ant-oat01-"):
            raise HTTPException(400, "Ungültiger Claude OAuth Token — erwartet sk-ant-oat01-...")

        token_file = Path("/etc/hydrahive/claude_oauth_token")
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
        config["providers"][provider] = {
            "enabled": req.enabled,
            "api_key": req.api_key,
        }
        env_key_map = {
            "claude_max": "ANTHROPIC_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        env_var = env_key_map.get(provider, f"{provider.upper()}_API_KEY")
        env_file = Path("/etc/hydrahive/llm_env")
        lines = []
        if env_file.exists():
            lines = [line for line in env_file.read_text().splitlines() if not line.startswith(f"{env_var}=")]
        if req.api_key:
            lines.append(f"{env_var}={req.api_key}")
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
                _env = Path("/etc/hydrahive/llm_env").read_text()
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

        codex_file = Path("/etc/hydrahive/openai_codex_token.json")
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
    async def set_openai_codex_token(body: dict):
        import json as _json

        access_token = body.get("access_token", "").strip()
        account_id = body.get("account_id", "").strip()
        if not access_token:
            raise HTTPException(400, "access_token fehlt")
        if not account_id:
            raise HTTPException(400, "account_id fehlt")

        data = {
            "access_token": access_token,
            "refresh_token": body.get("refresh_token", ""),
            "account_id": account_id,
        }
        token_file = Path("/etc/hydrahive/openai_codex_token.json")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        token_file.chmod(0o600)
        logger.info("OpenAI Codex OAuth Token gespeichert")
        audit_log("llm.token_set", details={"provider": "openai_codex"})
        return {"updated": True, "provider": "openai_codex"}

    @admin_router.get("/llm/openai_codex_status")
    def get_openai_codex_status():
        import json as _json

        token_file = Path("/etc/hydrahive/openai_codex_token.json")
        if not token_file.exists() or token_file.stat().st_size == 0:
            return {"configured": False, "account_id": None}
        try:
            data = _json.loads(token_file.read_text(encoding="utf-8"))
            if data.get("access_token") and data.get("account_id"):
                return {
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
    async def exchange_anthropic_code(body: dict):
        import time
        import httpx as _httpx

        code_and_state = body.get("code_and_state", "").strip()
        if code_and_state and "#" in code_and_state:
            code, state = code_and_state.split("#", 1)
        else:
            code = body.get("code", "").strip()
            state = body.get("state", "").strip()

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

        import time as _time
        token_file = Path("/etc/hydrahive/claude_oauth_token")
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
    async def exchange_openai_codex_code(body: dict):
        import base64 as _b64
        import json as _json
        import time
        from urllib.parse import parse_qs, urlparse

        import httpx as _httpx

        redirect_url = body.get("redirect_url", "").strip()
        if redirect_url:
            parsed = urlparse(redirect_url)
            qs = parse_qs(parsed.query)
            code = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
        else:
            code = body.get("code", "").strip()
            state = body.get("state", "").strip()

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

        token_file = Path("/etc/hydrahive/openai_codex_token.json")
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
        token_file = Path("/etc/hydrahive/claude_oauth_token")
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
    async def pull_ollama_model(body: dict):
        import subprocess as _sub

        model = body.get("model", "").strip()
        if not model:
            raise HTTPException(400, "model fehlt")
        try:
            result = _sub.run(["ollama", "pull", model], capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise HTTPException(500, f"ollama pull fehlgeschlagen: {result.stderr[:200]}")
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
        llm_env = Path("/etc/hydrahive/llm_env")
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
    async def set_embedding_config(body: dict):
        """Speichert Embedding-Modell und optionalen Voyage AI API-Key."""
        model     = body.get("model", "").strip()
        voyage_key = body.get("voyage_api_key", "").strip()

        if not model:
            raise HTTPException(400, "model fehlt")

        # Embedding-Modell in llm_config.json speichern
        config = _load_llm_config()
        config["embedding_model"] = model
        _save_llm_config(config)

        # Voyage API Key in llm_env speichern/aktualisieren
        if voyage_key:
            llm_env_path = Path("/etc/hydrahive/llm_env")
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
                raise HTTPException(500, f"llm_env schreiben fehlgeschlagen: {e}")

        audit_log("llm.embedding_config", target=model)
        logger.info("Embedding-Modell gesetzt: %s", model)
        return {"saved": True, "model": model, "voyage_key_updated": bool(voyage_key)}
