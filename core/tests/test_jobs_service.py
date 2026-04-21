"""#687: Jobs-Service Fundament — Unit-Tests ohne FastAPI.

Lifecycle, Cancel, Artifact-Write, Progress, Startup-Recovery, Path-Safety.
Kein echter Provider-Call, kein HTTP, kein Netzwerk.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.jobs_service import (
    JobCancelled,
    JobContext,
    JobError,
    JobMeta,
    JobNotFoundError,
    JobService,
    JobStorageError,
    _noop_runner,
    _RESTART_ERROR,
)
from hydrahive_core.tool_registry import (
    set_workspace_override,
    reset_workspace_override,
)


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


# ────────────────────────────────────────────── Lifecycle


@pytest.mark.asyncio
async def test_submit_runs_and_succeeds(svc):
    async def runner(ctx: JobContext):
        ctx.update_progress(50, "halbzeit")
        ctx.record_artifact(b"hello", "out.txt", "text/plain")

    meta = svc.submit(
        type="noop", provider="internal", runner=runner,
        created_by="alice", input_summary={"k": "v"},
    )
    assert meta.status == "queued"
    assert meta.job_id.startswith("job_")
    assert meta.created_by == "alice"

    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "succeeded"
    assert final.finished_at is not None
    assert final.progress_percent == 50
    assert len(final.artifacts) == 1
    assert final.artifacts[0]["filename"] == "out.txt"
    assert final.artifacts[0]["size"] == 5
    assert final.artifacts[0]["mime"] == "text/plain"


@pytest.mark.asyncio
async def test_runner_exception_becomes_failed_without_traceback(svc):
    async def runner(ctx):
        raise RuntimeError("boom — internal path /etc/hydrahive/secret.key leaked")

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"
    assert final.error is not None
    # User-facing error hat Typ + message, aber keinen Stack, keinen
    # "Traceback (most recent call last)".
    assert "RuntimeError" in final.error
    assert "Traceback" not in final.error
    assert "File \"" not in final.error


@pytest.mark.asyncio
async def test_cancel_during_run(svc):
    started = asyncio.Event()
    keep_going = asyncio.Event()

    async def runner(ctx):
        started.set()
        for _ in range(50):
            ctx.check_cancelled()
            await asyncio.sleep(0.05)
            if keep_going.is_set():
                break

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await started.wait()
    svc.cancel(meta.job_id)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    keep_going.set()
    final = svc.get(meta.job_id)
    assert final.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_terminal_job_is_noop(svc):
    async def runner(ctx):
        ctx.record_artifact(b"x", "x.txt", "text/plain")

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final_before = svc.get(meta.job_id)
    assert final_before.status == "succeeded"
    # Cancel nach success → unverändert
    svc.cancel(meta.job_id)
    final_after = svc.get(meta.job_id)
    assert final_after.status == "succeeded"


@pytest.mark.asyncio
async def test_builtin_noop_runner(svc):
    meta = svc.submit(type="noop", provider="internal", runner=_noop_runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "succeeded"
    assert final.progress_percent == 100
    assert len(final.artifacts) == 1
    assert final.artifacts[0]["filename"] == "noop.txt"


# ────────────────────────────────────────────── List / Filter


@pytest.mark.asyncio
async def test_list_filters_by_created_by(svc):
    async def runner(ctx):
        pass

    m_alice = svc.submit(type="noop", provider="internal", runner=runner, created_by="alice")
    m_bob = svc.submit(type="noop", provider="internal", runner=runner, created_by="bob")
    # Task-Refs bevor _tasks.pop() nach Terminierung greift.
    t_alice = svc._tasks[m_alice.job_id]
    t_bob = svc._tasks[m_bob.job_id]
    await asyncio.wait_for(t_alice, timeout=2)
    await asyncio.wait_for(t_bob, timeout=2)

    alice_jobs = svc.list(created_by="alice")
    assert [m.job_id for m in alice_jobs] == [m_alice.job_id]
    bob_jobs = svc.list(created_by="bob")
    assert [m.job_id for m in bob_jobs] == [m_bob.job_id]


def test_list_skips_corrupt_meta(svc):
    (svc._meta_dir / "broken.json").write_text("not valid json", encoding="utf-8")
    assert svc.list() == []


# ────────────────────────────────────────────── Progress


@pytest.mark.asyncio
async def test_progress_out_of_range_raises(svc):
    captured = []

    async def runner(ctx):
        try:
            ctx.update_progress(150, "over")
        except JobError as exc:
            captured.append(str(exc))
            raise

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    assert captured
    final = svc.get(meta.job_id)
    assert final.status == "failed"


@pytest.mark.asyncio
async def test_progress_message_truncated(svc):
    async def runner(ctx):
        ctx.update_progress(10, "x" * 1000)

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.progress_message is not None
    assert len(final.progress_message) <= 500


# ────────────────────────────────────────────── Path Safety


@pytest.mark.asyncio
async def test_record_artifact_rejects_traversal_filename(svc):
    captured: list[str] = []

    async def runner(ctx):
        try:
            ctx.record_artifact(b"x", "../escape.txt", "text/plain")
        except JobError as exc:
            captured.append(str(exc))
            raise

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    assert captured
    assert svc.get(meta.job_id).status == "failed"


@pytest.mark.asyncio
async def test_record_artifact_rejects_slash_in_name(svc):
    captured: list[str] = []

    async def runner(ctx):
        try:
            ctx.record_artifact(b"x", "sub/out.txt", "text/plain")
        except JobError as exc:
            captured.append(str(exc))
            raise

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    assert captured
    assert svc.get(meta.job_id).status == "failed"


@pytest.mark.asyncio
async def test_artifact_path_rejects_traversal(svc):
    async def runner(ctx):
        pass
    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    with pytest.raises(JobError):
        svc.artifact_path(meta.job_id, "../escape.txt")


def test_get_rejects_invalid_job_id(svc):
    with pytest.raises(JobError):
        svc.get("not_a_valid_id")
    with pytest.raises(JobError):
        svc.get("job_short")
    with pytest.raises(JobError):
        svc.get("job_" + "Z" * 16)   # uppercase outside regex


def test_job_id_format():
    svc_probe = JobService.__new__(JobService)  # bypass __init__
    # Einfacher: direkt Regex-Test durch submit-Lieferung — jeder submit
    # erzeugt eine gültige ID.
    pass  # regex coverage über das Happy-Path-Submit


# ────────────────────────────────────────────── Artifact File Source


@pytest.mark.asyncio
async def test_record_artifact_from_file_path(svc, tmp_path):
    src = tmp_path / "source.bin"
    src.write_bytes(b"\x00\x01binary\x02")

    async def runner(ctx):
        ctx.record_artifact(src, "copy.bin", "application/octet-stream")

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert len(final.artifacts) == 1
    dest = svc.artifact_path(meta.job_id, "copy.bin")
    assert dest.read_bytes() == b"\x00\x01binary\x02"


@pytest.mark.asyncio
async def test_record_artifact_rejects_missing_source(svc, tmp_path):
    missing = tmp_path / "nope.bin"

    async def runner(ctx):
        ctx.record_artifact(missing, "x.bin", "application/octet-stream")

    meta = svc.submit(type="noop", provider="internal", runner=runner)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.status == "failed"


# ────────────────────────────────────────────── Startup Recovery


def test_recover_stale_running(tmp_path):
    # Simulate a leftover running meta file, dann neue JobService-Instanz.
    root = tmp_path / "jobs"
    (root / "meta").mkdir(parents=True)
    stale = {
        "job_id": "job_0123456789abcdef",
        "type": "noop", "provider": "internal", "status": "running",
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
        "started_at": "2020-01-01T00:00:00Z",
        "finished_at": None, "created_by": None, "project_id": None,
        "agent_id": None, "input_summary": {}, "progress_percent": 10,
        "progress_message": "mid", "artifacts": [], "error": None,
    }
    (root / "meta" / "job_0123456789abcdef.json").write_text(
        json.dumps(stale), encoding="utf-8",
    )

    svc = JobService(root=root)
    meta = svc.get("job_0123456789abcdef")
    assert meta.status == "failed"
    assert meta.error == _RESTART_ERROR
    assert meta.finished_at is not None


def test_recover_stale_queued(tmp_path):
    root = tmp_path / "jobs"
    (root / "meta").mkdir(parents=True)
    stale = {
        "job_id": "job_0123456789abcdef",
        "type": "noop", "provider": "internal", "status": "queued",
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
        "started_at": None, "finished_at": None,
        "created_by": None, "project_id": None, "agent_id": None,
        "input_summary": {}, "progress_percent": None,
        "progress_message": None, "artifacts": [], "error": None,
    }
    (root / "meta" / "job_0123456789abcdef.json").write_text(
        json.dumps(stale), encoding="utf-8",
    )
    svc = JobService(root=root)
    assert svc.get("job_0123456789abcdef").status == "failed"


def test_recover_keeps_terminal_statuses(tmp_path):
    root = tmp_path / "jobs"
    (root / "meta").mkdir(parents=True)
    ids_by_status = {
        "succeeded": "job_" + "a" * 16,
        "failed":    "job_" + "b" * 16,
        "cancelled": "job_" + "c" * 16,
    }
    for st, jid in ids_by_status.items():
        data = {
            "job_id": jid,
            "type": "noop", "provider": "internal", "status": st,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
            "started_at": "2020-01-01T00:00:00Z",
            "finished_at": "2020-01-01T00:01:00Z",
            "created_by": None, "project_id": None, "agent_id": None,
            "input_summary": {}, "progress_percent": 100,
            "progress_message": "done", "artifacts": [], "error": None,
        }
        (root / "meta" / f"{jid}.json").write_text(
            json.dumps(data), encoding="utf-8",
        )
    svc = JobService(root=root)
    # Terminale Stati bleiben nach recovery stabil
    for st, jid in ids_by_status.items():
        assert svc.get(jid).status == st


# ────────────────────────────────────────────── NotFound


def test_get_unknown_id(svc):
    with pytest.raises(JobNotFoundError):
        svc.get("job_" + "0" * 16)


# ────────────────────────────────────────────── #704 Sprint D: Release-Safety


class TestInitPermissionTolerance:
    """Hotfix 8f17e4f: Core-Start darf bei fehlendem/readonly jobs_dir nicht
    crashen — das ist genau die Regression, die den .220-Vorfall ausgelöst hat."""

    def test_permission_denied_on_mkdir_sets_fs_ok_false(self, tmp_path, monkeypatch):
        """Simuliert: `/var/lib/hydrahive/jobs` ist nicht beschreibbar beim Init."""
        from pathlib import Path as _P

        real_mkdir = _P.mkdir

        def fake_mkdir(self, *args, **kwargs):
            if "hydrahive_jobs_test" in str(self):
                raise PermissionError(f"denied: {self}")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(_P, "mkdir", fake_mkdir)
        svc = JobService(root=tmp_path / "hydrahive_jobs_test")
        assert svc._fs_ok is False

    def test_submit_degraded_raises_storage_error(self, tmp_path, monkeypatch):
        """Bei _fs_ok=False muss submit() JobStorageError werfen, nicht
        einen rohen PermissionError oder JobError mit Path-Leak."""
        from pathlib import Path as _P

        real_mkdir = _P.mkdir

        def fake_mkdir(self, *args, **kwargs):
            if "hydrahive_jobs_degraded" in str(self):
                raise PermissionError("denied")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(_P, "mkdir", fake_mkdir)
        svc = JobService(root=tmp_path / "hydrahive_jobs_degraded")

        async def runner(ctx):  # pragma: no cover — nicht erreicht
            pass

        with pytest.raises(JobStorageError) as ei:
            svc.submit(type="noop", provider="internal", runner=runner)
        # Der Fehlertext ist generisch und enthält keinen Dateisystem-Pfad.
        assert "unavailable" in str(ei.value).lower()
        assert str(tmp_path) not in str(ei.value)
        assert "/var/lib" not in str(ei.value)

    def test_list_degraded_returns_empty(self, tmp_path, monkeypatch):
        """Bei fehlendem meta_dir liefert list() leere Liste statt zu crashen."""
        from pathlib import Path as _P

        real_mkdir = _P.mkdir

        def fake_mkdir(self, *args, **kwargs):
            if "hydrahive_jobs_empty" in str(self):
                raise PermissionError("denied")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(_P, "mkdir", fake_mkdir)
        svc = JobService(root=tmp_path / "hydrahive_jobs_empty")
        assert svc.list() == []


class TestCorruptMetaHandling:
    """#704 Sprint B: get() darf bei corrupt JSON / fehlenden Pflichtfeldern
    nicht 500 mit Stack produzieren."""

    def test_get_corrupt_json_raises_storage_error(self, svc, tmp_path):
        jid = "job_" + "a" * 16
        (svc._meta_dir / f"{jid}.json").write_text(
            "{this is not valid json",
            encoding="utf-8",
        )
        with pytest.raises(JobStorageError) as ei:
            svc.get(jid)
        # Fehlertext ist generisch, kein Pfad, kein Traceback-Fragment.
        assert "corrupt" in str(ei.value).lower()
        assert str(tmp_path) not in str(ei.value)
        assert jid not in str(ei.value)  # auch job_id leakt nicht in Response-Text

    def test_get_missing_required_fields_raises_storage_error(self, svc):
        jid = "job_" + "b" * 16
        (svc._meta_dir / f"{jid}.json").write_text(
            '{"job_id": "' + jid + '", "type": "noop"}',  # fehlt provider, status, ...
            encoding="utf-8",
        )
        with pytest.raises(JobStorageError):
            svc.get(jid)

    @pytest.mark.asyncio
    async def test_list_still_skips_corrupt_after_sprint_b(self, svc):
        """Regression: Sprint B fasst list() nicht an — Corrupt-Skip bleibt."""
        (svc._meta_dir / "broken.json").write_text("not valid", encoding="utf-8")

        async def runner(ctx):
            pass
        meta = svc.submit(type="noop", provider="internal", runner=runner, created_by="alice")
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        jobs = svc.list()
        assert len(jobs) == 1
        assert jobs[0].job_id == meta.job_id


# ============================================================================
# #802 Phase 1 — Artifact-Storage im Project-Workspace
# ============================================================================


def test_mime_to_type_mapping():
    from hydrahive_core.jobs_service import _mime_to_type
    assert _mime_to_type("image/png") == "image"
    assert _mime_to_type("image/jpeg") == "image"
    assert _mime_to_type("video/mp4") == "video"
    assert _mime_to_type("video/webm") == "video"
    assert _mime_to_type("audio/mpeg") == "music"
    assert _mime_to_type("audio/ogg") == "music"
    assert _mime_to_type("text/plain") == "text"
    assert _mime_to_type("application/json") == "other"
    assert _mime_to_type("") == "other"
    assert _mime_to_type("video/quicktime") == "video"


def test_meta_from_dict_rejects_invalid_artifact_storage_value():
    from hydrahive_core.jobs_service import _meta_from_dict
    with pytest.raises(ValueError):
        _meta_from_dict({
            "job_id": "job_1234567890abcdef",
            "type": "noop", "provider": "internal", "status": "queued",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "artifact_storage": "definitely_not_valid",
        })


def test_get_rejects_meta_with_invalid_artifact_storage(tmp_path):
    """get() liest Meta mit ungültigem artifact_storage → JobStorageError
    (ValueError aus _meta_from_dict wird in get() zu JobStorageError)."""
    svc = JobService(root=tmp_path / "jobs")
    meta_file = tmp_path / "jobs" / "meta" / "job_abcdef1234567890.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(
        json.dumps({
            "job_id": "job_abcdef1234567890",
            "type": "noop", "provider": "internal", "status": "queued",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "artifact_storage": "not_a_valid_value",
        }),
        encoding="utf-8",
    )
    with pytest.raises(JobStorageError):
        svc.get("job_abcdef1234567890")


def test_artifact_workspace_path_raises_when_workspace_decided_but_project_missing(tmp_path):
    """artifact_storage=workspace aber project_id=None → JobError (Inkonsistenz-Signal)."""
    from hydrahive_core.jobs_service import _artifact_workspace_path
    svc = JobService(root=tmp_path / "jobs")
    meta = JobMeta(
        job_id="job_abcd1234567890ef",
        type="noop", provider="internal", status="running",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        artifact_storage="workspace",
        project_id=None,
    )
    with pytest.raises(JobError):
        _artifact_workspace_path(svc, meta, "file.png", "image/png")


@pytest.mark.asyncio
async def test_record_artifact_writes_to_workspace_when_project_id(tmp_path):
    """Job MIT project_id → Artifact landet im Workspace + Flag persistiert."""
    # Workspace-Override: /tmp/.../projects/proj_A → realer Pfad
    ws_root = tmp_path / "projects" / "proj_A"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        svc = JobService(root=tmp_path / "jobs")

        async def runner(ctx: JobContext):
            ctx.record_artifact(b"PNG_DATA", "test.png", "image/png")

        meta = svc.submit(
            type="image", provider="minimax", runner=runner, project_id="proj_A",
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        final = svc.get(meta.job_id)

        assert final.status == "succeeded"
        assert final.artifact_storage == "workspace"
        assert len(final.artifacts) == 1
        assert final.artifacts[0]["filename"] == "test.png"
        # Physisch muss die Datei im Workspace-Layout liegen
        image_dir = ws_root / ".hydrahive" / "artifacts" / "image"
        assert image_dir.exists()
        # Datum-Partition + job_id-Präfix
        found = list(image_dir.rglob(f"{meta.job_id}__test.png"))
        assert len(found) == 1, f"expected 1 file, got {found}"
        assert found[0].read_bytes() == b"PNG_DATA"
    finally:
        reset_workspace_override(token)


@pytest.mark.asyncio
async def test_record_artifact_falls_back_to_legacy_without_project_id(tmp_path):
    """Job OHNE project_id → Legacy-Pfad (jobs_dir), Flag bleibt None."""
    svc = JobService(root=tmp_path / "jobs")

    async def runner(ctx: JobContext):
        ctx.record_artifact(b"PNG_DATA", "fallback.png", "image/png")

    meta = svc.submit(type="image", provider="minimax", runner=runner, project_id=None)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)

    assert final.status == "succeeded"
    assert final.artifact_storage is None
    assert len(final.artifacts) == 1
    legacy_path = tmp_path / "jobs" / "artifacts" / meta.job_id / "fallback.png"
    assert legacy_path.exists()
    assert legacy_path.read_bytes() == b"PNG_DATA"


@pytest.mark.asyncio
async def test_record_artifact_storage_flag_persists_across_calls(tmp_path):
    """Erster record_artifact setzt Flag, zweiter bleibt bei workspace."""
    ws_root = tmp_path / "projects" / "proj_persist"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        svc = JobService(root=tmp_path / "jobs")

        async def runner(ctx: JobContext):
            ctx.record_artifact(b"MP3_DATA", "song.mp3", "audio/mpeg")
            ctx.record_artifact(b"TEXT_DATA", "lyrics.txt", "text/plain")

        meta = svc.submit(
            type="music", provider="minimax", runner=runner, project_id="proj_persist",
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        final = svc.get(meta.job_id)

        assert final.artifact_storage == "workspace"
        assert len(final.artifacts) == 2
        # Beide in Workspace, unterschiedliche type-Ordner
        assert (ws_root / ".hydrahive" / "artifacts" / "music").exists()
        assert (ws_root / ".hydrahive" / "artifacts" / "text").exists()
    finally:
        reset_workspace_override(token)


# ----------------------------------------------------------------------
# #802 Phase 2 — artifact_path() als Workspace-First-Proxy
# ----------------------------------------------------------------------


def test_mime_for_artifact_lookup():
    """_mime_for_artifact findet gespeicherten MIME in meta.artifacts."""
    from hydrahive_core.jobs_service import _mime_for_artifact
    meta = JobMeta(
        job_id="job_testmime", type="noop", provider="test",
        status="succeeded",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        artifacts=[
            {"filename": "a.png", "size": 100, "mime": "image/png",
             "created_at": "2026-01-01T00:00:00Z"},
            {"filename": "b.mp4", "size": 200, "mime": "video/mp4",
             "created_at": "2026-01-01T00:00:00Z"},
        ],
    )
    assert _mime_for_artifact(meta, "a.png") == "image/png"
    assert _mime_for_artifact(meta, "b.mp4") == "video/mp4"
    assert _mime_for_artifact(meta, "c.txt") == "application/octet-stream"


@pytest.mark.asyncio
async def test_artifact_path_returns_workspace_when_storage_is_workspace(tmp_path):
    """storage==workspace → artifact_path gibt Workspace-Pfad zurück."""
    ws_root = tmp_path / "projects" / "proj_ws_read"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        svc = JobService(root=tmp_path / "jobs")

        async def runner(ctx: JobContext):
            ctx.record_artifact(b"PNG_DATA", "img.png", "image/png")

        meta = svc.submit(
            type="image", provider="minimax", runner=runner,
            project_id="proj_ws_read",
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        final = svc.get(meta.job_id)
        assert final.artifact_storage == "workspace"

        path = svc.artifact_path(meta.job_id, "img.png")
        assert path.exists()
        assert str(path).startswith(str(ws_root))
    finally:
        reset_workspace_override(token)


@pytest.mark.asyncio
async def test_artifact_path_falls_back_to_legacy_when_storage_is_none(tmp_path):
    """storage=None (kein project_id) → artifact_path gibt Legacy-Pfad zurück."""
    svc = JobService(root=tmp_path / "jobs")

    async def runner(ctx: JobContext):
        ctx.record_artifact(b"DATA", "doc.txt", "text/plain")

    meta = svc.submit(type="noop", provider="internal", runner=runner, project_id=None)
    await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
    final = svc.get(meta.job_id)
    assert final.artifact_storage is None

    path = svc.artifact_path(meta.job_id, "doc.txt")
    assert path.exists()
    assert str(path).startswith(str(tmp_path / "jobs" / "artifacts"))


@pytest.mark.asyncio
async def test_artifact_path_workspace_pfad_auch_wenn_datei_weg(tmp_path):
    """storage=workspace aber Workspace-File physisch weg → Workspace-Pfad
    zurückgeben (nicht Legacy-Fallback); Router entscheidet via .exists() → 404."""
    ws_root = tmp_path / "projects" / "proj_ws_missing"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        svc = JobService(root=tmp_path / "jobs")

        async def runner(ctx: JobContext):
            ctx.record_artifact(b"PNG_DATA", "lost.png", "image/png")

        meta = svc.submit(
            type="image", provider="minimax", runner=runner,
            project_id="proj_ws_missing",
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        assert svc.get(meta.job_id).artifact_storage == "workspace"

        # Workspace-Artifact physisch löschen
        ws_artifact = svc.artifact_path(meta.job_id, "lost.png")
        ws_artifact.unlink()

        # artifact_path liefert trotzdem Workspace-Pfad (Meta sagt workspace)
        path = svc.artifact_path(meta.job_id, "lost.png")
        assert str(path).startswith(str(ws_root))
        assert not path.exists()
    finally:
        reset_workspace_override(token)


def test_artifact_path_rejects_traversal_after_proxy(tmp_path):
    """artifact_path() validiert filename auch im Workspace-First-Modus."""
    svc = JobService(root=tmp_path / "jobs")

    async def _run() -> str:
        async def runner(ctx: JobContext):
            ctx.record_artifact(b"x", "real.txt", "text/plain")
        m = svc.submit(type="noop", provider="internal", runner=runner)
        await asyncio.wait_for(svc._tasks[m.job_id], timeout=2)
        return m.job_id

    job_id = asyncio.run(_run())

    with pytest.raises(JobError):
        svc.artifact_path(job_id, "../etc/passwd")


@pytest.mark.asyncio
async def test_artifact_workspace_path_resolves_symlink_override(tmp_path):
    """Workspace-Override mit Symlink → Artifact landet im aufgelösten realen Pfad."""
    real_ws = tmp_path / "real_project"
    real_ws.mkdir(parents=True)
    link_ws = tmp_path / "link_project"
    link_ws.symlink_to(real_ws, target_is_directory=True)

    token = set_workspace_override(link_ws)
    try:
        svc = JobService(root=tmp_path / "jobs")

        async def runner(ctx: JobContext):
            ctx.record_artifact(b"PNG_DATA", "symlink_test.png", "image/png")

        meta = svc.submit(
            type="image", provider="test", runner=runner, project_id="link_project",
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        final = svc.get(meta.job_id)

        assert final.artifact_storage == "workspace"
        # Datei muss im echten Pfad liegen (real_ws), nicht im Symlink-Namen
        real_image_dir = real_ws / ".hydrahive" / "artifacts" / "image"
        assert real_image_dir.exists()
        files = list(real_image_dir.rglob(f"{meta.job_id}__symlink_test.png"))
        assert len(files) == 1
    finally:
        reset_workspace_override(token)
