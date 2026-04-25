"""
router_libre.py — FreeStyle Libre 3 Integration via LibreLinkUp API (#912)

Liest Glukosewerte aus der LibreView-Cloud (LibreLinkUp-API).
Config: /etc/hydrahive/freestyle_libre.json
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TREND_ARROWS = {1: "↑↑", 2: "↑", 3: "↗", 4: "→", 5: "↘", 6: "↓", 7: "↓↓"}
_REGION_HOSTS = {
    "EU": "api-eu.libreview.io",
    "US": "api-us.libreview.io",
    "DE": "api-eu.libreview.io",
    "AP": "api-ap.libreview.io",
    "AU": "api-au.libreview.io",
    "JP": "api-jp.libreview.io",
}
_DEFAULT_HOST = "api-eu.libreview.io"
_LLU_HEADERS = {
    "Content-Type": "application/json",
    "product": "llu.ios",
    "version": "4.12.0",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "cache-control": "no-cache",
}

# In-memory cache
_auth_cache: dict[str, Any] = {}   # token, expires_at, patient_id, host
_glucose_cache: dict[str, Any] = {}  # value, trend, timestamp, fetched_at


def _config_path() -> Path:
    return Path("/etc/hydrahive/freestyle_libre.json")


def _load_config() -> dict | None:
    p = _config_path()
    if not p.exists():
        return None
    try:
        cfg = json.loads(p.read_text())
        if not cfg.get("email") or not cfg.get("password"):
            return None
        return cfg
    except Exception:
        return None


def _http(method: str, url: str, body: dict | None = None,
          token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    headers = dict(_LLU_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        # gzip handled automatically by urllib
        return json.loads(raw)


def _get_token(cfg: dict) -> tuple[str, str, str]:
    """Returns (token, patient_id, host). Uses cache if fresh."""
    now = time.time()
    if (_auth_cache.get("token") and
            _auth_cache.get("expires_at", 0) > now + 300 and
            _auth_cache.get("email") == cfg["email"]):
        return _auth_cache["token"], _auth_cache["patient_id"], _auth_cache["host"]

    region = cfg.get("region", "EU").upper()
    host = _REGION_HOSTS.get(region, _DEFAULT_HOST)

    resp = _http("POST", f"https://{host}/llu/auth/login",
                 body={"email": cfg["email"], "password": cfg["password"]})

    # LibreLinkUp sometimes redirects to a different regional endpoint
    redirect = (resp.get("data", {}) or {}).get("redirect")
    if redirect:
        redirect_region = str(redirect).upper()
        host = _REGION_HOSTS.get(redirect_region, _DEFAULT_HOST)
        resp = _http("POST", f"https://{host}/llu/auth/login",
                     body={"email": cfg["email"], "password": cfg["password"]})

    ticket = resp.get("data", {}).get("authTicket", {})
    token = ticket.get("token", "")
    if not token:
        raise RuntimeError("Login fehlgeschlagen — Token leer")

    # Fetch connections to get patient ID
    conn_resp = _http("GET", f"https://{host}/llu/connections", token=token)
    connections = conn_resp.get("data") or []
    patient_id = connections[0].get("patientId", "") if connections else ""

    _auth_cache.update({
        "token": token,
        "patient_id": patient_id,
        "host": host,
        "email": cfg["email"],
        "expires_at": now + 82800,  # 23h — token lasts ~2 weeks, refresh daily
    })
    return token, patient_id, host


def _mgdl_to_mmol(mgdl: float) -> float:
    return round(mgdl / 18.018, 1)


def _mmol_to_mgdl(mmol: float) -> int:
    return round(mmol * 18.018)


def _color(value_mmol: float, cfg: dict) -> str:
    low = float(cfg.get("low", 3.9))
    high = float(cfg.get("high", 10.0))
    if value_mmol < low - 0.5 or value_mmol > high + 3.9:
        return "red"
    if value_mmol < low or value_mmol > high:
        return "yellow"
    return "green"


def _format_value(raw_mgdl: float, unit: str, cfg: dict) -> dict:
    if unit == "mmol":
        value = _mgdl_to_mmol(raw_mgdl)
        unit_str = "mmol/L"
    else:
        value = round(raw_mgdl)
        unit_str = "mg/dL"
    return {
        "value": value,
        "unit": unit_str,
        "color": _color(_mgdl_to_mmol(raw_mgdl), cfg),
    }


def fetch_current(cfg: dict) -> dict:
    """Fetch current glucose from LibreLinkUp. Caches 4.5 min."""
    now = time.time()
    cache_ttl = float(cfg.get("cache_seconds", 270))
    if _glucose_cache.get("fetched_at", 0) + cache_ttl > now:
        return _glucose_cache["data"]

    token, patient_id, host = _get_token(cfg)
    conn_resp = _http("GET", f"https://{host}/llu/connections", token=token)
    connections = conn_resp.get("data") or []
    if not connections:
        raise RuntimeError("Keine LibreLinkUp-Verbindungen gefunden")

    conn = connections[0]
    meas = conn.get("glucoseMeasurement") or {}
    raw_mgdl = float(meas.get("ValueInMgPerDl", meas.get("Value", 0)))
    trend_num = int(meas.get("TrendArrow", 4))
    ts = meas.get("FactoryTimestamp") or meas.get("Timestamp") or ""

    unit = cfg.get("unit", "mmol")
    result = {
        **_format_value(raw_mgdl, unit, cfg),
        "trend": _TREND_ARROWS.get(trend_num, "→"),
        "trend_num": trend_num,
        "timestamp": ts,
        "patient": conn.get("firstName", "") + " " + conn.get("lastName", ""),
        "sensor_serial": (conn.get("sensor") or {}).get("sn", ""),
    }
    _glucose_cache.update({"fetched_at": now, "data": result})
    return result


def fetch_history(cfg: dict, hours: int = 24) -> list[dict]:
    """Fetch glucose history graph from LibreLinkUp."""
    token, patient_id, host = _get_token(cfg)
    if not patient_id:
        return []

    graph_resp = _http("GET", f"https://{host}/llu/connections/{patient_id}/graph",
                       token=token)
    entries = (graph_resp.get("data") or {}).get("graphData") or []

    unit = cfg.get("unit", "mmol")
    cutoff = time.time() - hours * 3600
    result = []
    for e in entries:
        ts = e.get("FactoryTimestamp") or e.get("Timestamp") or ""
        raw_mgdl = float(e.get("ValueInMgPerDl", e.get("Value", 0)))
        trend_num = int(e.get("TrendArrow", 4))
        fmt = _format_value(raw_mgdl, unit, cfg)
        result.append({
            **fmt,
            "trend": _TREND_ARROWS.get(trend_num, "→"),
            "timestamp": ts,
        })
    return result[-min(len(result), hours * 12):]  # ~1 reading per 5 min


# ─── FastAPI routes ────────────────────────────────────────────────────────────

def register_libre_routes(router, *, require_auth) -> None:
    from fastapi import Depends, HTTPException

    @router.get("/libre/status")
    def libre_status(_auth=Depends(require_auth)):
        cfg = _load_config()
        return {"configured": cfg is not None}

    @router.get("/libre/current")
    def libre_current(_auth=Depends(require_auth)):
        cfg = _load_config()
        if not cfg:
            raise HTTPException(404, "Freestyle Libre nicht konfiguriert")
        try:
            return fetch_current(cfg)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _auth_cache.clear()
                raise HTTPException(502, "LibreLinkUp: Authentifizierung fehlgeschlagen")
            raise HTTPException(502, f"LibreLinkUp HTTP {e.code}")
        except Exception as e:
            logger.warning("libre_current error: %s", e)
            raise HTTPException(502, str(e)[:200])

    @router.get("/libre/history")
    def libre_history(hours: int = 24, _auth=Depends(require_auth)):
        cfg = _load_config()
        if not cfg:
            raise HTTPException(404, "Freestyle Libre nicht konfiguriert")
        try:
            return {"readings": fetch_history(cfg, min(hours, 72))}
        except Exception as e:
            logger.warning("libre_history error: %s", e)
            raise HTTPException(502, str(e)[:200])
