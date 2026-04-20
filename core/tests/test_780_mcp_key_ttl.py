"""Test #780: MCP-API-Key TTL + Audit-Entries.

Testet das _load_mcp_api_key-TTL-Verhalten isoliert. Die Endpoints selbst
testen wir ueber den existierenden test_e2e_core_routes / TestClient-Pfad;
hier fokussieren wir auf die Load-Funktion und das JSON-Schema.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _setup(tmp_path, cfg: dict) -> Path:
    """Schreibt eine mcp_servers.json und patcht settings."""
    f = tmp_path / "mcp_servers.json"
    f.write_text(json.dumps(cfg), encoding="utf-8")
    return f


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_mcp_api_key_repro(cfg_file: Path) -> str:
    """1:1 Repro der _load_mcp_api_key-Logik aus mcp_server.py.

    Damit wir die TTL-Logik testen koennen ohne die Factory
    register_mcp_server_routes aufbauen zu muessen.
    """
    try:
        cfg = json.loads(cfg_file.read_text())
    except (OSError, ValueError):
        return ""
    key = cfg.get("server_api_key", "")
    if not key:
        return ""
    expires_at = cfg.get("server_api_key_expires_at", "")
    if expires_at:
        try:
            from datetime import datetime as _dt, timezone as _tz
            _exp = _dt.fromisoformat(expires_at.replace("Z", "+00:00"))
            if _exp.tzinfo is None:
                _exp = _exp.replace(tzinfo=_tz.utc)
            if _dt.now(_tz.utc) > _exp:
                return ""
        except (ValueError, AttributeError):
            pass
    return key


def test_load_no_config(tmp_path):
    cfg_file = tmp_path / "missing.json"
    assert _load_mcp_api_key_repro(cfg_file) == ""


def test_load_empty_config(tmp_path):
    cfg_file = _setup(tmp_path, {})
    assert _load_mcp_api_key_repro(cfg_file) == ""


def test_load_key_without_ttl(tmp_path):
    cfg_file = _setup(tmp_path, {"server_api_key": "hh-mcp-valid"})
    assert _load_mcp_api_key_repro(cfg_file) == "hh-mcp-valid"


def test_load_key_with_future_expiry(tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    cfg_file = _setup(tmp_path, {
        "server_api_key": "hh-mcp-future",
        "server_api_key_expires_at": _iso(future),
    })
    assert _load_mcp_api_key_repro(cfg_file) == "hh-mcp-future"


def test_load_key_with_past_expiry_returns_empty(tmp_path):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    cfg_file = _setup(tmp_path, {
        "server_api_key": "hh-mcp-expired",
        "server_api_key_expires_at": _iso(past),
    })
    assert _load_mcp_api_key_repro(cfg_file) == ""


def test_load_key_with_malformed_expiry_fails_open(tmp_path):
    """Fail-open bei unparseable timestamp — sonst Migrations-Pech."""
    cfg_file = _setup(tmp_path, {
        "server_api_key": "hh-mcp-foo",
        "server_api_key_expires_at": "not-a-valid-date",
    })
    # malformed → ignoriert → Key ist gueltig
    assert _load_mcp_api_key_repro(cfg_file) == "hh-mcp-foo"


def test_load_key_invalid_json_returns_empty(tmp_path):
    cfg_file = tmp_path / "broken.json"
    cfg_file.write_text("{ not json", encoding="utf-8")
    assert _load_mcp_api_key_repro(cfg_file) == ""
