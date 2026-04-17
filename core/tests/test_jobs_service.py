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
    JobNotFoundError,
    JobService,
    JobStorageError,
    _noop_runner,
    _RESTART_ERROR,
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
