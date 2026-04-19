"""jobs_service.py — Async Long-Running-Job-Fundament (#687).

Generischer Service für lange Backend-Jobs (später: MiniMax Image/Video/Music
über #679/#688/#689). Keine Provider-Logik, kein Queue-Broker — in-Process
``asyncio``-Tasks mit File-basierter Persistenz.

Layout unter ``settings.jobs_dir`` (default ``/var/lib/hydrahive/jobs``):

  meta/<job_id>.json         JobMeta als JSON
  artifacts/<job_id>/        Provider-Result-Dateien (png, mp4, mp3, ...)

Lifecycle:

  queued → running → (succeeded | failed | cancelled)

Beim Start normalisiert der Service alle Meta-Files im Status ``queued`` oder
``running`` zu ``failed`` mit ``error="core restart before completion"`` —
wir bieten in Phase 1 bewusst keine Task-Resumption (das wäre provider-
spezifisch und gehört zu jeder Integration einzeln).

Runner-Contract:

    async def runner(ctx: JobContext) -> None:
        ctx.update_progress(50, "Halbzeit")
        ctx.check_cancelled()          # raises JobCancelled
        ctx.record_artifact(data, "out.png", "image/png")

Runner sind dafür verantwortlich, ``check_cancelled()`` periodisch zu rufen;
blockierende Aufrufe hängen sonst beim Cancel. Nicht über-engineeren mit
Thread-Interruption.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from .settings import settings

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────── Konstanten + Validation

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

_TERMINAL_STATUSES: tuple[JobStatus, ...] = ("succeeded", "failed", "cancelled")

_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{16}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_RESTART_ERROR = "core restart before completion"


class JobError(Exception):
    """Client-seitiger / Validation-Fehler (ungültige ID, ungültiger Name,
    Traversal-Versuch). Router rendert als 400."""


class JobStorageError(Exception):
    """Server-seitiger Storage-Fehler (Pfad nicht beschreibbar, Meta corrupt).
    Router rendert als 503 mit generischer Message — kein internal path im
    Response-Body. Diagnostische Details landen im Logger."""


class JobCancelled(Exception):
    """Runner wurde via ``ctx.check_cancelled()`` abgebrochen."""


class JobNotFoundError(JobError):
    """Job existiert nicht."""


# ────────────────────────────────────────────── Datenmodell


@dataclass
class JobMeta:
    job_id: str
    type: str
    provider: str
    status: JobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    created_by: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    input_summary: dict = field(default_factory=dict)
    progress_percent: int | None = None
    progress_message: str | None = None
    artifacts: list[dict] = field(default_factory=list)
    error: str | None = None


# ────────────────────────────────────────────── Helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_job_id() -> str:
    return "job_" + secrets.token_hex(8)


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise JobError(f"invalid job_id: {job_id!r}")
    return job_id


def _validate_artifact_name(name: str) -> str:
    if not isinstance(name, str) or not _ARTIFACT_NAME_RE.fullmatch(name):
        raise JobError(f"invalid artifact name: {name!r}")
    return name


def _assert_within(root: Path, candidate: Path) -> Path:
    """Verhindert Path-Traversal. ``candidate`` muss ein Kind von ``root`` sein."""
    try:
        resolved = candidate.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
    except Exception as exc:
        raise JobError(f"path resolution failed: {exc}") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise JobError(f"path escapes jobs root: {candidate}") from exc
    return resolved


def _meta_to_dict(meta: JobMeta) -> dict:
    return asdict(meta)


def _meta_from_dict(data: dict) -> JobMeta:
    allowed = {
        "job_id", "type", "provider", "status", "created_at", "updated_at",
        "started_at", "finished_at", "created_by", "project_id", "agent_id",
        "input_summary", "progress_percent", "progress_message",
        "artifacts", "error",
    }
    kwargs = {k: v for k, v in data.items() if k in allowed}
    return JobMeta(**kwargs)


# ────────────────────────────────────────────── JobContext (Runner-API)


class JobContext:
    """Runtime-API, die einem Runner übergeben wird.

    Bewusst schmal: Runner darf Progress melden, Cancel checken und Artefakte
    ablegen. Meta-Felder wie ``status``/``finished_at`` setzt ausschließlich
    der Service.
    """

    def __init__(self, service: "JobService", job_id: str):
        self._service = service
        self._job_id = job_id

    @property
    def job_id(self) -> str:
        return self._job_id

    def check_cancelled(self) -> None:
        """Wirft :class:`JobCancelled` wenn für diesen Job ein Cancel
        registriert wurde. Runner müssen das periodisch aufrufen."""
        if self._service._is_cancelled(self._job_id):
            raise JobCancelled(self._job_id)

    def update_progress(self, percent: int | None, message: str | None = None) -> None:
        self._service._update_progress(self._job_id, percent, message)

    def record_artifact(
        self,
        data: bytes | Path | str,
        filename: str,
        mime: str = "application/octet-stream",
    ) -> dict:
        return self._service._record_artifact(self._job_id, data, filename, mime)


# ────────────────────────────────────────────── JobService


RunnerFn = Callable[[JobContext], Awaitable[None]]


class JobService:
    """Persistenter In-Process-Job-Service (#687 Phase 1)."""

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root) if root is not None else settings.jobs_dir
        self._meta_dir = self._root / "meta"
        self._artifacts_dir = self._root / "artifacts"
        # Permission-tolerant Init — auf Servern legt der Installer den Pfad
        # an. Ist er nicht beschreibbar, läuft Core trotzdem hoch, und
        # spätere submit()-Calls scheitern mit einer klaren Meldung.
        self._fs_ok = True
        for d in (self._meta_dir, self._artifacts_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as exc:
                self._fs_ok = False
                logger.warning(
                    "jobs: cannot prepare %s (%s). submit/list disabled until "
                    "the installer or an admin creates %s with write access "
                    "for the core service user.",
                    d, exc, self._root,
                )
        # job_id → asyncio.Task (nur running)
        self._tasks: dict[str, asyncio.Task] = {}
        # job_id → True wenn Cancel angefordert
        self._cancelled: set[str] = set()
        if self._fs_ok:
            self._recover_stale()

    # ── Paths ────────────────────────────────────────────────────────────

    def _meta_path(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        path = self._meta_dir / f"{job_id}.json"
        return _assert_within(self._meta_dir, path)

    def _artifact_dir_for(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        path = self._artifacts_dir / job_id
        return _assert_within(self._artifacts_dir, path)

    def _artifact_path(self, job_id: str, filename: str) -> Path:
        _validate_artifact_name(filename)
        base = self._artifact_dir_for(job_id)
        return _assert_within(base, base / filename)

    # ── Read ─────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> JobMeta:
        path = self._meta_path(job_id)
        if not path.exists():
            raise JobNotFoundError(job_id)
        # #704 Sprint B: corrupt JSON oder fehlende Pflichtfelder →
        # JobStorageError ohne Pfad/Traceback im Response, aber mit job_id
        # im Logger für Operator-Diagnose.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _meta_from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError) as exc:
            logger.warning("jobs: meta corrupt for %s: %s", job_id, exc)
            raise JobStorageError("job metadata corrupt") from None

    def list(
        self,
        *,
        created_by: str | None = None,
        status: str | None = None,
        type: str | None = None,
        project_id: str | None = None,
    ) -> list[JobMeta]:
        out: list[JobMeta] = []
        if not self._meta_dir.exists():
            return out
        for meta_file in sorted(self._meta_dir.glob("*.json")):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                meta = _meta_from_dict(data)
            except Exception as exc:
                logger.warning("jobs: skipping corrupt meta %s: %s", meta_file.name, exc)
                continue
            if created_by is not None and meta.created_by != created_by:
                continue
            if status is not None and meta.status != status:
                continue
            if type is not None and meta.type != type:
                continue
            if project_id is not None and meta.project_id != project_id:
                continue
            out.append(meta)
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    def artifact_path(self, job_id: str, filename: str) -> Path:
        """Resolved + validated Dateipfad für Download-Route. Wirft JobError
        bei Traversal/ungültigem Namen; Caller prüft ``.exists()``."""
        return self._artifact_path(job_id, filename)

    # ── Write ────────────────────────────────────────────────────────────

    def _write_meta(self, meta: JobMeta) -> None:
        meta.updated_at = _now_iso()
        path = self._meta_path(meta.job_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(_meta_to_dict(meta), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def submit(
        self,
        *,
        type: str,
        provider: str,
        runner: RunnerFn,
        input_summary: dict | None = None,
        created_by: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> JobMeta:
        """Legt eine neue Job-Meta an und startet den Runner als asyncio-Task.

        ``input_summary`` darf **keine Secrets oder großen Prompts** enthalten.
        Caller ist für Sanitization verantwortlich.
        """
        if not self._fs_ok:
            # #704 Sprint C: JobStorageError → Router 503 ohne Path-Leak.
            # Der Pfad steht im Init-Log-Warning und ist für Operator sichtbar.
            logger.warning("jobs: submit denied — storage not writable at %s", self._root)
            raise JobStorageError("jobs storage unavailable")
        now = _now_iso()
        meta = JobMeta(
            job_id=_new_job_id(),
            type=str(type),
            provider=str(provider),
            status="queued",
            created_at=now,
            updated_at=now,
            created_by=created_by,
            project_id=project_id,
            agent_id=agent_id,
            input_summary=dict(input_summary or {}),
        )
        self._write_meta(meta)
        # Artifact-Dir lazy anlegen (erster record_artifact), damit leere
        # Jobs keinen leeren Ordner hinterlassen.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            task = loop.create_task(
                self._run(meta.job_id, runner),
                name=f"job-{meta.job_id}",
            )
        else:
            task = loop.create_task(
                self._run(meta.job_id, runner),
                name=f"job-{meta.job_id}",
            )
        self._tasks[meta.job_id] = task
        return meta

    def cancel(self, job_id: str) -> JobMeta:
        meta = self.get(job_id)
        if meta.status in _TERMINAL_STATUSES:
            return meta
        self._cancelled.add(job_id)
        return meta

    # ── Internal: Runner Execution ──────────────────────────────────────

    async def _run(self, job_id: str, runner: RunnerFn) -> None:
        try:
            meta = self.get(job_id)
        except JobNotFoundError:
            logger.error("jobs: _run started but meta missing for %s", job_id)
            return

        meta.status = "running"
        meta.started_at = _now_iso()
        self._write_meta(meta)

        ctx = JobContext(self, job_id)
        try:
            # Frühzeitig Cancel-Check — falls vor Start schon gecancelled.
            ctx.check_cancelled()
            await runner(ctx)
            self._finalize(job_id, status="succeeded")
        except JobCancelled:
            self._finalize(job_id, status="cancelled")
        except Exception as exc:
            # User-facing: Kein Traceback im Meta-Error-Feld. Logger hat den
            # vollen Stack für Operator.
            logger.exception("jobs: runner failed for %s", job_id)
            self._finalize(
                job_id, status="failed",
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        finally:
            self._tasks.pop(job_id, None)
            self._cancelled.discard(job_id)

    def _finalize(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error: str | None = None,
    ) -> None:
        try:
            meta = self.get(job_id)
        except JobNotFoundError:
            return
        meta.status = status
        meta.finished_at = _now_iso()
        if error is not None:
            meta.error = error
        self._write_meta(meta)

    # ── Internal: Runner-API (JobContext delegiert) ─────────────────────

    def _is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    def _update_progress(
        self,
        job_id: str,
        percent: int | None,
        message: str | None,
    ) -> None:
        meta = self.get(job_id)
        if percent is not None:
            if not isinstance(percent, int) or not (0 <= percent <= 100):
                raise JobError(f"progress_percent out of range: {percent!r}")
            meta.progress_percent = percent
        if message is not None:
            meta.progress_message = str(message)[:500]
        self._write_meta(meta)

    def _record_artifact(
        self,
        job_id: str,
        data: bytes | Path | str,
        filename: str,
        mime: str,
    ) -> dict:
        dest = self._artifact_path(job_id, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, (bytes, bytearray)):
            dest.write_bytes(bytes(data))
        else:
            # Path/str — als Dateipfad behandeln (Copy, kein Move, damit Source
            # bei Bedarf anderswo bleibt)
            src = Path(data)
            if not src.exists() or not src.is_file():
                raise JobError(f"artifact source not a regular file: {src}")
            shutil.copyfile(src, dest)

        size = dest.stat().st_size
        entry = {
            "filename": filename,
            "size": size,
            "mime": str(mime or "application/octet-stream"),
            "created_at": _now_iso(),
        }
        meta = self.get(job_id)
        meta.artifacts = list(meta.artifacts) + [entry]
        self._write_meta(meta)
        return entry

    # ── Startup Recovery ────────────────────────────────────────────────

    def _recover_stale(self) -> None:
        """Alle Meta-Files mit Status ``queued`` oder ``running`` in
        ``failed`` umschreiben — Core-Restart unterbricht in-Process-Tasks."""
        for meta_file in self._meta_dir.glob("*.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("jobs: recover skipping %s (parse): %s", meta_file.name, exc)
                continue
            if data.get("status") not in ("queued", "running"):
                continue
            data["status"] = "failed"
            data["error"] = _RESTART_ERROR
            now = _now_iso()
            if not data.get("finished_at"):
                data["finished_at"] = now
            data["updated_at"] = now
            try:
                meta_file.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("jobs: recover write failed for %s: %s", meta_file.name, exc)


# ────────────────────────────────────────────── Builtin Noop Runner (#687 Smoke)

async def _noop_runner(ctx: JobContext) -> None:
    """Minimaler Runner für Fundament-Tests und Smoke-Check. Nicht als
    öffentliches Feature beworben; wird nicht im Frontend angezeigt."""
    ctx.update_progress(10, "noop: starting")
    await asyncio.sleep(0)
    ctx.check_cancelled()
    ctx.update_progress(50, "noop: halfway")
    ctx.record_artifact(b"noop artifact\n", "noop.txt", "text/plain")
    ctx.update_progress(100, "noop: done")
