"""
test_810_tailscale_ssrf.py — SSRF-Guard für Tailscale-Discovery (#810)

_check_hydrahive darf nur Tailscale-CGNAT (100.64/10), RFC1918 oder
Loopback probieren. Public IPs, Metadata-Endpoints (169.254.169.254),
Garbage-Input → abgelehnt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.mark.parametrize("ip", [
    "100.110.63.75",       # Tailscale CGNAT — prod-Beispiel .181
    "100.64.0.1",          # CGNAT lower
    "100.127.255.254",     # CGNAT upper
    "192.168.178.177",     # RFC1918 — Home-LAN
    "10.0.0.5",            # RFC1918
    "172.16.0.1",          # RFC1918
    "172.31.255.254",      # RFC1918 upper
    "127.0.0.1",           # Loopback
    "fd00::1",             # IPv6 ULA
    "fe80::1",             # IPv6 link-local
])
def test_safe_targets_accepted(ip):
    from hydrahive_core.router_tailscale import _is_safe_probe_target
    assert _is_safe_probe_target(ip) is True, f"erwartet safe: {ip}"


@pytest.mark.parametrize("ip", [
    "8.8.8.8",                  # Google DNS
    "1.1.1.1",                  # Cloudflare DNS
    "169.254.169.254",          # AWS/GCP Metadata endpoint
    "169.254.0.1",              # link-local IPv4 — blockieren
    "192.0.2.1",                # TEST-NET
    "100.128.0.1",              # CGNAT Grenzbereich (nicht Tailscale)
    "100.63.255.254",           # CGNAT Grenzbereich (nicht Tailscale)
    "172.15.255.255",           # unmittelbar VOR RFC1918-Start
    "172.32.0.0",               # unmittelbar NACH RFC1918-Ende
    "",                         # leer
    "notanip",                  # garbage
    "99999.0.0.0",              # invalide Syntax
    "http://192.168.1.1",       # URL-Form, keine IP
])
def test_unsafe_targets_rejected(ip):
    from hydrahive_core.router_tailscale import _is_safe_probe_target
    assert _is_safe_probe_target(ip) is False, f"erwartet unsafe: {ip}"


def test_check_hydrahive_returns_none_on_public_ip(monkeypatch):
    """Auch wenn jemand _check_hydrahive direkt mit Public-IP ruft:
    kein Probe, kein Request."""
    from hydrahive_core import router_tailscale

    calls = []
    def fake_urlopen(*a, **kw):
        calls.append(a[0].full_url if hasattr(a[0], "full_url") else a[0])
        raise RuntimeError("should not be reached")
    monkeypatch.setattr(router_tailscale.urllib.request, "urlopen", fake_urlopen)

    assert router_tailscale._check_hydrahive("8.8.8.8") is None
    assert router_tailscale._check_hydrahive("169.254.169.254") is None
    assert router_tailscale._check_hydrahive("") is None
    assert calls == [], f"urlopen wurde aufgerufen: {calls}"


def test_check_hydrahive_probes_safe_ip(monkeypatch):
    """Safe-IP → urlopen wird gerufen (auch wenn es dann fehlschlägt)."""
    from hydrahive_core import router_tailscale

    calls = []
    def fake_urlopen(req, *a, **kw):
        calls.append(req.full_url if hasattr(req, "full_url") else str(req))
        raise OSError("connection refused — expected in test")
    monkeypatch.setattr(router_tailscale.urllib.request, "urlopen", fake_urlopen)

    router_tailscale._check_hydrahive("100.110.63.75")
    assert any("100.110.63.75" in url for url in calls), \
        "Safe-IP wurde nicht geprobed"
