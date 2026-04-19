"""
test_project_loader.py — v2 ProjectLoader (#590)

Testet:
- _scan_all() findet Projekt-Dirs
- _deleted_ Prefix wird übersprungen (project_loader.py:80)
- register() lädt config.yaml korrekt
- get(project_id) liefert die Config
- projects-Property ist thread-safe Kopie
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture
def tmp_projects_dir(tmp_path: Path) -> Path:
    """
    Temp /projects/ mit:
      proj-a/  (valid v2)
      proj-b/  (valid v2, mit members)
      _deleted_proj-c_123/  (soft-deleted → muss ignoriert werden)
      proj-no-config/ (kein config.yaml → load liefert None, skip)
    """
    def _write_config(pdir: Path, pid: str, members: list[str] | None = None) -> None:
        pdir.mkdir(parents=True, exist_ok=True)
        body = f'id: {pid}\nversion: "2.0.0"\nidentity:\n  name: {pid}\n'
        if members:
            body += "members:\n" + "".join(f"  - {m}\n" for m in members)
        (pdir / "config.yaml").write_text(body)

    _write_config(tmp_path / "proj-a", "proj-a")
    _write_config(tmp_path / "proj-b", "proj-b", members=["alice", "bob"])
    _write_config(tmp_path / "_deleted_proj-c_1234567890", "proj-c")
    (tmp_path / "proj-no-config").mkdir()
    return tmp_path


@pytest.fixture
def loader(tmp_projects_dir: Path):
    """ProjectLoader-Instanz nach initialem Scan (ohne Watcher)."""
    from hydrahive_core.project_loader import ProjectLoader
    pl = ProjectLoader(projects_dir=tmp_projects_dir)
    pl._dir.mkdir(parents=True, exist_ok=True)
    pl._scan_all()
    return pl


# ============================================================= _scan_all

def test_scan_findet_valide_projekte(loader):
    ids = set(loader.projects.keys())
    assert "proj-a" in ids
    assert "proj-b" in ids


def test_scan_ueberspringt_deleted_prefix(loader):
    """_deleted_ Projekt-Dirs duerfen nicht registriert werden."""
    assert "proj-c" not in loader.projects
    # Auch kein Eintrag mit dem _deleted_ Praefix-Namen
    assert not any(pid.startswith("_deleted_") for pid in loader.projects)


def test_scan_ueberspringt_dir_ohne_config(loader):
    """Verzeichnis ohne config.yaml/project.yaml wird ignoriert."""
    assert "proj-no-config" not in loader.projects


def test_scan_ueberspringt_fremde_config_yaml(tmp_projects_dir):
    """Nicht jede App-config.yaml unter /projects ist ein HydraHive-Projekt."""
    from hydrahive_core.project_loader import ProjectLoader

    xiao = tmp_projects_dir / "xiaozhi-config"
    xiao.mkdir()
    (xiao / "config.yaml").write_text(
        "\n".join([
            "server:",
            "  websocket: ws://example.invalid/xiaozhi/v1/",
            "selected_module:",
            "  LLM: OpenAILLM",
            "tts:",
            "  EdgeTTS:",
            "    voice: de-DE-ConradNeural",
            "",
        ]),
        encoding="utf-8",
    )

    pl = ProjectLoader(projects_dir=tmp_projects_dir)
    pl._scan_all()

    assert "xiaozhi-config" not in pl.projects


def test_register_entfernt_stale_eintrag_wenn_config_ungueltig(tmp_projects_dir, loader):
    """Watchdog-Revalidierung darf invalid gewordene Projekte nicht in Memory behalten."""
    project_dir = tmp_projects_dir / "proj-a"
    assert "proj-a" in loader.projects

    (project_dir / "config.yaml").write_text(
        "\n".join([
            "server:",
            "  websocket: ws://example.invalid/xiaozhi/v1/",
            "",
        ]),
        encoding="utf-8",
    )

    cfg = loader.register(project_dir)

    assert cfg is None
    assert "proj-a" not in loader.projects


# ============================================================= get + members

def test_get_liefert_projektconfig(loader):
    cfg = loader.get("proj-b")
    assert cfg is not None
    assert cfg.id == "proj-b"
    assert cfg.members == ["alice", "bob"]


def test_get_unbekannt_gibt_none(loader):
    assert loader.get("nicht-da") is None


# ============================================================= projects-property

def test_projects_returns_dict_copy(loader):
    """projects-Property liefert Kopie — externe Mutation darf Loader nicht beeinflussen."""
    snap = loader.projects
    snap["injected"] = "x"
    assert "injected" not in loader.projects


# ============================================================= register manuell

def test_register_manuell_nach_create(tmp_projects_dir, loader):
    """register() kann nach REST-API-Create ein neues Projekt einbinden."""
    new_dir = tmp_projects_dir / "proj-new"
    new_dir.mkdir()
    (new_dir / "config.yaml").write_text(
        'id: proj-new\nversion: "2.0.0"\nidentity:\n  name: proj-new\n'
    )
    cfg = loader.register(new_dir)
    assert cfg is not None
    assert cfg.id == "proj-new"
    assert "proj-new" in loader.projects


# ============================================================= #707 Phantom-Filter

def test_scan_ueberspringt_template_leak_ohne_name(tmp_projects_dir):
    """#707: Eine Template-config.yaml (identity.name='') darf nicht als Projekt auftauchen.

    Reproduziert das Phantom-Problem: alte Installer-Versionen haben
    installer/templates/*/ nach /projects/ kopiert. Templates haben leeren
    name — ohne Filter taucht das als Phantom-Projekt in der API auf und
    kehrt nach Lösch-Versuchen durch Restart wieder zurück.
    """
    from hydrahive_core.project_loader import ProjectLoader

    phantom = tmp_projects_dir / "blank"
    phantom.mkdir()
    (phantom / "config.yaml").write_text(
        "\n".join([
            'version: "2.0.0"',
            "identity:",
            '  name: ""',
            '  description: ""',
            "llm:",
            "  provider: anthropic",
            "  model: claude-sonnet-4-6",
            "members: []",
            "",
        ]),
        encoding="utf-8",
    )

    pl = ProjectLoader(projects_dir=tmp_projects_dir)
    pl._scan_all()

    assert "blank" not in pl.projects


def test_register_filtert_phantom_projekt_aus_bestehendem_state(tmp_projects_dir, loader):
    """#707: Wird ein bestehendes Projekt auf name='' geändert, fliegt es aus dem State."""
    project_dir = tmp_projects_dir / "proj-a"
    assert "proj-a" in loader.projects

    (project_dir / "config.yaml").write_text(
        'id: proj-a\nversion: "2.0.0"\nidentity:\n  name: ""\n',
        encoding="utf-8",
    )

    cfg = loader.register(project_dir)

    assert cfg is None
    assert "proj-a" not in loader.projects


# ============================================================= Globaler Singleton

def test_global_loader_set_get():
    from hydrahive_core import project_loader as pl
    dummy = object()
    pl.set_global_loader(dummy)  # type: ignore[arg-type]
    try:
        assert pl.get_project_loader() is dummy
    finally:
        pl.set_global_loader(None)  # type: ignore[arg-type]
