"""#705: Stale-Normalisierung für /admin/update/status.

Testet _update_services_active (fail-safe) und _apply_stale_normalization
(Matrix: service_active × age × malformed_started_at × non-running).
Helper sind auf Modul-Ebene in router_system.py, deshalb direkt testbar
ohne FastAPI-Setup.
"""
from __future__ import annotations

import subprocess as _sp
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import router_system
from hydrahive_core.router_system import (
    _apply_stale_normalization,
    _update_services_active,
)


# ────────────────────────────────────────────── _update_services_active


class TestUpdateServicesActive:
    def test_both_inactive(self, monkeypatch):
        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=3)   # is-active returnt 3 bei inactive
        monkeypatch.setattr(_sp, "run", fake_run)
        assert _update_services_active() is False

    def test_selfupdate_active(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd[-1])
            # erster Aufruf: selfupdate → aktiv (returncode 0)
            return SimpleNamespace(returncode=0 if "selfupdate" in cmd[-1] else 3)

        monkeypatch.setattr(_sp, "run", fake_run)
        assert _update_services_active() is True
        # autoupdate wird nicht mehr gefragt — short-circuit.
        assert calls == ["hydrahive-selfupdate.service"]

    def test_autoupdate_active(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=0 if "autoupdate" in cmd[-1] else 3)
        monkeypatch.setattr(_sp, "run", fake_run)
        assert _update_services_active() is True

    def test_timeout_assumes_active(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise _sp.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1.5))
        monkeypatch.setattr(_sp, "run", fake_run)
        # fail-safe: True
        assert _update_services_active() is True

    def test_filenotfound_assumes_active(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("systemctl not found")
        monkeypatch.setattr(_sp, "run", fake_run)
        assert _update_services_active() is True

    def test_arbitrary_exception_assumes_active(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise RuntimeError("unexpected")
        monkeypatch.setattr(_sp, "run", fake_run)
        assert _update_services_active() is True


# ────────────────────────────────────────────── _apply_stale_normalization


def _now_minus(minutes: float) -> str:
    """ISO-Timestamp mit Offset, x Minuten in der Vergangenheit."""
    t = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    return t.isoformat()


class TestApplyStaleNormalization:
    @pytest.fixture(autouse=True)
    def _patch_services(self, monkeypatch):
        """Default: Services aktiv. Tests können das überschreiben."""
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: True,
        )

    def test_non_running_unchanged(self):
        status = {"status": "ok", "commit": "abc"}
        _apply_stale_normalization(status)
        assert status == {"status": "ok", "commit": "abc"}

    def test_error_status_unchanged(self):
        status = {"status": "error", "error": "boom"}
        _apply_stale_normalization(status)
        assert status == {"status": "error", "error": "boom"}

    def test_running_fresh_service_active_stays_running(self, monkeypatch):
        status = {"status": "running", "started_at": _now_minus(1)}
        _apply_stale_normalization(status)
        assert status["status"] == "running"
        assert "stale" not in status
        assert "stale_reason" not in status

    def test_running_service_inactive_becomes_stale_immediately(self, monkeypatch):
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: False,
        )
        status = {"status": "running", "started_at": _now_minus(1)}
        _apply_stale_normalization(status)
        assert status["status"] == "ok"
        assert status["stale"] is True
        assert status["stale_reason"] == "service_inactive"

    def test_running_old_service_active_still_stale(self, monkeypatch):
        """Age-Regel trotz aktivem Service (z.B. Service hängt endlos)."""
        status = {"status": "running", "started_at": _now_minus(15)}
        _apply_stale_normalization(status)
        assert status["status"] == "ok"
        assert status["stale"] is True
        assert status["stale_reason"] == "age_exceeded"

    def test_running_old_and_inactive_reports_service_inactive(self, monkeypatch):
        """Beide Bedingungen → service_inactive gewinnt (diagnostisch wertvoller)."""
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: False,
        )
        status = {"status": "running", "started_at": _now_minus(30)}
        _apply_stale_normalization(status)
        assert status["stale_reason"] == "service_inactive"

    def test_running_9_minutes_service_active_stays_running(self, monkeypatch):
        status = {"status": "running", "started_at": _now_minus(9)}
        _apply_stale_normalization(status)
        assert status["status"] == "running"

    def test_running_11_minutes_service_active_becomes_stale(self, monkeypatch):
        status = {"status": "running", "started_at": _now_minus(11)}
        _apply_stale_normalization(status)
        assert status["status"] == "ok"
        assert status["stale_reason"] == "age_exceeded"

    def test_running_no_started_at_service_active_stays_running(self, monkeypatch):
        """Ohne Zeit-Info und mit aktivem Service: nichts tun."""
        status = {"status": "running"}
        _apply_stale_normalization(status)
        assert status["status"] == "running"

    def test_running_no_started_at_service_inactive_still_stale(self, monkeypatch):
        """Ohne Zeit-Info aber Service tot: trotzdem stale (Service-Regel greift)."""
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: False,
        )
        status = {"status": "running"}   # kein started_at
        _apply_stale_normalization(status)
        assert status["status"] == "ok"
        assert status["stale_reason"] == "service_inactive"

    def test_running_malformed_started_at_service_active_stays_running(self, monkeypatch):
        status = {"status": "running", "started_at": "not-a-date"}
        _apply_stale_normalization(status)
        # Service aktiv + Zeit unklar → weder service_inactive noch
        # age_exceeded trifft zu → bleibt running.
        assert status["status"] == "running"

    def test_running_malformed_started_at_service_inactive_becomes_stale(self, monkeypatch):
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: False,
        )
        status = {"status": "running", "started_at": "garbage"}
        _apply_stale_normalization(status)
        assert status["status"] == "ok"
        assert status["stale_reason"] == "service_inactive"

    def test_running_naive_started_at_handled(self, monkeypatch):
        """_run_self_update schreibt naive isoformat() ohne TZ. Die
        Stale-Regel muss das tolerieren."""
        naive = (datetime.now() - timedelta(minutes=15)).isoformat()
        status = {"status": "running", "started_at": naive}
        _apply_stale_normalization(status)
        # Mit Service aktiv + naive-dt, age_exceeded wird via
        # astimezone-from-local-time berechnet. 15 min Differenz bleibt
        # > 10 min auf System in UTC oder mit kleiner Offset-Shift.
        assert status["status"] == "ok"
        assert status["stale_reason"] == "age_exceeded"

    def test_response_shape_additive(self, monkeypatch):
        """stale / stale_reason sind zusätzlich — bestehende Keys bleiben."""
        monkeypatch.setattr(
            router_system, "_update_services_active", lambda: False,
        )
        status = {
            "status": "running",
            "started_at": _now_minus(1),
            "commit": "abc",
            "pusher": "admin-manual",
            "custom_x": "y",
        }
        _apply_stale_normalization(status)
        assert status["commit"] == "abc"
        assert status["pusher"] == "admin-manual"
        assert status["custom_x"] == "y"
        assert status["status"] == "ok"
        assert status["stale"] is True
