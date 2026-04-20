"""Test #777: SSH Host-Key enforcement default is now 'strict'.

Vorher (bis 2026-04-20): Default 'warn' — unverifizierte Hosts durften
verbinden, nur Log-Hinweis.
Neu (#777): Default 'strict' — unverifizierte Hosts werden geblockt.
Opt-out fuer Dev-Setups: HYDRAHIVE_REQUIRE_HOST_KEYS=warn.
"""
from __future__ import annotations

from hydrahive_core.ssh_known_hosts import get_enforcement_mode


def test_default_is_strict(monkeypatch):
    """Ohne Env-Variable ist der Modus 'strict'."""
    monkeypatch.delenv("HYDRAHIVE_REQUIRE_HOST_KEYS", raising=False)
    assert get_enforcement_mode() == "strict"


def test_explicit_strict(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "strict")
    assert get_enforcement_mode() == "strict"


def test_explicit_warn_opts_out(monkeypatch):
    """Dev-Setups koennen weiterhin per Env auf 'warn' zurueckwechseln."""
    monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "warn")
    assert get_enforcement_mode() == "warn"


def test_false_variants_opt_out(monkeypatch):
    for val in ("0", "false", "no"):
        monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", val)
        assert get_enforcement_mode() == "warn", f"Expected warn for env={val}"


def test_unknown_value_defaults_strict(monkeypatch):
    """Unbekannter Wert faellt auf strict zurueck (sicherer Default)."""
    monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "maybe")
    assert get_enforcement_mode() == "strict"


def test_empty_value_defaults_strict(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "")
    assert get_enforcement_mode() == "strict"


def test_uppercase_warn_still_opts_out(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_REQUIRE_HOST_KEYS", "WARN")
    assert get_enforcement_mode() == "warn"
