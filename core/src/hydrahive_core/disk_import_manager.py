"""
disk_import_manager.py — VDI/VMDK/VHD/VMA Import Pipeline (#908)
Lädt Disk-Images hoch, konvertiert zu QCOW2 via qemu-img.
VMA (.vma, .vma.gz, .vma.zst) — Proxmox Backup Format — wird via `vma extract` (Proxmox-Tool) extrahiert.
Requires: apt install pve-qemu-kvm (Proxmox repo) für das `vma`-Binary.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".vdi", ".vmdk", ".vhd", ".vhdx", ".raw", ".img", ".qcow2",
                        ".vma", ".gz", ".zst"}
# .gz and .zst are only valid when the stem ends in .vma (e.g. backup.vma.zst)
_PROGRESS_RE = re.compile(r'\((\d+(?:\.\d+)?)/100%\)')


def _is_vma_filename(filename: str) -> bool:
    """True for backup.vma, backup.vma.gz, backup.vma.zst"""
    name = filename.lower()
    return name.endswith(".vma") or name.endswith(".vma.gz") or name.endswith(".vma.zst")


def _vma_compression(filename: str) -> str | None:
    """Returns 'zst', 'gz', or None."""
    name = filename.lower()
    if name.endswith(".zst"):
        return "zst"
    if name.endswith(".gz"):
        return "gz"
    return None




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

    async def start_import_from_path(self, server_path: str) -> DiskImportJob:
        """Import a disk image that's already on the server filesystem (no upload needed)."""
        src = Path(server_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {server_path}")
        if not src.is_file():
            raise ValueError(f"Kein reguläres File: {server_path}")

        filename = src.name
        if _is_vma_filename(filename):
            ext = ".vma"
        else:
            ext = src.suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                raise ValueError(f"Nicht unterstütztes Format '{ext}'")

        job_id = uuid.uuid4().hex
        job = DiskImportJob(job_id=job_id, filename=filename, size_bytes=src.stat().st_size)
        self._jobs[job_id] = job
        job.status = "converting"

        if ext == ".vma":
            # stable_dec_key: same source file → same decompressed cache → skip re-decompression
            import hashlib as _hl
            stable_dec_key = _hl.sha1(str(src).encode()).hexdigest()[:16]
            asyncio.create_task(self._extract_vma(job_id, src, _vma_compression(filename),
                                                   keep_source=True, stable_dec_key=stable_dec_key))
        else:
            asyncio.create_task(self._convert_from_path(job_id, src, ext))
        return job

    async def _convert_from_path(self, job_id: str, src: Path, src_ext: str) -> None:
        """qemu-img convert for server-side file (source not deleted)."""
        job = self._jobs.get(job_id)
        if not job:
            return
        out_path = self._import_dir / f"{job_id}.qcow2"
        fmt = src_ext.lstrip(".")
        if fmt == "vhd":
            fmt = "vpc"
        elif fmt in ("img", "raw"):
            fmt = "raw"
        cmd = ["qemu-img", "convert", "-p", "-f", fmt, "-O", "qcow2", str(src), str(out_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stderr_buf: list[str] = []
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(256)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                stderr_buf.append(text)
                m = _PROGRESS_RE.search(text)
                if m:
                    self._set_progress(job_id, min(99, int(float(m.group(1)))))
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("".join(stderr_buf).strip()[-300:])
            job.output_path = str(out_path)
            job.progress_pct = 100
            job.status = "done"
        except Exception as e:
            job.status = "error"
            job.error = str(e)[:500]
            if out_path.exists():
                out_path.unlink()
        logger.info("Import-from-path %s: %s", job_id, job.status)

    async def start_import(self, filename: str, chunks: AsyncIterator[bytes]) -> DiskImportJob:
        if _is_vma_filename(filename):
            ext = ".vma"
        else:
            ext = Path(filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                raise ValueError(f"Nicht unterstütztes Format '{ext}'. Erlaubt: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

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
        if ext == ".vma":
            asyncio.create_task(self._extract_vma(job_id, tmp_path, _vma_compression(filename)))
        else:
            asyncio.create_task(self._convert(job_id, tmp_path, ext))
        return job

    async def _extract_vma(self, job_id: str, tmp_path: Path, compression: str | None,
                           keep_source: bool = False, stable_dec_key: str | None = None) -> None:
        """Proxmox VMA backup extractor — decompresses if needed, then calls `vma extract`."""
        job = self._jobs.get(job_id)
        if not job:
            return
        import os as _os
        out_path = self._import_dir / f"{job_id}.qcow2"
        extract_dir = self._import_dir / f"{job_id}_vma"
        dec_name = f"_stable_{stable_dec_key}_dec.vma" if stable_dec_key else f"{job_id}_dec.vma"
        dec_path = self._import_dir / dec_name
        vma_path = tmp_path

        try:
            # --- Step 1: decompress if needed ---
            if compression and dec_path.exists() and dec_path.stat().st_size > 0:
                logger.info("VMA %s: dekomprimierte Datei vorhanden — überspringe Dekomprimierung", job_id)
                vma_path = dec_path
                job.progress_pct = 10
            elif compression:
                job.progress_pct = 2
                _CPUS = str(_os.cpu_count() or 4)
                compressed_size = tmp_path.stat().st_size
                estimated_dec_size = max(compressed_size * 4, 1)

                if compression == "zst":
                    dec_cmd = ["zstd", "--decompress", "--force", f"-T{_CPUS}",
                               str(tmp_path), "-o", str(dec_path)]
                    use_stdout = False
                elif shutil.which("pigz"):
                    dec_cmd = ["pigz", "--decompress", "--keep", "--force",
                               f"-p{_CPUS}", "--stdout", str(tmp_path)]
                    use_stdout = True
                else:
                    dec_cmd = ["gunzip", "--force", "--keep", "--stdout", str(tmp_path)]
                    use_stdout = True

                if use_stdout:
                    dec_fh = open(dec_path, "wb")
                    dec_proc = await asyncio.create_subprocess_exec(
                        *dec_cmd, stdout=dec_fh.fileno(), stderr=asyncio.subprocess.PIPE)
                else:
                    dec_fh = None
                    dec_proc = await asyncio.create_subprocess_exec(
                        *dec_cmd, stderr=asyncio.subprocess.PIPE)

                while dec_proc.returncode is None:
                    await asyncio.sleep(2)
                    if dec_path.exists():
                        pct = min(65, int(dec_path.stat().st_size / estimated_dec_size * 65))
                        self._set_progress(job_id, 2 + pct)
                    try:
                        await asyncio.wait_for(dec_proc.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass

                if dec_fh:
                    dec_fh.close()
                if dec_proc.returncode != 0:
                    stderr_out = await dec_proc.stderr.read() if dec_proc.stderr else b""
                    raise RuntimeError(
                        f"Dekomprimierung fehlgeschlagen (rc={dec_proc.returncode}): "
                        f"{stderr_out.decode(errors='replace')[-300:]}")

                vma_path = dec_path
                job.progress_pct = 68

            # --- Step 2: vma extract ---
            extract_dir.mkdir(exist_ok=True)
            job.progress_pct = max(job.progress_pct, 10)
            logger.info("VMA %s: starte vma extract %s → %s", job_id, vma_path.name, extract_dir)

            vma_proc = await asyncio.create_subprocess_exec(
                "vma", "extract", str(vma_path), str(extract_dir),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, vma_stderr = await vma_proc.communicate()
            if vma_proc.returncode != 0:
                err = vma_stderr.decode(errors="replace").strip()
                raise RuntimeError(f"vma extract fehlgeschlagen (rc={vma_proc.returncode}): {err[-400:]}")

            # vma extract creates files named "disk-<devname>" in extract_dir
            disk_files = sorted(extract_dir.glob("disk-*"))
            if not disk_files:
                contents = [p.name for p in extract_dir.iterdir()]
                raise ValueError(f"vma extract hat keine disk-* Dateien erstellt. Inhalt: {contents}")

            raw_file = disk_files[0]
            devname = raw_file.name[len("disk-"):]
            total_size = raw_file.stat().st_size
            logger.info("VMA extract: dev=%s size=%d (%.2f GiB)", devname, total_size,
                        total_size / 1073741824)
            job.progress_pct = 80

            # --- Step 3: convert raw → qcow2 ---
            proc2 = await asyncio.create_subprocess_exec(
                "qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2",
                str(raw_file), str(out_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stderr_buf: list[str] = []
            assert proc2.stderr is not None
            while True:
                chunk = await proc2.stderr.read(256)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                stderr_buf.append(text)
                m = _PROGRESS_RE.search(text)
                if m and job_id in self._jobs:
                    self._set_progress(job_id, 80 + int(float(m.group(1)) * 0.19))
            await proc2.wait()
            if proc2.returncode != 0:
                raise RuntimeError("qemu-img: " + "".join(stderr_buf).strip()[-300:])

            job.output_path = str(out_path)
            job.progress_pct = 100
            job.status = "done"

        except FileNotFoundError as e:
            job.status = "error"
            fname = getattr(e, "filename", str(e))
            if "vma" in str(fname):
                job.error = ("'vma'-Tool nicht gefunden. Installation: "
                             "Proxmox-Repo einrichten + apt install pve-qemu-kvm")
            else:
                job.error = f"Tool nicht gefunden: {fname}"
        except Exception as e:
            job.status = "error"
            job.error = str(e)[:500]
            if out_path.exists():
                out_path.unlink()
        finally:
            if not keep_source and tmp_path.exists():
                tmp_path.unlink()
            shutil.rmtree(str(extract_dir), ignore_errors=True)
            # dec-Datei nur bei Erfolg löschen (bei Fehler für Retry aufbewahren)
            if dec_path.exists() and job.status == "done":
                dec_path.unlink()
        logger.info("VMA import %s: %s", job_id, job.status)

    def _set_progress(self, job_id: str, pct: int) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].progress_pct = min(99, pct)

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
