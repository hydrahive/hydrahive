"""
HydraHive ISO-Manager — ISO-Upload, Validierung, Storage (#898)
================================================================
Async-first, kein shell=True, FastAPI UploadFile-kompatibel.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# ── Magic Bytes ────────────────────────────────────────────────────────────────
# ISO 9660: Sector 16 starts at byte 32768; byte 0 = descriptor type, bytes 1-5 = "CD001"
_ISO9660_MAGIC = b"CD001"
_ISO9660_OFFSET = 32769  # skip the 1-byte descriptor type

# ── Filename-Regex ─────────────────────────────────────────────────────────────
_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9._\-]{1,128}$")


@dataclass
class ISOInfo:
    filename: str
    size_bytes: int
    size_human: str
    uploaded_at: float
    path: str


class ISOManager:
    def __init__(self, iso_dir: Path, max_size_gb: int = 50, max_count: int = 20):
        self._iso_dir = iso_dir.resolve()
        self._max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        self._max_count = max_count

    def _safe_filename(self, filename: str) -> str:
        """Validiert Dateinamen und gibt ihn zurück. Raises ValueError bei ungültig."""
        if not filename.lower().endswith(".iso"):
            raise ValueError(f"Datei muss auf .iso enden: {filename}")
        if not _SAFE_FILENAME.match(filename):
            raise ValueError(f"Ungültiger Dateiname (nur a-z A-Z 0-9 . _ -): {filename}")
        resolved = (self._iso_dir / filename).resolve()
        if not resolved.is_relative_to(self._iso_dir):
            raise ValueError(f"Path-Traversal verhindert: {filename}")
        return filename

    def _format_size(self, size_bytes: int) -> str:
        """Formatiert Bytes als lesbare Größe."""
        gb = size_bytes / (1024**3)
        if gb >= 1:
            return f"{gb:.1f} GB"
        mb = size_bytes / (1024**2)
        return f"{mb:.1f} MB"

    def validate_iso(self, path: Path) -> bool:
        """Prüft ISO 9660 Magic Bytes. Akzeptiert auch UDF-ISOs."""
        try:
            if path.stat().st_size < _ISO9660_OFFSET + 5:  # needs 32769 + 5 bytes
                return False
            with path.open("rb") as f:
                f.seek(_ISO9660_OFFSET)
                magic = f.read(5)
            return magic == _ISO9660_MAGIC
        except OSError:
            return False

    async def save_iso(self, filename: str, chunks: AsyncIterator[bytes]) -> ISOInfo:
        """Speichert ein ISO-Image aus einem Stream."""
        safe_name = self._safe_filename(filename)

        # Duplikat-Check
        if (self._iso_dir / safe_name).exists():
            raise ValueError(f"ISO '{safe_name}' existiert bereits — Upload verweigert (409)")

        # Max-Count-Check
        existing = [p for p in self._iso_dir.iterdir() if p.suffix.lower() == ".iso"]
        if len(existing) >= self._max_count:
            raise ValueError(
                f"Max-ISO-Count erreicht ({self._max_count}) — altes ISO löschen bevor neues hochgeladen werden kann"
            )

        # In temporäre Datei schreiben
        tmp_path = self._iso_dir / f"{safe_name}.tmp"
        max_size = self._max_size_bytes
        written = 0
        try:
            with tmp_path.open("wb") as fh:
                async for chunk in chunks:
                    if written + len(chunk) > max_size:
                        fh.flush()
                        raise ValueError(
                            f"ISO-Größe überschreitet Limit von {self._max_size_bytes / (1024**3):.0f} GB"
                        )
                    fh.write(chunk)
                    written += len(chunk)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        # Datei zu groß (bei sync geschrieben)
        if tmp_path.stat().st_size > max_size:
            tmp_path.unlink()
            raise ValueError(f"ISO-Größe überschreitet Limit von {self._max_size_bytes / (1024**3):.0f} GB")

        # Magic-Bytes validieren
        if not self.validate_iso(tmp_path):
            tmp_path.unlink()
            raise ValueError("Keine gültige ISO-Datei (kein ISO 9660 oder UDF Magic gefunden)")

        # tmp → finale Datei umbenennen
        final_path = self._iso_dir / safe_name
        tmp_path.rename(final_path)
        final_path.chmod(0o640)

        uploaded_at = final_path.stat().st_mtime
        logger.info("ISO gespeichert: %s (%s)", safe_name, self._format_size(written))
        return ISOInfo(
            filename=safe_name,
            size_bytes=written,
            size_human=self._format_size(written),
            uploaded_at=uploaded_at,
            path=str(final_path),
        )

    def list_isos(self) -> list[ISOInfo]:
        """Gibt alle ISOs sortiert nach mtime (neueste zuerst) zurück."""
        result: list[ISOInfo] = []
        for p in sorted(self._iso_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix.lower() != ".iso":
                continue
            st = p.stat()
            result.append(ISOInfo(
                filename=p.name,
                size_bytes=st.st_size,
                size_human=self._format_size(st.st_size),
                uploaded_at=st.st_mtime,
                path=str(p),
            ))
        return result

    def delete_iso(self, filename: str) -> None:
        """Löscht ein ISO. Raises ValueError wenn nicht gefunden."""
        safe = self._safe_filename(filename)
        path = self._iso_dir / safe
        if not path.exists():
            raise ValueError(f"ISO nicht gefunden: {filename}")
        path.unlink()
        logger.info("ISO gelöscht: %s", safe)

    def get_iso_path(self, filename: str) -> Path:
        """Gibt den absoluten Path eines ISO zurück. Raises ValueError wenn nicht gefunden."""
        safe = self._safe_filename(filename)
        path = self._iso_dir / safe
        if not path.exists():
            raise ValueError(f"ISO nicht gefunden: {filename}")
        return path
