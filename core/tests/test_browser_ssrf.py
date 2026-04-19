"""
test_browser_ssrf.py — SSRF-Schutz in browser_navigate (#745)

Deckt den `_validate_safe_url`-Validator + den Tool-execute-Pfad ab.
Playwright wird nicht aufgerufen — der Validator ist synchron und
schliesst die Anfrage *vor* dem `page.goto()` ab.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.browser_tools import (
    BrowserNavigateTool,
    UnsafeURLError,
    _is_private_ip,
    _validate_safe_url,
)


# ============================================================= _is_private_ip


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.5.5.5",           # loopback
    "10.0.0.1", "10.255.255.254",       # RFC1918
    "172.16.0.1", "172.31.255.254",
    "192.168.0.1", "192.168.255.254",
    "169.254.169.254",                   # Cloud-Metadata
    "0.0.0.0",
    "224.0.0.1",                         # multicast
    "255.255.255.255",                   # broadcast
    "::1",                               # IPv6 loopback
    "fc00::1", "fd00::1",                # IPv6 unique local
    "fe80::1",                           # IPv6 link-local
])
def test_is_private_ip_blocks(ip):
    assert _is_private_ip(ip), f"{ip} should be private"


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34",
    "2606:4700:4700::1111",              # Cloudflare IPv6
])
def test_is_private_ip_allows_public(ip):
    assert not _is_private_ip(ip), f"{ip} should be public"


def test_is_private_ip_invalid_literal_returns_false():
    """Kein IP-Literal → False (Caller macht DNS-Resolve)."""
    assert _is_private_ip("example.com") is False


# ============================================================= _validate_safe_url: Scheme


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<script>x</script>",
    "ftp://example.com/",
    "gopher://example.com/",
    "about:blank",
    "",
])
def test_validate_scheme_blocks_non_http(url):
    with pytest.raises(UnsafeURLError):
        _validate_safe_url(url)


# ============================================================= _validate_safe_url: Loopback-Namen


@pytest.mark.parametrize("host", [
    "localhost",
    "LOCALHOST",
    "Localhost.LocalDomain",
    "broadcasthost",
    "ip6-localhost",
    "ip6-loopback",
])
def test_validate_blocks_loopback_hostnames(host):
    with pytest.raises(UnsafeURLError, match="Loopback-Hostname"):
        _validate_safe_url(f"http://{host}/")


# ============================================================= _validate_safe_url: IP-Literale


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:8765/api/admin/update/trigger",
    "http://10.0.0.5/",
    "http://192.168.178.220/",
    "http://172.16.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://0.0.0.0/",
])
def test_validate_blocks_private_ip_literals(url):
    with pytest.raises(UnsafeURLError, match="Private/Loopback-IP"):
        _validate_safe_url(url)


def test_validate_blocks_explicit_https_loopback():
    with pytest.raises(UnsafeURLError):
        _validate_safe_url("https://127.0.0.1:8443/")


# ============================================================= _validate_safe_url: DNS-Rebinding


def test_validate_blocks_hostname_resolving_to_private_ip(monkeypatch):
    """Hostname der zu 127.0.0.1 resolved → geblockt (DNS-Rebinding-Schutz)."""
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    monkeypatch.setattr("hydrahive_core.browser_tools.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeURLError, match="loest auf private"):
        _validate_safe_url("http://rebind.example.com/")


def test_validate_blocks_when_dns_fails(monkeypatch):
    """DNS-Fehler → block (nicht stillschweigend durchlassen)."""
    def fake_getaddrinfo(host, *args, **kwargs):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr("hydrahive_core.browser_tools.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeURLError, match="DNS-Resolution"):
        _validate_safe_url("http://does-not-exist.invalid/")


# ============================================================= _validate_safe_url: legit URLs


def test_validate_accepts_public_url(monkeypatch):
    """Normale Public-URL geht durch."""
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
    monkeypatch.setattr("hydrahive_core.browser_tools.socket.getaddrinfo", fake_getaddrinfo)

    assert _validate_safe_url("https://example.com/foo?bar=1") == "https://example.com/foo?bar=1"


def test_validate_accepts_cloudflare_dns(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:4700:4700::1111", 0, 0, 0))]
    monkeypatch.setattr("hydrahive_core.browser_tools.socket.getaddrinfo", fake_getaddrinfo)

    assert _validate_safe_url("https://1.1.1.1.cloudflare-dns.com/") is not None


# ============================================================= Env-Override


def test_env_override_skips_private_check(monkeypatch):
    """HYDRAHIVE_BROWSER_ALLOW_PRIVATE=1 laesst Loopback durch."""
    monkeypatch.setenv("HYDRAHIVE_BROWSER_ALLOW_PRIVATE", "1")
    assert _validate_safe_url("http://127.0.0.1/") == "http://127.0.0.1/"
    assert _validate_safe_url("http://localhost/") == "http://localhost/"


def test_env_override_does_not_skip_scheme(monkeypatch):
    """Env-Override kippt nur den IP/Host-Block, Scheme-Check bleibt aktiv."""
    monkeypatch.setenv("HYDRAHIVE_BROWSER_ALLOW_PRIVATE", "1")
    with pytest.raises(UnsafeURLError, match="Schema"):
        _validate_safe_url("file:///etc/passwd")


# ============================================================= Tool-execute-Pfad


def test_execute_returns_error_dict_for_ssrf():
    """BrowserNavigateTool.execute gibt {'error': ...} zurueck,
    ohne page.goto zu triggern, wenn die URL unsafe ist."""
    tool = BrowserNavigateTool()
    with mock.patch("hydrahive_core.browser_tools._get_page") as get_page:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id="test_project",
            url="http://127.0.0.1:8765/api/admin/update/trigger",
        ))
    assert "error" in result
    assert "nicht erlaubt" in result["error"].lower()
    # _get_page darf NICHT aufgerufen werden — der SSRF-Block muss VOR der
    # Browser-Initialisierung greifen.
    get_page.assert_not_called()


def test_execute_returns_error_for_invalid_scheme():
    tool = BrowserNavigateTool()
    with mock.patch("hydrahive_core.browser_tools._get_page") as get_page:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id="test_project",
            url="file:///etc/hostname",
        ))
    assert "error" in result
    get_page.assert_not_called()
