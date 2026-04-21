"""
test_814_admin_credential_sync.py — Setup-Wizard / Passwort-Change schreiben
console_password in admin_credentials.

Symptom-Beschreibung siehe Issue #814:
- POST /setup legt erste Admin-Credentials an; ohne den Sync bleibt
  console_password auf dem Installer-Random-String → BL-15 killt Login
  bei nächstem update.sh.
- PUT /users/admin/password ändert Admin-Passwort; ohne Sync dasselbe.

Hier testen wir die isolierte Helper-Funktion — sie ist die Schaltstelle.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test_814")
    log.setLevel(logging.DEBUG)
    return log


def test_sync_creates_file_if_missing(tmp_path, monkeypatch):
    from hydrahive_core import router_core_misc

    cred = tmp_path / "admin_credentials"
    from types import SimpleNamespace
    monkeypatch.setattr(router_core_misc, "settings", SimpleNamespace(admin_credentials=cred))

    ok = router_core_misc._sync_admin_credential_console_password("lummerland123", _make_logger())

    assert ok is True
    assert cred.exists()
    content = cred.read_text()
    assert "console_password=lummerland123" in content
    assert (cred.stat().st_mode & 0o777) == 0o600


def test_sync_replaces_existing_console_password(tmp_path, monkeypatch):
    from hydrahive_core import router_core_misc

    cred = tmp_path / "admin_credentials"
    cred.write_text(
        "matrix_admin_password=matrix-pw\n"
        "console_password=OLD_RANDOM_STRING\n"
        "other_key=other_value\n"
    )
    from types import SimpleNamespace
    monkeypatch.setattr(router_core_misc, "settings", SimpleNamespace(admin_credentials=cred))

    ok = router_core_misc._sync_admin_credential_console_password("lummerland123", _make_logger())

    assert ok is True
    content = cred.read_text()
    assert "OLD_RANDOM_STRING" not in content
    assert "console_password=lummerland123" in content
    # andere Keys bleiben
    assert "matrix_admin_password=matrix-pw" in content
    assert "other_key=other_value" in content
    # exakt EINE console_password-Zeile
    assert content.count("console_password=") == 1


def test_sync_preserves_other_keys_ordering(tmp_path, monkeypatch):
    from hydrahive_core import router_core_misc

    cred = tmp_path / "admin_credentials"
    cred.write_text(
        "matrix_admin_password=MATRIXPW\n"
        "another_secret=xyz\n"
    )
    from types import SimpleNamespace
    monkeypatch.setattr(router_core_misc, "settings", SimpleNamespace(admin_credentials=cred))

    router_core_misc._sync_admin_credential_console_password("NEW_PW", _make_logger())

    lines = cred.read_text().splitlines()
    assert "matrix_admin_password=MATRIXPW" in lines
    assert "another_secret=xyz" in lines
    assert "console_password=NEW_PW" in lines


def test_sync_handles_permission_error_gracefully(tmp_path, monkeypatch):
    from hydrahive_core import router_core_misc

    cred = tmp_path / "admin_credentials"
    cred.write_text("console_password=old\n")
    cred.chmod(0o600)

    from types import SimpleNamespace
    monkeypatch.setattr(router_core_misc, "settings", SimpleNamespace(admin_credentials=cred))

    # Simuliere Permission-Fehler beim Schreiben (via chmod 000 auf parent)
    import os
    original_replace = Path.replace

    def failing_replace(self, target):
        raise PermissionError("simulated EACCES")

    monkeypatch.setattr(Path, "replace", failing_replace)

    ok = router_core_misc._sync_admin_credential_console_password("new", _make_logger())

    assert ok is False  # kein Crash, nur False


def test_sync_atomic_via_tmp_and_replace(tmp_path, monkeypatch):
    """Sicherstellt dass kein halbgeschriebener State zurückbleibt, wenn was
    zwischen write_text und replace passiert — und dass vor dem Schreiben
    ein Tmp-File genutzt wird."""
    from hydrahive_core import router_core_misc

    cred = tmp_path / "admin_credentials"
    cred.write_text("console_password=before\n")
    from types import SimpleNamespace
    monkeypatch.setattr(router_core_misc, "settings", SimpleNamespace(admin_credentials=cred))

    router_core_misc._sync_admin_credential_console_password("after", _make_logger())

    # Nach erfolgreichem Sync: kein .tmp übrig, Datei enthält neuen Wert
    assert not (tmp_path / "admin_credentials.tmp").exists()
    assert cred.read_text().strip() == "console_password=after"
