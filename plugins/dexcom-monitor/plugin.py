"""
Dexcom Glukose-Monitor Plugin für HydraHive

Überwacht Dexcom G7/G6 CGM Werte über die Dexcom Share API
und sendet WhatsApp-Alerts bei kritischen Glukosewerten.

Dexcom Share API (inoffiziell aber stabil):
- Login → Session-ID
- Glucose Readings → aktuelle Werte mit Trend
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("dexcom-monitor")

# Dexcom Share API Endpoints
DEXCOM_BASE = {
    "us": "https://share2.dexcom.com/ShareWebServices/Services",
    "eu": "https://shareous1.dexcom.com/ShareWebServices/Services",
}

DEXCOM_APP_ID = "d89443d2-327c-4a6f-89e5-496bbb0317db"

# Trend-Pfeile
TREND_ARROWS = {
    0: "?",       # None
    1: "↑↑",      # DoubleUp
    2: "↑",       # SingleUp
    3: "↗",       # FortyFiveUp
    4: "→",       # Flat
    5: "↘",       # FortyFiveDown
    6: "↓",       # SingleDown
    7: "↓↓",      # DoubleDown
    8: "?",       # NotComputable
    9: "?",       # RateOutOfRange
}

# Cooldown-Tracking (in-memory)
_last_alert: dict[str, float] = {}


def _load_user_config(username: str) -> dict:
    """Lädt die User-spezifische Plugin-Config."""
    path = Path(f"/etc/hydrahive/user_app_config/{username}/dexcom-monitor.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


async def _dexcom_login(username: str, password: str, region: str = "eu") -> str | None:
    """Login bei Dexcom Share → gibt Session-ID zurück."""
    import httpx
    base = DEXCOM_BASE.get(region, DEXCOM_BASE["eu"])
    url = f"{base}/General/LoginPublisherAccountByName"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "accountName": username,
                "password": password,
                "applicationId": DEXCOM_APP_ID,
            })
            if r.status_code == 200:
                return r.text.strip('"')
            logger.warning("Dexcom Login fehlgeschlagen: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.error("Dexcom Login Fehler: %s", e)
    return None


async def _dexcom_readings(session_id: str, region: str = "eu", minutes: int = 30, count: int = 6) -> list[dict]:
    """Holt Glukose-Werte der letzten X Minuten."""
    import httpx
    base = DEXCOM_BASE.get(region, DEXCOM_BASE["eu"])
    url = f"{base}/Publisher/ReadPublisherLatestGlucoseValues"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, params={
                "sessionId": session_id,
                "minutes": minutes,
                "maxCount": count,
            })
            if r.status_code == 200:
                readings = r.json()
                result = []
                for reading in readings:
                    # Dexcom gibt Timestamps als "/Date(1234567890000)/" Format
                    ts_str = reading.get("WT", "")
                    ts_ms = int(ts_str.replace("/Date(", "").replace(")/", "")) if "Date" in ts_str else 0
                    result.append({
                        "value": reading.get("Value", 0),
                        "trend": reading.get("Trend", 0),
                        "trend_arrow": TREND_ARROWS.get(reading.get("Trend", 0), "?"),
                        "timestamp": ts_ms // 1000 if ts_ms else 0,
                    })
                return result
            logger.warning("Dexcom Readings fehlgeschlagen: %s", r.status_code)
    except Exception as e:
        logger.error("Dexcom Readings Fehler: %s", e)
    return []


def register(api):
    """Registriert die Dexcom-Tools."""

    @api.tool(
        tool_id="dexcom_read_glucose",
        description=(
            "Liest aktuelle Glukosewerte vom Dexcom CGM. "
            "Gibt den aktuellen Wert, Trend-Pfeil und die letzten Messungen zurück."
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "Zeitraum in Minuten (Standard: 30)",
                },
                "count": {
                    "type": "integer",
                    "description": "Max. Anzahl Werte (Standard: 6)",
                },
            },
            "required": [],
        },
    )
    async def dexcom_read_glucose(minutes: int = 30, count: int = 6, **ctx) -> str:
        username = ctx.get("_username", "admin")
        cfg = _load_user_config(username)
        if not cfg.get("dexcom_username") or not cfg.get("dexcom_password"):
            return json.dumps({"error": "Dexcom nicht konfiguriert. Bitte unter Mein Agent → Glukose Tab einrichten."})

        session_id = await _dexcom_login(cfg["dexcom_username"], cfg["dexcom_password"], cfg.get("dexcom_region", "eu"))
        if not session_id:
            return json.dumps({"error": "Dexcom Login fehlgeschlagen. Benutzername/Passwort prüfen."})

        readings = await _dexcom_readings(session_id, cfg.get("dexcom_region", "eu"), minutes, count)
        if not readings:
            return json.dumps({"error": "Keine Glukosewerte verfügbar."})

        current = readings[0]
        alert_low = cfg.get("alert_low", 70)
        alert_high = cfg.get("alert_high", 250)
        status = "normal"
        if current["value"] < alert_low:
            status = "NIEDRIG ⚠️"
        elif current["value"] > alert_high:
            status = "HOCH ⚠️"

        return json.dumps({
            "current": {
                "value": current["value"],
                "unit": "mg/dL",
                "trend": current["trend_arrow"],
                "status": status,
            },
            "readings": readings,
            "alert_thresholds": {"low": alert_low, "high": alert_high},
        }, ensure_ascii=False)

    @api.tool(
        tool_id="dexcom_check_alerts",
        description=(
            "Prüft Glukosewerte und sendet WhatsApp-Alert bei kritischen Werten. "
            "Wird typischerweise vom Heartbeat alle 5 Minuten aufgerufen."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def dexcom_check_alerts(**ctx) -> str:
        username = ctx.get("_username", "admin")
        cfg = _load_user_config(username)
        if not cfg.get("enabled"):
            return json.dumps({"skipped": True, "reason": "Monitoring deaktiviert"})
        if not cfg.get("dexcom_username") or not cfg.get("dexcom_password"):
            return json.dumps({"skipped": True, "reason": "Dexcom nicht konfiguriert"})

        session_id = await _dexcom_login(cfg["dexcom_username"], cfg["dexcom_password"], cfg.get("dexcom_region", "eu"))
        if not session_id:
            return json.dumps({"error": "Dexcom Login fehlgeschlagen"})

        readings = await _dexcom_readings(session_id, cfg.get("dexcom_region", "eu"), minutes=10, count=2)
        if not readings:
            return json.dumps({"error": "Keine Werte verfügbar"})

        current = readings[0]
        value = current["value"]
        trend = current["trend_arrow"]
        alert_low = cfg.get("alert_low", 70)
        alert_high = cfg.get("alert_high", 250)
        cooldown_min = cfg.get("cooldown_min", 15)
        notify_numbers = [n.strip() for n in str(cfg.get("notify_numbers", "")).split(",") if n.strip()]

        # Alarm-Check
        alert_type = None
        if value < alert_low:
            alert_type = "NIEDRIG"
        elif value > alert_high:
            alert_type = "HOCH"

        if not alert_type:
            return json.dumps({"ok": True, "value": value, "trend": trend, "status": "normal"})

        # Cooldown prüfen
        cooldown_key = f"{username}:{alert_type}"
        now = time.time()
        last = _last_alert.get(cooldown_key, 0)
        if now - last < cooldown_min * 60:
            return json.dumps({
                "alert": alert_type,
                "value": value,
                "trend": trend,
                "suppressed": True,
                "reason": f"Cooldown ({cooldown_min} Min)",
                "next_alert_in": int(cooldown_min * 60 - (now - last)),
            })

        _last_alert[cooldown_key] = now

        # WhatsApp-Alert senden
        alerts_sent = []
        if notify_numbers:
            try:
                from hydrahive_core.whatsapp_agent import bridge_send
                emoji = "🔴" if alert_type == "NIEDRIG" else "🟡"
                msg = f"{emoji} Glukose-Alarm: {value} mg/dL ({alert_type}) {trend}\n\nBitte prüfen!"
                for number in notify_numbers:
                    jid = f"{number}@c.us" if "@" not in number else number
                    await bridge_send("personal_admin", jid, msg)
                    alerts_sent.append(number)
            except Exception as e:
                logger.error("WhatsApp Alert fehlgeschlagen: %s", e)

        return json.dumps({
            "alert": alert_type,
            "value": value,
            "trend": trend,
            "whatsapp_sent": alerts_sent,
            "cooldown_min": cooldown_min,
        }, ensure_ascii=False)
