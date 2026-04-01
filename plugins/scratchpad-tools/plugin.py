"""scratchpad-tools — Scratchpads lesen und bearbeiten als Agent-Tools."""
import json
from pathlib import Path

PADS_DIR = Path("/etc/hydrahive/scratchpads")

def register(api):

    @api.tool(
        tool_id="scratch_list",
        description="Listet alle verfügbaren Scratchpads.",
        parameters={"type":"object","properties":{},"required":[]},
    )
    def scratch_list(**_) -> str:
        if not PADS_DIR.exists():
            return "Keine Scratchpads vorhanden"
        pads = sorted(f.stem for f in PADS_DIR.glob("*.json"))
        if not pads:
            return "Keine Scratchpads vorhanden"
        return "Verfügbare Scratchpads:\n" + "\n".join(f"  - {p}" for p in pads)

    @api.tool(
        tool_id="scratch_read",
        description="Liest ein Scratchpad und gibt die Notizen und Verbindungen als Text zurück. Nutze dieses Tool wenn der User sagt 'schau dir mein Scratchpad an'.",
        parameters={
            "type":"object",
            "properties":{
                "name":{"type":"string","description":"Scratchpad-Name (default: default)"},
            },
            "required":[],
        },
    )
    def scratch_read(name: str = "default", **_) -> str:
        pad_file = PADS_DIR / f"{name}.json"
        if not pad_file.exists():
            return f"Scratchpad '{name}' nicht gefunden"
        try:
            data = json.loads(pad_file.read_text())
        except Exception as e:
            return f"Fehler beim Lesen: {e}"

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        if not nodes:
            return f"Scratchpad '{name}' ist leer"

        node_map = {}
        lines = [f"Scratchpad: {name}", f"{len(nodes)} Notizen, {len(edges)} Verbindungen", ""]
        lines.append("Notizen:")
        for n in nodes:
            nd = n.get("data", {})
            color = nd.get("color", "zinc")
            label = nd.get("label", "")
            note = nd.get("note", "")
            node_map[n["id"]] = label
            lines.append(f"  [{color}] {label}")
            if note:
                lines.append(f"          Notiz: {note}")

        if edges:
            lines.append("")
            lines.append("Verbindungen:")
            for e in edges:
                src = node_map.get(e.get("source", ""), "?")
                tgt = node_map.get(e.get("target", ""), "?")
                lines.append(f"  {src} --> {tgt}")

        return "\n".join(lines)

    @api.tool(
        tool_id="scratch_write",
        description="Erstellt oder überschreibt ein Scratchpad. Gibt eine Liste von Notizen (mit Farbe und Position) und Verbindungen an. Der User sieht das Ergebnis live im Blueprint-Tab.",
        parameters={
            "type":"object",
            "properties":{
                "name":{"type":"string","description":"Scratchpad-Name"},
                "notes":{"type":"array","description":"Liste von Notizen","items":{
                    "type":"object",
                    "properties":{
                        "text":{"type":"string","description":"Notiz-Text"},
                        "color":{"type":"string","enum":["zinc","blue","green","purple","orange","pink","cyan","amber","red"],"description":"Farbe"},
                        "note":{"type":"string","description":"Kleine Zusatzinfo (optional)"},
                    },
                    "required":["text"],
                }},
                "connections":{"type":"array","description":"Verbindungen zwischen Notizen (Index-basiert, 0=erste Notiz)","items":{
                    "type":"object",
                    "properties":{
                        "from":{"type":"integer","description":"Index der Quell-Notiz"},
                        "to":{"type":"integer","description":"Index der Ziel-Notiz"},
                    },
                    "required":["from","to"],
                }},
            },
            "required":["name","notes"],
        },
    )
    def scratch_write(name: str, notes: list, connections: list = None, **_) -> str:
        PADS_DIR.mkdir(parents=True, exist_ok=True)
        nodes = []
        edges = []
        # Notizen als Nodes layouten (Grid)
        cols = max(3, int(len(notes) ** 0.5) + 1)
        for i, note in enumerate(notes):
            node_id = f"scratch-{i+1}"
            col = i % cols
            row = i // cols
            nodes.append({
                "id": node_id,
                "type": "scratch",
                "position": {"x": 80 + col * 220, "y": 80 + row * 120},
                "data": {
                    "label": note.get("text", ""),
                    "color": note.get("color", "zinc"),
                    "note": note.get("note", ""),
                },
            })
        # Verbindungen
        for conn in (connections or []):
            src_idx = conn.get("from", 0)
            tgt_idx = conn.get("to", 0)
            if 0 <= src_idx < len(nodes) and 0 <= tgt_idx < len(nodes):
                edges.append({
                    "id": f"e-{src_idx}-{tgt_idx}",
                    "source": nodes[src_idx]["id"],
                    "target": nodes[tgt_idx]["id"],
                    "sourceHandle": "r",
                    "targetHandle": "l",
                    "animated": False,
                    "style": {"stroke": "#6366f1", "strokeWidth": 2},
                    "type": "smoothstep",
                })
        data = {"nodes": nodes, "edges": edges, "counter": len(nodes)}
        pad_file = PADS_DIR / f"{name}.json"
        pad_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return f"Scratchpad '{name}' gespeichert: {len(nodes)} Notizen, {len(edges)} Verbindungen. User kann es im Blueprint-Tab unter Scratchpad → {name} ansehen."

    @api.tool(
        tool_id="scratch_add_note",
        description="Fügt eine einzelne Notiz zu einem bestehenden Scratchpad hinzu.",
        parameters={
            "type":"object",
            "properties":{
                "name":{"type":"string","description":"Scratchpad-Name"},
                "text":{"type":"string","description":"Notiz-Text"},
                "color":{"type":"string","description":"Farbe (default: zinc)"},
                "connect_to":{"type":"string","description":"Text einer existierenden Notiz mit der verbunden werden soll (optional)"},
            },
            "required":["name","text"],
        },
    )
    def scratch_add_note(name: str, text: str, color: str = "zinc", connect_to: str = "", **_) -> str:
        pad_file = PADS_DIR / f"{name}.json"
        if pad_file.exists():
            data = json.loads(pad_file.read_text())
        else:
            data = {"nodes": [], "edges": [], "counter": 0}

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        cnt = data.get("counter", len(nodes)) + 1

        # Position: rechts neben der letzten Notiz
        last_x = max((n["position"]["x"] for n in nodes), default=0)
        last_y = max((n["position"]["y"] for n in nodes), default=0)
        new_id = f"scratch-{cnt}"
        nodes.append({
            "id": new_id, "type": "scratch",
            "position": {"x": last_x + 220, "y": 80 + (len(nodes) % 4) * 100},
            "data": {"label": text, "color": color, "note": ""},
        })

        # Verbindung
        if connect_to:
            for n in nodes:
                if n["data"]["label"].lower().strip() == connect_to.lower().strip():
                    edges.append({
                        "id": f"e-{n['id']}-{new_id}",
                        "source": n["id"], "target": new_id,
                        "sourceHandle": "r", "targetHandle": "l",
                        "animated": False, "style": {"stroke": "#6366f1", "strokeWidth": 2}, "type": "smoothstep",
                    })
                    break

        data["nodes"] = nodes
        data["edges"] = edges
        data["counter"] = cnt
        pad_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return f"Notiz '{text}' zu Scratchpad '{name}' hinzugefügt"
