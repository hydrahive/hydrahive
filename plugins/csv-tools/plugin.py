"""
csv-tools Plugin — CSV/JSON Datenverarbeitung ohne LLM-Tokens.

Tools:
  - csv_load:     CSV laden und Überblick (Spalten, Zeilen, Typen)
  - csv_query:    Filtern, Sortieren, Aggregieren
  - csv_to_json:  CSV in JSON konvertieren
  - json_query:   JSON Datei lesen und Pfad abfragen (jq-ähnlich)
  - csv_head:     Erste N Zeilen einer CSV anzeigen
"""
import csv
import json
import io
from pathlib import Path


def _read_csv(path: str) -> tuple[list[str], list[dict]]:
    """CSV einlesen, gibt (header, rows) zurück."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")
    if p.stat().st_size > 50_000_000:
        raise ValueError("Datei zu groß (max 50MB)")
    text = p.read_text(encoding="utf-8", errors="replace")
    # Delimiter erkennen
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t|")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    rows = list(reader)
    return headers, rows


def register(api):

    @api.tool(
        tool_id="csv_load",
        description="CSV-Datei laden und Überblick anzeigen: Spalten, Zeilenanzahl, Datentypen, erste Werte. Nutze dieses Tool bevor du mit CSV-Daten arbeitest.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur CSV-Datei"},
            },
            "required": ["path"],
        },
    )
    def csv_load(path: str, **_) -> str:
        try:
            headers, rows = _read_csv(path)
        except Exception as e:
            return f"Fehler: {e}"

        lines = [
            f"Datei: {path}",
            f"Zeilen: {len(rows)}",
            f"Spalten: {len(headers)}",
            "",
            "Spalten-Übersicht:",
        ]
        for h in headers:
            values = [r.get(h, "") for r in rows[:100] if r.get(h)]
            sample = values[:3]
            # Typ schätzen
            numeric = sum(1 for v in values if v.replace(".", "").replace("-", "").isdigit())
            dtype = "numerisch" if numeric > len(values) * 0.7 else "text"
            empty = sum(1 for r in rows if not r.get(h, "").strip())
            lines.append(f"  {h}: {dtype}, {empty} leer, Beispiele: {sample}")

        return "\n".join(lines)

    @api.tool(
        tool_id="csv_query",
        description="CSV-Datei filtern, sortieren und aggregieren. Ergebnisse als Text-Tabelle.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur CSV-Datei"},
                "filter": {"type": "string", "description": "Filter z.B. 'status=active' oder 'price>100' (optional)"},
                "sort_by": {"type": "string", "description": "Spalte zum Sortieren (optional)"},
                "sort_desc": {"type": "boolean", "description": "Absteigend sortieren (default: false)"},
                "columns": {"type": "string", "description": "Nur diese Spalten zeigen, komma-getrennt (optional)"},
                "limit": {"type": "integer", "description": "Max Zeilen (default: 50)"},
                "aggregate": {"type": "string", "description": "Aggregation z.B. 'sum:price' oder 'count:status' oder 'avg:score' (optional)"},
            },
            "required": ["path"],
        },
    )
    def csv_query(path: str, filter: str = "", sort_by: str = "", sort_desc: bool = False,
                  columns: str = "", limit: int = 50, aggregate: str = "", **_) -> str:
        try:
            headers, rows = _read_csv(path)
        except Exception as e:
            return f"Fehler: {e}"

        # Filter
        if filter:
            for op in [">=", "<=", "!=", "=", ">", "<"]:
                if op in filter:
                    col, val = filter.split(op, 1)
                    col, val = col.strip(), val.strip()
                    filtered = []
                    for r in rows:
                        rv = r.get(col, "")
                        try:
                            if op == "=": match = rv == val
                            elif op == "!=": match = rv != val
                            elif op == ">": match = float(rv) > float(val)
                            elif op == "<": match = float(rv) < float(val)
                            elif op == ">=": match = float(rv) >= float(val)
                            elif op == "<=": match = float(rv) <= float(val)
                            else: match = False
                        except (ValueError, TypeError):
                            match = val.lower() in rv.lower() if op == "=" else False
                        if match:
                            filtered.append(r)
                    rows = filtered
                    break

        # Aggregation
        if aggregate:
            parts = aggregate.split(":")
            if len(parts) == 2:
                func, col = parts[0].lower(), parts[1]
                values = [r.get(col, "") for r in rows]
                if func == "count":
                    counts: dict = {}
                    for v in values:
                        counts[v] = counts.get(v, 0) + 1
                    lines = [f"count({col}) — {len(rows)} Zeilen:", ""]
                    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
                        lines.append(f"  {k or '(leer)'}: {v}")
                    return "\n".join(lines)
                nums = []
                for v in values:
                    try: nums.append(float(v))
                    except: pass
                if func == "sum": return f"sum({col}) = {sum(nums)}"
                elif func == "avg": return f"avg({col}) = {sum(nums)/len(nums):.2f}" if nums else "Keine numerischen Werte"
                elif func == "min": return f"min({col}) = {min(nums)}" if nums else "Keine Werte"
                elif func == "max": return f"max({col}) = {max(nums)}" if nums else "Keine Werte"

        # Sortierung
        if sort_by and sort_by in headers:
            def sort_key(r):
                v = r.get(sort_by, "")
                try: return (0, float(v))
                except: return (1, v.lower())
            rows.sort(key=sort_key, reverse=sort_desc)

        # Spalten filtern
        show_cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else headers

        # Limit
        limit = min(max(1, limit), 500)
        rows = rows[:limit]

        # Tabelle formatieren
        if not rows:
            return "Keine Ergebnisse"
        lines = ["\t".join(show_cols)]
        for r in rows:
            lines.append("\t".join(r.get(c, "") for c in show_cols))
        result = "\n".join(lines)
        if len(result) > 15000:
            result = result[:15000] + "\n... (gekürzt)"
        return result

    @api.tool(
        tool_id="csv_to_json",
        description="Konvertiert eine CSV-Datei zu JSON.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur CSV-Datei"},
                "limit": {"type": "integer", "description": "Max Zeilen (default: 100)"},
            },
            "required": ["path"],
        },
    )
    def csv_to_json(path: str, limit: int = 100, **_) -> str:
        try:
            headers, rows = _read_csv(path)
        except Exception as e:
            return f"Fehler: {e}"
        limit = min(max(1, limit), 1000)
        result = json.dumps(rows[:limit], ensure_ascii=False, indent=2)
        if len(result) > 15000:
            result = result[:15000] + "\n... (gekürzt)"
        return result

    @api.tool(
        tool_id="json_query",
        description="JSON-Datei lesen und einen Pfad abfragen (ähnlich wie jq). Unterstützt verschachtelte Pfade mit Punkt-Notation.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur JSON-Datei"},
                "query": {"type": "string", "description": "JSON-Pfad z.B. 'data.items' oder 'users.0.name' (optional, leer = gesamte Datei)"},
            },
            "required": ["path"],
        },
    )
    def json_query(path: str, query: str = "", **_) -> str:
        p = Path(path)
        if not p.exists():
            return f"Datei nicht gefunden: {path}"
        if p.stat().st_size > 50_000_000:
            return "Datei zu groß (max 50MB)"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return f"JSON-Fehler: {e}"

        if query:
            parts = query.split(".")
            current = data
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list):
                    try: current = current[int(part)]
                    except: current = None
                else:
                    current = None
                if current is None:
                    return f"Pfad '{query}' nicht gefunden"

            result = json.dumps(current, ensure_ascii=False, indent=2)
        else:
            result = json.dumps(data, ensure_ascii=False, indent=2)

        if len(result) > 15000:
            result = result[:15000] + "\n... (gekürzt)"
        return result

    @api.tool(
        tool_id="csv_head",
        description="Zeigt die ersten N Zeilen einer CSV-Datei als formatierte Tabelle.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur CSV-Datei"},
                "lines": {"type": "integer", "description": "Anzahl Zeilen (default: 10)"},
            },
            "required": ["path"],
        },
    )
    def csv_head(path: str, lines: int = 10, **_) -> str:
        try:
            headers, rows = _read_csv(path)
        except Exception as e:
            return f"Fehler: {e}"
        lines = min(max(1, lines), 100)
        result = ["\t".join(headers)]
        for r in rows[:lines]:
            result.append("\t".join(r.get(h, "") for h in headers))
        return "\n".join(result)
