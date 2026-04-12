"""
test_access_control.py — v2 Access-Control (#590, #607)

Testet _get_user_allowed_projects aus main.py:
- Admin → None (unbegrenzt)
- User in project.members → Projekt drin
- Personal-Projekt immer drin
- Legacy users.json.allowed_projects gemerged
- Loader=None → nur Personal + Legacy
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture
def main_module():
    """Importiere main erst nach conftest-Mocks."""
    from hydrahive_core import main  # noqa: WPS433
    return main


@pytest.fixture
def fake_loader():
    """Loader-Stub mit projects-dict."""
    def _make(projects: dict[str, list[str]]):
        # projects: {pid: [member_usernames]}
        mapping = {
            pid: SimpleNamespace(members=members)
            for pid, members in projects.items()
        }
        return SimpleNamespace(projects=mapping)
    return _make


# ============================================================= admin → None

def test_admin_gibt_none_zurueck(main_module):
    """role=admin → None (unbegrenzt, keine Prüfung nötig)."""
    assert main_module._get_user_allowed_projects("irgendwer", "admin") is None


# ============================================================= members-Scan

def test_user_in_members_kriegt_projekt(main_module, fake_loader):
    loader = fake_loader({"proj-a": ["alice", "bob"], "proj-b": ["carol"]})
    with patch.object(main_module, "_load_users", return_value={"alice": {}}), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=loader):
        result = main_module._get_user_allowed_projects("alice", "user")
    assert "proj-a" in result
    assert "proj-b" not in result


def test_user_nicht_in_members_bleibt_draussen(main_module, fake_loader):
    loader = fake_loader({"proj-a": ["alice"], "proj-b": ["bob"]})
    with patch.object(main_module, "_load_users", return_value={"carol": {}}), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=loader):
        result = main_module._get_user_allowed_projects("carol", "user")
    assert "proj-a" not in result
    assert "proj-b" not in result


def test_personal_projekt_immer_drin(main_module, fake_loader):
    """personal_<username> wird IMMER ergaenzt, auch ohne members-Eintrag."""
    loader = fake_loader({"sonst-nix": ["x"]})
    with patch.object(main_module, "_load_users", return_value={"till": {}}), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=loader):
        result = main_module._get_user_allowed_projects("till", "user")
    assert "personal_till" in result


# ============================================================= Legacy-Fallback

def test_legacy_allowed_projects_gemerged(main_module, fake_loader):
    """users.json.allowed_projects wird als Legacy-Fallback dazugemerged."""
    loader = fake_loader({})  # keine member-Projekte
    users = {"till": {"allowed_projects": ["legacy-a", "legacy-b"]}}
    with patch.object(main_module, "_load_users", return_value=users), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=loader):
        result = main_module._get_user_allowed_projects("till", "user")
    assert "legacy-a" in result
    assert "legacy-b" in result
    assert "personal_till" in result


def test_legacy_und_members_kombiniert(main_module, fake_loader):
    loader = fake_loader({"new-proj": ["till"]})
    users = {"till": {"allowed_projects": ["old-proj"]}}
    with patch.object(main_module, "_load_users", return_value=users), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=loader):
        result = main_module._get_user_allowed_projects("till", "user")
    assert result >= {"new-proj", "old-proj", "personal_till"}


# ============================================================= Loader-None-Fallback

def test_loader_none_nur_personal_plus_legacy(main_module):
    """Wenn Loader noch nicht initialisiert, fallback auf Legacy+Personal."""
    users = {"till": {"allowed_projects": ["legacy-a"]}}
    with patch.object(main_module, "_load_users", return_value=users), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=None):
        result = main_module._get_user_allowed_projects("till", "user")
    assert result == {"legacy-a", "personal_till"}


def test_loader_none_user_ohne_legacy(main_module):
    """Loader=None und keine legacy-Eintraege → nur Personal."""
    with patch.object(main_module, "_load_users", return_value={"neu": {}}), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=None):
        result = main_module._get_user_allowed_projects("neu", "user")
    assert result == {"personal_neu"}


# ============================================================= Fehler-Resilienz

def test_loader_wirft_exception_fallback_auf_personal(main_module, fake_loader):
    """Loader-Exception darf nicht propagiert werden — Access fail-safe."""
    bad_loader = SimpleNamespace()  # kein .projects
    with patch.object(main_module, "_load_users", return_value={"till": {}}), \
         patch("hydrahive_core.project_loader.get_project_loader", return_value=bad_loader):
        result = main_module._get_user_allowed_projects("till", "user")
    # Personal muss trotzdem drin sein
    assert "personal_till" in result
