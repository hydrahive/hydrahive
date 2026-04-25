"""
disk_import_manager.py — VDI/VMDK/VHD Import Pipeline (#908)
Lädt Disk-Images hoch, konvertiert zu QCOW2 via qemu-img.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".vdi", ".vmdk", ".vhd", ".vhdx", ".raw", ".img", ".qcow2"}
_PROGRESS_RE = re.compile(r'\((\d+(?:\.\d+)?)/100%\)')


@dataclass
class DiskImportJob:
    job_id: str
    filename: str
    status: str = "uploading"   # uploading | converting | done | error
    progress_pct: int = 0
    error: str | None = None
    output_path: str | None = None
    created_at: float = field(default_factory=time.time)
    size_bytes: int = 0


class DiskImportManager:
    def __init__(self, import_dir: Path, max_size_gb: int = 500):
        self._import_dir = import_dir.resolve()
        self._max_size_bytes = max_size_gb * 1024 ** 3
        self._jobs: dict[str, DiskImportJob] = {}

    def _safe_stem(self, filename: str) -> str:
        stem = Path(filename).stem
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", stem)[:64] or "disk"
        return safe

    async def start_import(self, filename: str, chunks: AsyncIterator[bytes]) -> DiskImportJob:
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Nicht unterstütztes Format '{ext}'. Erlaubt: {', '.join(SUPPORTED_EXTENSIONS)}")

        job_id = uuid.uuid4().hex
        tmp_path = self._import_dir / f"{job_id}.tmp"
        job = DiskImportJob(job_id=job_id, filename=filename)
        self._jobs[job_id] = job

        # Upload streamen
        written = 0
        try:
            with tmp_path.open("wb") as fh:
                async for chunk in chunks:
                    if written + len(chunk) > self._max_size_bytes:
                        fh.flush()
                        raise ValueError(f"Disk-Image überschreitet Limit von {self._max_size_bytes // (1024**3)} GB")
                    fh.write(chunk)
                    written += len(chunk)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            self._jobs.pop(job_id, None)
            raise

        job.size_bytes = written
        job.status = "converting"
        asyncio.create_task(self._convert(job_id, tmp_path, ext))
        return job

    async def _convert(self, job_id: str, tmp_path: Path, src_ext: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        out_path = self._import_dir / f"{job_id}.qcow2"
        fmt = src_ext.lstrip(".")
        if fmt in ("vhdx",):
            fmt = "vhdx"
        elif fmt in ("vhd",):
            fmt = "vpc"
        elif fmt in ("img", "raw"):
            fmt = "raw"

        cmd = ["qemu-img", "convert", "-p", "-f", fmt, "-O", "qcow2", str(tmp_path), str(out_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # qemu-img -p schreibt Progress mit \r, nicht \n — daher Chunks lesen
            stderr_buf = []
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(256)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                stderr_buf.append(text)
                m = _PROGRESS_RE.search(text)
                if m and job_id in self._jobs:
                    self._jobs[job_id].progress_pct = min(99, int(float(m.group(1))))

            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("".join(stderr_buf).strip()[-300:])

            job.output_path = str(out_path)
            job.progress_pct = 100
            job.status = "done"
        except FileNotFoundError:
            job.status = "error"
            job.error = "qemu-img nicht gefunden — bitte QEMU installieren"
        except Exception as e:
            job.status = "error"
            job.error = str(e)[:500]
            if out_path.exists():
                out_path.unlink()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        logger.info("Import %s: %s", job_id, job.status)

    def get_job(self, job_id: str) -> DiskImportJob | None:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job and job.output_path:
            p = Path(job.output_path)
            if p.exists():
                p.unlink()

    async def cleanup_old(self, max_age_hours: int = 24) -> None:
        cutoff = time.time() - max_age_hours * 3600
        for jid, job in list(self._jobs.items()):
            if job.created_at < cutoff:
                self.cancel_job(jid)
        # Verwaiste Dateien
        for f in self._import_dir.glob("*.tmp"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
        for f in self._import_dir.glob("*.qcow2"):
            if f.stat().st_mtime < cutoff and f.stem not in self._jobs:
                f.unlink()
