"""
test_parse_duration.py — Tests für _parse_duration() in agent_runtime.py
"""
import pytest
from hydrahive_core.agent_runtime import _parse_duration

DEFAULT = 30.0


class TestParseDurationNummernEingaben:
    def test_int_wird_zu_float(self):
        assert _parse_duration(42, DEFAULT) == 42.0

    def test_float_bleibt_float(self):
        assert _parse_duration(1.5, DEFAULT) == 1.5

    def test_null_gibt_default(self):
        assert _parse_duration(None, DEFAULT) == DEFAULT

    def test_null_default_wird_zurueckgegeben(self):
        assert _parse_duration(None, 99.0) == 99.0


class TestParseDurationStrings:
    def test_sekunden_suffix(self):
        assert _parse_duration("30s", DEFAULT) == 30.0

    def test_minuten_suffix(self):
        assert _parse_duration("2m", DEFAULT) == 120.0

    def test_stunden_suffix(self):
        assert _parse_duration("1h", DEFAULT) == 3600.0

    def test_kein_suffix_wird_als_sekunden_interpretiert(self):
        assert _parse_duration("45", DEFAULT) == 45.0

    def test_dezimalzahl_mit_suffix(self):
        assert _parse_duration("1.5m", DEFAULT) == 90.0

    def test_whitespace_wird_ignoriert(self):
        assert _parse_duration("  30s  ", DEFAULT) == 30.0

    def test_null_sekunden(self):
        assert _parse_duration("0s", DEFAULT) == 0.0

    def test_grosse_werte(self):
        assert _parse_duration("24h", DEFAULT) == 86400.0


class TestParseDurationFallback:
    def test_ungueltige_einheit_gibt_default(self):
        assert _parse_duration("30x", DEFAULT) == DEFAULT

    def test_leerer_string_gibt_default(self):
        assert _parse_duration("", DEFAULT) == DEFAULT

    def test_buchstaben_gibt_default(self):
        assert _parse_duration("abc", DEFAULT) == DEFAULT

    def test_negativzahl_gibt_default(self):
        # Negativwerte passen nicht auf das Regex \d+ → Default
        assert _parse_duration("-5s", DEFAULT) == DEFAULT
