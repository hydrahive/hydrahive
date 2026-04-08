"""
alert_service.py — Smart Alert System (#374)

Prüft periodisch System-Metriken und sendet Notifications bei Problemen:
- Disk Space niedrig
- Agent Heartbeat fehlt
- OAuth-Token läuft ab
- Hohe Error-Rate im Journal

Konfiguration: /etc/hydrahive/alerts.json
Pattern: analog zu cleanup_service.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from .settings import settings

logger = logging.getLogger(__name__)

_CONFIG_FILE = settings.alerts_config
_COOLDOWN_FILE = settings.etc_dir / "alert_cooldowns.json"

_DEFAULT_CFG: dict[str, Any] = {
    "enabled": True,
    "check_interval_seconds": 120,
    "disk_warn_pct": 80,
    "disk_crit_pct": 92,
    "heartbeat_max_age_seconds": 300,
    "journal_error_threshold": 10,
    "journal_window_lines": 200,
    "oauth_warn_days": 3,
    "notify_users": ["admin"],
    "cooldown_minutes": 30,
}


def _load_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        try:
            return {**_DEFAULT_CFG, **json.loads(_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULT_CFG)


def save_config(cfg: dict[str, Any]) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _load_cooldowns() -> dict[str, float]:
    try:
        if _COOLDOWN_FILE.exists():
            return json.loads(_COOLDOWN_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cooldowns(cd: dict[str, float]) -> None:
    try:
        _COOLDOWN_FILE.write_text(json.dumps(cd))
    except Exception:
        pass


# ── Checks ───────────────────────────────────────────────────────────────────

def check_disk(cfg: dict) -> list[dict]:
    """Prüft Disk-Usage."""
    alerts = []
    try:
        usage = shutil.disk_usage("/")
        pct = round(usage.used / usage.total * 100, 1)
        free_gb = round(usage.free / 1_073_741_824, 1)
        if pct >= cfg.get("disk_crit_pct", 92):
            alerts.append({
                "key": "disk_critical",
                "level": "critical",
                "title": f"Disk KRITISCH: {pct}% belegt",
                "body": f"Nur noch {free_gb} GB frei. Sofortige Bereinigung empfohlen.",
                "link": "/system",
            })
        elif pct >= cfg.get("disk_warn_pct", 80):
            alerts.append({
                "key": "disk_warning",
                "level": "warning",
                "title": f"Disk Warnung: {pct}% belegt",
                "body": f"Noch {free_gb} GB frei. Bereinigung empfohlen.",
                "link": "/system",
            })
    except Exception as e:
        logger.debug("Alert check_disk Fehler: %s", e)
    return alerts


def check_heartbeats(cfg: dict, discovery: Any) -> list[dict]:
    """Prüft ob Agenten-Heartbeats aktuell sind."""
    alerts = []
    max_age = cfg.get("heartbeat_max_age_seconds", 300)
    try:
        for agent_id, agent_cfg in discovery.agents.items():
            rt = getattr(agent_cfg, "_runtime", None)
            if rt is None:
                continue
            hb_age = getattr(rt, "last_heartbeat_age", None)
            if hb_age is not None and hb_age > max_age:
                alerts.append({
                    "key": f"heartbeat_{agent_id}",
                    "level": "warning",
                    "title": f"Agent '{agent_id}' — kein Heartbeat seit {int(hb_age)}s",
                    "body": f"Letzter Heartbeat vor {int(hb_age)} Sekunden (Limit: {max_age}s). Agent möglicherweise hängen geblieben.",
                    "link": f"/agents/{agent_id}/chat",
                })
    except Exception as e:
        logger.debug("Alert check_heartbeats Fehler: %s", e)
    return alerts


def check_journal_errors(cfg: dict) -> list[dict]:
    """Prüft Error-Rate im Core-Journal."""
    alerts = []
    threshold = cfg.get("journal_error_threshold", 10)
    lines = cfg.get("journal_window_lines", 200)
    try:
        result = subprocess.run(
            ["journalctl", "-u", "hydrahive-core", "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
        error_count = sum(
            1 for line in result.stdout.splitlines()
            if " ERROR " in line or " error " in line or "Traceback" in line
        )
        if error_count >= threshold:
            alerts.append({
                "key": "journal_errors",
                "level": "warning",
                "title": f"{error_count} Fehler in den letzten {lines} Log-Zeilen",
                "body": "Ungewöhnlich viele Fehler im Core-Journal. Logs prüfen.",
                "link": "/system",
            })
    except Exception as e:
        logger.debug("Alert check_journal Fehler: %s", e)
    return alerts


def check_oauth_token(cfg: dict) -> list[dict]:
    """Prüft ob OAuth-Token bald abläuft."""
    alerts = []
    warn_days = cfg.get("oauth_warn_days", 3)
    token_file = settings.claude_oauth_token
    if not token_file.exists():
        return alerts
    try:
        raw = token_file.read_text().strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            expires_at = data.get("expires_at", 0)
            if expires_at:
                remaining = (expires_at - time.time()) / 86400
                has_refresh = bool(data.get("refresh_token"))
                if remaining <= 0 and not has_refresh:
                    alerts.append({
                        "key": "oauth_expired",
                        "level": "critical",
                        "title": "Claude OAuth-Token abgelaufen!",
                        "body": "Token ist abgelaufen und kein Refresh-Token vorhanden. Bitte erneut authentifizieren.",
                        "link": "/settings",
                    })
                elif remaining <= warn_days:
                    alerts.append({
                        "key": "oauth_expiring",
                        "level": "warning",
                        "title": f"Claude OAuth-Token läuft in {remaining:.0f} Tagen ab",
                        "body": f"Token läuft in {remaining:.1f} Tagen ab. {'Refresh-Token vorhanden.' if has_refresh else 'Kein Refresh-Token — manuell erneuern!'}",
                        "link": "/settings",
                    })
    except Exception as e:
        logger.debug("Alert check_oauth Fehler: %s", e)
    return alerts


# ── Service ──────────────────────────────────────────────────────────────────

class AlertService:
    """Periodischer Alert-Check als Background-Task."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._discovery: Any = None
        self._notify_fn: Callable[..., Coroutine] | None = None
        self._load_users_fn: Callable[[], dict] | None = None

    def start(
        self,
        *,
        discovery: Any,
        notify_fn: Callable[..., Coroutine],
        load_users_fn: Callable[[], dict],
    ) -> None:
        self._discovery = discovery
        self._notify_fn = notify_fn
        self._load_users_fn = load_users_fn
        self._task = asyncio.create_task(self._loop(), name="alert-service")
        logger.info("AlertService gestartet")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def run_now(self) -> dict:
        """Alle Checks sofort ausführen (für API-Trigger)."""
        return await asyncio.get_event_loop().run_in_executor(None, self._run_checks_sync)

    def _run_checks_sync(self) -> dict:
        cfg = _load_config()
        if not cfg.get("enabled", True):
            return {"skipped": True, "reason": "disabled"}

        all_alerts: list[dict] = []
        all_alerts.extend(check_disk(cfg))
        all_alerts.extend(check_heartbeats(cfg, self._discovery))
        all_alerts.extend(check_journal_errors(cfg))
        all_alerts.extend(check_oauth_token(cfg))

        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "alerts": all_alerts,
            "total": len(all_alerts),
        }

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # 30s nach Start warten
        while True:
            try:
                cfg = _load_config()
                if not cfg.get("enabled", True):
                    await asyncio.sleep(300)
                    continue

                interval = cfg.get("check_interval_seconds", 120)
                result = await self.run_now()
                alerts = result.get("alerts", [])

                if alerts:
                    await self._send_alerts(alerts, cfg)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("AlertService Fehler: %s", e)
                await asyncio.sleep(60)

    async def _send_alerts(self, alerts: list[dict], cfg: dict) -> None:
        """Sendet Alerts als Notifications (mit Cooldown)."""
        if not self._notify_fn:
            return

        cooldowns = _load_cooldowns()
        cooldown_minutes = cfg.get("cooldown_minutes", 30)
        now = time.time()
        notify_users = cfg.get("notify_users", ["admin"])
        sent = 0

        for alert in alerts:
            key = alert["key"]
            last_sent = cooldowns.get(key, 0)
            if now - last_sent < cooldown_minutes * 60:
                continue

            for user in notify_users:
                try:
                    await self._notify_fn(
                        user=user,
                        type=f"alert_{alert['level']}",
                        title=alert["title"],
                        body=alert["body"],
                        link=alert.get("link"),
                    )
                    sent += 1
                except Exception as e:
                    logger.warning("Alert-Notification an %s fehlgeschlagen: %s", user, e)

            cooldowns[key] = now

        if sent:
            _save_cooldowns(cooldowns)
            logger.info("AlertService: %d Notifications gesendet (%d Alerts)", sent, len(alerts))


alert_service = AlertService()
