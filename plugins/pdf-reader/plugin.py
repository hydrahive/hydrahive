"""
pdf-reader Plugin — PDF Text-Extraktion als Agent-Tool.

Nutzt subprocess mit pdftotext (poppler-utils) als Fallback,
oder versucht reines Python-Parsing mit dem eingebauten Modul.

Tools:
  - pdf_read:  PDF-Text extrahieren (ganz oder Seiten-Range)
  - pdf_info:  PDF-Metadaten (Seiten, Titel, Autor, Größe)
  - pdf_search: Text in einer PDF suchen
"""
import subprocess
from pathlib import Path


def _pdf_to_text(path: str, first_page: int = 0, last_page: int = 0) -> str:
    """Extrahiert Text aus PDF via pdftotext (poppler-utils)."""
    cmd = ["pdftotext", "-layout"]
    if first_page > 0:
        cmd += ["-f", str(first_page)]
    if last_page > 0:
        cmd += ["-l", str(last_page)]
    cmd += [path, "-"]  # "-" = stdout
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout
        return f"pdftotext Fehler: {r.stderr.strip()}"
    except FileNotFoundError:
        return "_FALLBACK_"
    except subprocess.TimeoutExpired:
        return "Timeout beim Lesen der PDF"


def _pdf_info_raw(path: str) -> dict:
    """PDF-Metadaten via pdfinfo (poppler-utils)."""
    try:
        r = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {}
        info = {}
        for line in r.stdout.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                info[key.strip()] = val.strip()
        return info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def _fallback_read(path: str) -> str:
    """Einfacher Fallback: Versuche Text direkt aus der Datei zu extrahieren."""
    try:
        raw = Path(path).read_bytes()
        # Einfache Text-Extraktion aus PDF-Streams
        import re
        texts = []
        for match in re.finditer(rb'\(([^)]+)\)', raw):
            try:
                texts.append(match.group(1).decode('utf-8', errors='replace'))
            except Exception:
                pass
        if texts:
            return " ".join(texts)[:15000]
        return "Text konnte nicht extrahiert werden. Installiere poppler-utils: sudo apt install poppler-utils"
    except Exception as e:
        return f"Fehler: {e}"


def register(api):

    @api.tool(
        tool_id="pdf_read",
        description="Extrahiert Text aus einer PDF-Datei. Kann ganze PDFs oder einzelne Seiten lesen. Nutze dieses Tool wenn der User nach dem Inhalt einer PDF fragt.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur PDF-Datei"},
                "first_page": {"type": "integer", "description": "Erste Seite (default: 1)"},
                "last_page": {"type": "integer", "description": "Letzte Seite (default: alle)"},
            },
            "required": ["path"],
        },
    )
    def pdf_read(path: str, first_page: int = 0, last_page: int = 0, **_) -> str:
        p = Path(path)
        if not p.exists():
            return f"Datei nicht gefunden: {path}"
        if not p.suffix.lower() == ".pdf":
            return "Keine PDF-Datei"
        if p.stat().st_size > 100_000_000:
            return "PDF zu groß (max 100MB)"

        text = _pdf_to_text(path, first_page, last_page)
        if text == "_FALLBACK_":
            text = _fallback_read(path)

        if len(text) > 30000:
            text = text[:30000] + f"\n\n... [gekürzt, {len(text)} Zeichen insgesamt. Nutze first_page/last_page für Ausschnitte.]"
        return text or "Kein Text in der PDF gefunden"

    @api.tool(
        tool_id="pdf_info",
        description="Zeigt Metadaten einer PDF-Datei: Seitenanzahl, Titel, Autor, Erstelldatum, Dateigröße.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur PDF-Datei"},
            },
            "required": ["path"],
        },
    )
    def pdf_info(path: str, **_) -> str:
        p = Path(path)
        if not p.exists():
            return f"Datei nicht gefunden: {path}"

        size_mb = p.stat().st_size / (1024 * 1024)
        info = _pdf_info_raw(path)

        if info:
            lines = [
                f"Datei: {path}",
                f"Größe: {size_mb:.1f} MB",
                f"Seiten: {info.get('Pages', '?')}",
                f"Titel: {info.get('Title', '—')}",
                f"Autor: {info.get('Author', '—')}",
                f"Erstellt: {info.get('CreationDate', '—')}",
                f"PDF-Version: {info.get('PDF version', '—')}",
                f"Verschlüsselt: {info.get('Encrypted', 'nein')}",
            ]
        else:
            lines = [
                f"Datei: {path}",
                f"Größe: {size_mb:.1f} MB",
                "Metadaten: pdfinfo nicht verfügbar (installiere poppler-utils)",
            ]
        return "\n".join(lines)

    @api.tool(
        tool_id="pdf_search",
        description="Sucht einen Text in einer PDF-Datei und zeigt die Fundstellen mit Kontext.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur PDF-Datei"},
                "query": {"type": "string", "description": "Suchbegriff"},
            },
            "required": ["path", "query"],
        },
    )
    def pdf_search(path: str, query: str, **_) -> str:
        p = Path(path)
        if not p.exists():
            return f"Datei nicht gefunden: {path}"

        text = _pdf_to_text(path)
        if text == "_FALLBACK_":
            text = _fallback_read(path)

        if not text or text.startswith("Fehler"):
            return text or "Kein Text in der PDF"

        query_lower = query.lower()
        lines = text.splitlines()
        matches = []
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # Kontext: Zeile davor und danach
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                context = "\n".join(lines[start:end])
                matches.append(f"Zeile {i+1}:\n{context}")

        if not matches:
            return f"'{query}' nicht in der PDF gefunden"
        result = f"'{query}' — {len(matches)} Treffer:\n\n" + "\n---\n".join(matches[:20])
        if len(matches) > 20:
            result += f"\n\n... und {len(matches) - 20} weitere Treffer"
        return result
