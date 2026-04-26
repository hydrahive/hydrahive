"""
disk_import_manager.py — VDI/VMDK/VHD/VMA Import Pipeline (#908)
Lädt Disk-Images hoch, konvertiert zu QCOW2 via qemu-img.
VMA (.vma, .vma.gz, .vma.zst) — Proxmox Backup Format — wird via Python extrahiert.
"""
from __future__ import annotations

import asyncio
import logging
import re
import struct
import subprocess
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

# VMA format constants
_VMA_MAGIC = b"VMA"          # 3-byte prefix; byte 3 is version (0x01 standard, 0x00 seen in some builds)
_VMA_CLUSTER_SIZE = 65536   # 64 KB per cluster
_VMA_EXTENT_HEADER_SIZE = 512
_VMA_BLOCKS_PER_EXTENT = 59
_VMA_DEV_ENTRY_SIZE = 80    # VmaDeviceInfo: devflags(4)+devid(4)+size(8)+devname(64)
_VMA_MAX_DEVS = 255
# VmaExtentHeader offsets: magic(4)+reserved(2)+block_count(2)+uuid(16)+md5sum(16) = 40
_VMA_BLOCKINFO_OFFSET = 40


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


def _readexact(f: "Any", n: int) -> bytes:
    """Read exactly n bytes from f (handles short reads on pipes)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _vma_extract(vma_source: "Path | Any", raw_path: Path,
                 progress_cb: "Callable[[int], None]") -> tuple[int, str]:
    """
    Parse a VMA stream/file and write the first disk device as a raw image.
    vma_source: Path (opens file) oder bytes-Stream (z.B. subprocess stdout).
    Returns (image_size_bytes, devname).
    """
    from typing import Callable, Any  # local import to avoid top-level cycle

    ctx = open(vma_source, "rb") if isinstance(vma_source, Path) else None
    f = ctx if ctx is not None else vma_source
    try:
        # --- header cluster ---
        header_cluster = _readexact(f, _VMA_CLUSTER_SIZE)
        if len(header_cluster) < 512:
            raise ValueError("VMA-Datei zu kurz — beschädigt?")
        magic = header_cluster[:4]
        if magic[:3] != _VMA_MAGIC:
            raise ValueError(f"Kein VMA-Magic (got {magic!r}) — falsches Format?")
        if magic[3:4] not in (b"\x01", b"\x00"):
            logger.warning("Unbekanntes VMA-Version-Byte %r — versuche trotzdem zu parsen", magic[3:4])

        dev_info_offset = 64
        target_dev_id: int | None = None
        image_size: int = 0
        devname: str = "disk0"

        for i in range(_VMA_MAX_DEVS):
            off = dev_info_offset + i * _VMA_DEV_ENTRY_SIZE
            if off + _VMA_DEV_ENTRY_SIZE > len(header_cluster):
                break
            devflags = header_cluster[off]
            if not (devflags & 0x01):  # bit 0 = active
                continue
            img_size_raw = struct.unpack_from("<Q", header_cluster, off + 8)[0]
            if img_size_raw == 0:
                continue
            name_bytes = header_cluster[off + 16: off + 80]
            name = name_bytes.split(b"\x00")[0].decode("utf-8", errors="replace")
            if target_dev_id is None:
                target_dev_id = i + 1
                image_size = img_size_raw
                devname = name or f"dev{i+1}"

        if target_dev_id is None:
            raise ValueError("VMA enthält kein Disk-Device — Backup leer oder beschädigt?")
        if image_size == 0:
            raise ValueError(f"VMA Device '{devname}' hat Größe 0 — Header-Parsing fehlgeschlagen. "
                             f"Magic={magic!r}, dev_id={target_dev_id}")

        # --- write raw image ---
        total_clusters = (image_size + _VMA_CLUSTER_SIZE - 1) // _VMA_CLUSTER_SIZE
        written_clusters = 0
        logger.info("VMA header: dev=%s dev_id=%d image_size=%d (%.2f GiB) total_clusters=%d",
                    devname, target_dev_id, image_size, image_size / 1073741824, total_clusters)

        with raw_path.open("wb") as out:
            out.truncate(image_size)
            out.seek(0)

            while True:
                extent_header = _readexact(f, _VMA_EXTENT_HEADER_SIZE)
                if not extent_header:
                    break
                if len(extent_header) < _VMA_EXTENT_HEADER_SIZE:
                    break
                if extent_header[:4] != b"VMAE":
                    raise ValueError("Ungültiger Extent-Header — VMA beschädigt?")
                # blockinfo starts at offset 40: magic(4)+reserved(2)+block_count(2)+uuid(16)+md5sum(16)
                block_infos = struct.unpack_from("<59Q", extent_header, _VMA_BLOCKINFO_OFFSET)

                for bi in block_infos:
                    cluster_data = _readexact(f, _VMA_CLUSTER_SIZE)
                    if not cluster_data:
                        break
                    if bi == 0:
                        continue
                    dev_id = (bi >> 56) & 0x7F
                    cluster_num = bi & 0x00FFFFFFFFFFFFFF
                    allocated = bool(bi & (1 << 63))
                    if dev_id == target_dev_id and allocated:
                        if cluster_num >= total_clusters:
                            logger.warning("VMA: cluster_num %d out of bounds (max %d) — überspringe",
                                           cluster_num, total_clusters)
                            continue
                        out.seek(cluster_num * _VMA_CLUSTER_SIZE)
                        out.write(cluster_data)
                        written_clusters += 1
                        pct = 10 + int(written_clusters / max(total_clusters, 1) * 68)
                        progress_cb(min(78, pct))

    finally:
        if ctx is not None:
            ctx.close()

    return image_size, devname


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
            asyncio.create_task(self._extract_vma(job_id, src, _vma_compression(filename),
                                                   keep_source=True))
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
                           keep_source: bool = False) -> None:
        """Proxmox VMA backup extractor — decompresses if needed, then parses VMA format."""
        job = self._jobs.get(job_id)
        if not job:
            return
        raw_path = self._import_dir / f"{job_id}.raw"
        out_path = self._import_dir / f"{job_id}.qcow2"
        vma_path = tmp_path

        try:
            import os as _os
            import shutil as _shutil
            import subprocess as _sp
            _CPUS = str(_os.cpu_count() or 4)
            dec_path = self._import_dir / f"{job_id}_dec.vma"

            # Bereits dekomprimiert (Retry nach Fehler)? Aus Datei lesen, kein Streaming.
            if compression and dec_path.exists() and dec_path.stat().st_size > 0:
                logger.info("VMA %s: dekomprimierte Datei vorhanden — überspringe Dekomprimierung", job_id)
                vma_path = dec_path
                job.progress_pct = 10

                def _do_extract() -> tuple[int, str]:
                    return _vma_extract(vma_path, raw_path, lambda pct: self._set_progress(job_id, pct))
                total_size, devname = await asyncio.get_event_loop().run_in_executor(None, _do_extract)

            elif compression:
                # Dekomprimierung in Datei, dann VMA parsen.
                # Streaming-Pipe-Ansatz war wegen EFBIG unzuverlässig (bi==0 Blöcke, Alignment).
                job.progress_pct = 2
                compressed_size = tmp_path.stat().st_size
                estimated_dec_size = max(compressed_size * 4, 1)

                if compression == "zst":
                    dec_cmd = ["zstd", "--decompress", "--force", f"-T{_CPUS}",
                               str(tmp_path), "-o", str(dec_path)]
                elif compression == "gz" and _shutil.which("pigz"):
                    dec_cmd = ["pigz", "--decompress", "--keep", "--force",
                               f"-p{_CPUS}", "--stdout", str(tmp_path)]
                else:
                    dec_cmd = ["gunzip", "--force", "--keep", "--stdout", str(tmp_path)]

                # stdout in Datei schreiben wenn via --stdout
                use_stdout = compression != "zst"
                if use_stdout:
                    dec_fh = open(dec_path, "wb")
                    dec_proc = await asyncio.create_subprocess_exec(
                        *dec_cmd,
                        stdout=dec_fh.fileno(),
                        stderr=asyncio.subprocess.PIPE,
                    )
                else:
                    dec_fh = None
                    dec_proc = await asyncio.create_subprocess_exec(
                        *dec_cmd,
                        stderr=asyncio.subprocess.PIPE,
                    )

                # Progress während Dekomprimierung via wachsende Ausgabedatei
                while dec_proc.returncode is None:
                    await asyncio.sleep(2)
                    if dec_path.exists():
                        done = dec_path.stat().st_size
                        pct = min(68, int(done / estimated_dec_size * 68))
                        self._set_progress(job_id, 2 + pct)
                    try:
                        await asyncio.wait_for(dec_proc.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass

                if dec_fh:
                    dec_fh.close()
                rc = dec_proc.returncode
                if rc != 0:
                    stderr_out = b""
                    if dec_proc.stderr:
                        stderr_out = await dec_proc.stderr.read()
                    raise RuntimeError(
                        f"Dekomprimierung fehlgeschlagen (rc={rc}): "
                        f"{stderr_out.decode(errors='replace')[-300:]}"
                    )

                vma_path = dec_path
                job.progress_pct = 70

                def _do_extract() -> tuple[int, str]:
                    return _vma_extract(vma_path, raw_path,
                                        lambda pct: self._set_progress(job_id, 70 + int(pct * 0.1)))
                total_size, devname = await asyncio.get_event_loop().run_in_executor(None, _do_extract)

            else:
                # Keine Komprimierung — direkt parsen
                job.progress_pct = 2
                def _do_extract() -> tuple[int, str]:
                    return _vma_extract(vma_path, raw_path, lambda pct: self._set_progress(job_id, pct))
                total_size, devname = await asyncio.get_event_loop().run_in_executor(None, _do_extract)
            job.progress_pct = 80
            logger.info("VMA extract: dev=%s size=%d raw=%s", devname, total_size, raw_path)

            # Step 3 — convert raw → qcow2
            proc2 = await asyncio.create_subprocess_exec(
                "qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2",
                str(raw_path), str(out_path),
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
                    # map qemu-img 0-100 to 80-99
                    q = int(float(m.group(1)))
                    self._set_progress(job_id, 80 + int(q * 0.19))
            await proc2.wait()
            if proc2.returncode != 0:
                raise RuntimeError("qemu-img: " + "".join(stderr_buf).strip()[-300:])

            job.output_path = str(out_path)
            job.progress_pct = 100
            job.status = "done"
        except FileNotFoundError as e:
            job.status = "error"
            job.error = f"Tool nicht gefunden: {e.filename} — bitte zstd/qemu-img installieren"
        except Exception as e:
            job.status = "error"
            job.error = str(e)[:500]
            if out_path.exists():
                out_path.unlink()
        finally:
            if not keep_source and tmp_path.exists():
                tmp_path.unlink()
            # Dekomprimierte Datei NUR bei Erfolg löschen — bei Fehler für Retry aufbewahren
            dec = self._import_dir / f"{job_id}_dec.vma"
            if dec.exists() and job.status == "done":
                dec.unlink()
            if raw_path.exists():
                raw_path.unlink()
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
