"""
test_memory_diagnose.py — Legacy-Feedback-Diagnose + Cleanup-Script (#715, #716)
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.memory_diagnose import (
    LEGACY_CORE_POLICY_FEEDBACK_FILES,
    scan_legacy_feedback,
    summarize_report,
)


def _mk_project(root: Path, pid: str, memory_files: dict[str, str]) -> Path:
    pdir = root / pid
    mdir = pdir / "memory"
    mdir.mkdir(parents=True)
    for name, content in memory_files.items():
        (mdir / name).write_text(content, encoding="utf-8")
    return pdir


# ============================================================= scan


def test_scan_erkennt_duplikat_mit_keyword(tmp_path: Path):
    _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Token-Disziplin: pro Recherche Budget einhalten.",
    })
    hits = scan_legacy_feedback(tmp_path)
    assert len(hits) == 1
    h = hits[0]
    assert h.project_id == "alpha"
    assert h.keyword_match is True
    assert "token" in h.matched_keywords


def test_scan_markiert_uncertain_wenn_content_nicht_matcht(tmp_path: Path):
    """Datei mit Allowlist-Name aber wirklich anderem Inhalt wird uncertain markiert."""
    _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Notiz: Kunde XY moechte Feature Z bis April.",
    })
    hits = scan_legacy_feedback(tmp_path)
    assert len(hits) == 1
    assert hits[0].keyword_match is False
    assert hits[0].matched_keywords == []


def test_scan_ueberspringt_nicht_allowlistete_dateien(tmp_path: Path):
    """feedback_projektspezifisch.md bleibt unberuehrt."""
    _mk_project(tmp_path, "alpha", {
        "feedback_projektspezifisch.md": "Kunde-spezifische Prod-Regel: keine SSH.",
    })
    hits = scan_legacy_feedback(tmp_path)
    assert hits == []


def test_scan_ueberspringt_deleted_projekte(tmp_path: Path):
    _mk_project(tmp_path, "_deleted_alpha_1234", {
        "feedback_token_discipline.md": "token budget",
    })
    hits = scan_legacy_feedback(tmp_path)
    assert hits == []


def test_scan_ohne_memory_dir(tmp_path: Path):
    """Projekt ohne memory/ wird sauber uebersprungen."""
    (tmp_path / "alpha").mkdir()
    hits = scan_legacy_feedback(tmp_path)
    assert hits == []


def test_scan_mehrere_projekte_und_dateien(tmp_path: Path):
    _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md":  "Token-Disziplin beachten.",
        "feedback_memory_budget.md":     "Memory-Budget pro Request.",
    })
    _mk_project(tmp_path, "beta", {
        "feedback_bulk_lookups.md":      "Bulk parallel grep nutzen.",
    })
    hits = scan_legacy_feedback(tmp_path)
    assert len(hits) == 3
    by_pid = {h.project_id for h in hits}
    assert by_pid == {"alpha", "beta"}


# ============================================================= summarize


def test_summarize_aggregiert_sauber(tmp_path: Path):
    _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md":  "Token-Budget",
        "feedback_memory_budget.md":     "Kein erwartetes Wort hier.",  # uncertain
    })
    hits = scan_legacy_feedback(tmp_path)
    report = summarize_report(hits)
    assert report["total_files"] == 2
    assert report["safe_to_remove"] == 1
    assert report["uncertain_content"] == 1
    assert report["projects_affected"] == 1
    assert report["per_project_count"] == {"alpha": 2}
    assert len(report["files"]) == 2


def test_allowlist_ist_nicht_leer():
    """Sanity: Allowlist muss gepflegt sein."""
    assert len(LEGACY_CORE_POLICY_FEEDBACK_FILES) >= 10
    for name, keywords in LEGACY_CORE_POLICY_FEEDBACK_FILES.items():
        assert name.startswith("feedback_")
        assert name.endswith(".md")
        assert len(keywords) >= 1


# ============================================================= Cleanup-Script


def _repo_root() -> Path:
    # /home/.../core/tests/test_memory_diagnose.py -> up 3 -> repo root
    return Path(__file__).resolve().parent.parent.parent


def test_script_dry_run_listet_aber_loescht_nicht(tmp_path: Path):
    pdir = _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Token-Disziplin: Budget beachten.",
    })
    target = pdir / "memory" / "feedback_token_discipline.md"
    assert target.exists()

    script = _repo_root() / "scripts" / "dedupe_legacy_feedback.py"
    if not script.exists():
        pytest.skip("scripts/dedupe_legacy_feedback.py nicht vorhanden")

    r = subprocess.run(
        [sys.executable, str(script), "--projects-dir", str(tmp_path), "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert target.exists(), "Dry-run darf nichts loeschen"
    assert "dry-run" in r.stdout.lower()
    assert "feedback_token_discipline.md" in r.stdout


def test_script_execute_verschiebt_ins_backup(tmp_path: Path):
    pdir = _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Token-Disziplin: Budget beachten.",
    })
    target = pdir / "memory" / "feedback_token_discipline.md"
    original_content = target.read_text(encoding="utf-8")

    script = _repo_root() / "scripts" / "dedupe_legacy_feedback.py"
    if not script.exists():
        pytest.skip("scripts/dedupe_legacy_feedback.py nicht vorhanden")

    r = subprocess.run(
        [sys.executable, str(script), "--projects-dir", str(tmp_path), "--execute"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert not target.exists(), "Execute muss die Datei aus dem Memory entfernen"

    # Backup muss existieren und Inhalt erhalten haben
    backups = list((pdir / "memory").glob(".legacy_backup_*"))
    assert len(backups) == 1
    backed_up = backups[0] / "feedback_token_discipline.md"
    assert backed_up.exists()
    assert backed_up.read_text(encoding="utf-8") == original_content


def test_script_laesst_uncertain_ohne_force_stehen(tmp_path: Path):
    pdir = _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Kunden-spezifische Notiz, kein Core-Regel-Zitat.",
    })
    target = pdir / "memory" / "feedback_token_discipline.md"

    script = _repo_root() / "scripts" / "dedupe_legacy_feedback.py"
    if not script.exists():
        pytest.skip("scripts/dedupe_legacy_feedback.py nicht vorhanden")

    r = subprocess.run(
        [sys.executable, str(script), "--projects-dir", str(tmp_path), "--execute"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert target.exists(), "Uncertain ohne --force-uncertain muss stehen bleiben"
    assert "uncertain" in r.stdout.lower()


def test_script_force_uncertain_entfernt_auch_zweifelhafte(tmp_path: Path):
    pdir = _mk_project(tmp_path, "alpha", {
        "feedback_token_discipline.md": "Kunden-spezifische Notiz.",
    })
    target = pdir / "memory" / "feedback_token_discipline.md"

    script = _repo_root() / "scripts" / "dedupe_legacy_feedback.py"
    if not script.exists():
        pytest.skip("scripts/dedupe_legacy_feedback.py nicht vorhanden")

    r = subprocess.run(
        [sys.executable, str(script),
         "--projects-dir", str(tmp_path), "--execute", "--force-uncertain"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert not target.exists()
    backups = list((pdir / "memory").glob(".legacy_backup_*"))
    assert len(backups) == 1
