"""#802 Phase 4: cleanup_stale_workspace_artifacts Unit-Tests."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.cleanup_service import cleanup_stale_workspace_artifacts
from hydrahive_core.jobs_service import JobNotFoundError, JobStorageError


class _MockMeta:
    def __init__(self, artifacts: list[dict]):
        self.artifacts = artifacts


class _MockJobService:
    """Minimal-Mock mit get()-Methode. Metas-Dict kann Exception-Klassen
    enthalten — die werden bei get() geworfen."""

    def __init__(self, metas: dict):
        self._metas = metas

    def get(self, job_id: str):
        if job_id not in self._metas:
            raise JobNotFoundError(job_id)
        val = self._metas[job_id]
        if isinstance(val, type) and issubclass(val, Exception):
            raise val()
        return _MockMeta(val.get("artifacts", []))


def _make_artifact(
    tmp_path: Path, project_id: str, type_: str, date: str,
    job_id: str, filename: str, age_days: float,
) -> Path:
    """Helper: schreibt ein Workspace-Artifact-File mit manipulierter mtime."""
    d = tmp_path / project_id / ".hydrahive" / "artifacts" / type_ / date
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{job_id}__{filename}"
    f.write_bytes(b"X")
    # Korrekt: os.utime (nicht Attribut-Setter).
    ts = time.time() - age_days * 86_400
    os.utime(str(f), (ts, ts))
    return f


def test_orphan_deleted_when_job_meta_missing(tmp_path):
    """Job-Meta weg + Datei älter als TTL → gelöscht."""
    f = _make_artifact(
        tmp_path, "proj_a", "image", "2026-03-20",
        "job_abc123", "img.png", age_days=31,
    )
    svc = _MockJobService({})
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 1
    assert not f.exists()


def test_recent_orphan_not_deleted(tmp_path):
    """Orphan aber jünger als TTL → bleibt."""
    f = _make_artifact(
        tmp_path, "proj_b", "music", "2026-04-20",
        "job_recent", "song.mp3", age_days=1,
    )
    svc = _MockJobService({})
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 0
    assert f.exists()


def test_legitimate_artifact_not_deleted(tmp_path):
    """Job existiert + Datei in meta.artifacts → bleibt auch wenn alt."""
    f = _make_artifact(
        tmp_path, "proj_c", "video", "2026-03-20",
        "job_legit", "clip.mp4", age_days=100,
    )
    svc = _MockJobService({
        "job_legit": {"artifacts": [{"filename": "clip.mp4"}]},
    })
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 0
    assert f.exists()


def test_artifact_not_in_meta_is_deleted(tmp_path):
    """Job existiert, aber Datei nicht in meta.artifacts → Orphan → gelöscht."""
    f = _make_artifact(
        tmp_path, "proj_d", "image", "2026-03-20",
        "job_notlisted", "ghost.png", age_days=31,
    )
    svc = _MockJobService({
        "job_notlisted": {"artifacts": [{"filename": "other.png"}]},
    })
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 1
    assert not f.exists()


def test_corrupt_meta_not_deleted(tmp_path):
    """JobStorageError (korrupte Meta) → skip, Datei bleibt."""
    f = _make_artifact(
        tmp_path, "proj_e", "music", "2026-03-20",
        "job_corrupt", "song.mp3", age_days=100,
    )
    svc = _MockJobService({"job_corrupt": JobStorageError})
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 0
    assert f.exists()


def test_empty_date_dir_removed_after_delete(tmp_path):
    """Wenn alle Dateien in <date>/ orphan → <date>/ wird weg."""
    f = _make_artifact(
        tmp_path, "proj_f", "image", "2026-03-20",
        "job_clean", "x.png", age_days=31,
    )
    svc = _MockJobService({})
    date_dir = f.parent
    cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert not date_dir.exists()


def test_empty_type_dir_removed_after_delete(tmp_path):
    """Wenn alle <date>/ unter <type>/ leer → <type>/ wird weg."""
    f = _make_artifact(
        tmp_path, "proj_g", "music", "2026-03-20",
        "job_clean", "x.mp3", age_days=31,
    )
    svc = _MockJobService({})
    type_dir = f.parent.parent
    cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert not type_dir.exists()


def test_file_without_job_prefix_skipped(tmp_path):
    """Dateien ohne 'job_xxx__' Präfix werden ignoriert (User-Dateien etc.)."""
    d = tmp_path / "proj_h" / ".hydrahive" / "artifacts" / "other" / "2026-03-20"
    d.mkdir(parents=True)
    user_file = d / "random-user-upload.png"
    user_file.write_bytes(b"X")
    ts = time.time() - 100 * 86_400
    os.utime(str(user_file), (ts, ts))
    svc = _MockJobService({})
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 0
    assert user_file.exists()


def test_no_projects_dir_returns_zero(tmp_path):
    """Nichtexistentes projects_dir → 0, kein Crash."""
    svc = _MockJobService({})
    deleted = cleanup_stale_workspace_artifacts(
        str(tmp_path / "nope"), svc, max_age_days=30,
    )
    assert deleted == 0


def test_project_without_hydrahive_dir_skipped(tmp_path):
    """Projekt ohne .hydrahive/ → wird übersprungen."""
    (tmp_path / "proj_empty").mkdir()
    svc = _MockJobService({})
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 0


def test_mixed_projects_and_types(tmp_path):
    """Mehrere Projekte, Types, Dates: nur die alten Orphans löschen."""
    # Alt + Orphan
    f_old_orphan = _make_artifact(
        tmp_path, "proj_x", "image", "2026-03-20",
        "job_oldorph", "a.png", age_days=31,
    )
    # Neu + Orphan
    f_new_orphan = _make_artifact(
        tmp_path, "proj_x", "music", "2026-04-20",
        "job_neworph", "b.mp3", age_days=1,
    )
    # Alt + legit
    f_old_legit = _make_artifact(
        tmp_path, "proj_y", "video", "2026-03-20",
        "job_legit2", "c.mp4", age_days=31,
    )
    svc = _MockJobService({
        "job_legit2": {"artifacts": [{"filename": "c.mp4"}]},
    })
    deleted = cleanup_stale_workspace_artifacts(str(tmp_path), svc, max_age_days=30)
    assert deleted == 1
    assert not f_old_orphan.exists()
    assert f_new_orphan.exists()
    assert f_old_legit.exists()
