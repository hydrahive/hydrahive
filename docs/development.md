# OctopOS — Entwickler-Guide

Wie man OctopOS erweitert: neue Tools, Skills, Console-Seiten, Installer-Module.

---

## Inhaltsverzeichnis

1. [Entwicklungsumgebung](#1-entwicklungsumgebung)
2. [Eigenes Tool schreiben](#2-eigenes-tool-schreiben)
3. [Eigene Skills schreiben](#3-eigene-skills-schreiben)
4. [Core-Endpoint hinzufügen](#4-core-endpoint-hinzufügen)
5. [Console-Seite hinzufügen](#5-console-seite-hinzufügen)
6. [Installer-Modul hinzufügen](#6-installer-modul-hinzufügen)
7. [Deploy-Workflow](#7-deploy-workflow)
8. [Coding-Konventionen](#8-coding-konventionen)

---

## 1. Entwicklungsumgebung

### Voraussetzungen

- Python 3.12+
- Node.js 22+
- Ein laufendes OctopOS auf der VM (für Integration-Tests)

### Console lokal entwickeln

```bash
cd console
npm install
npm run dev   # Vite Dev-Server auf Port 5173
```

Die Console läuft dann auf `http://localhost:5173`. API-Calls gehen an `/api/...` — dafür muss entweder:
- ein lokaler Core laufen (Port 8765), oder
- in `vite.config.ts` ein Proxy auf die VM eingetragen werden:

```typescript
// vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://YOUR-VM-IP"
    }
  }
})
```

### Core lokal entwickeln

```bash
cd core
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Core starten (braucht /agents, /projects, /etc/octopos)
uvicorn octopos_core.main:app --reload --port 8765
```

Für lokale Entwicklung ohne vollständige Installation: Verzeichnisse manuell anlegen und `AGENTS_DIR`/`PROJECTS_DIR` in `main.py` temporär anpassen.

---

## 2. Eigenes Tool schreiben

Tools sind Python-Klassen die `BaseTool` erben.

### Minimalbeispiel

```python
# core/src/octopos_core/tools/my_tool.py
from ..tool_registry import BaseTool

class MyTool(BaseTool):
    @property
    def id(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Beschreibung für das LLM — was macht dieses Tool?"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Die Eingabe"
                }
            },
            "required": ["input"]
        }

    async def execute(
        self,
        agent_id:   str,
        project_id: str,
        input:      str,
        **kwargs,
    ) -> str:
        # Tool-Logik hier
        result = f"Verarbeitet: {input}"
        return result
```

### Filesystem-Tool (mit Path-Safety)

```python
from ..tool_registry import BaseTool, assert_path_within_project

class MyFileTool(BaseTool):
    @property
    def id(self) -> str:
        return "my_file_tool"

    @property
    def description(self) -> str:
        return "Liest eine Datei im Projektverzeichnis"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad zur Datei im Projektverzeichnis"
                }
            },
            "required": ["path"]
        }

    async def execute(self, agent_id, project_id, path, **kwargs) -> str:
        # PFLICHT: Path-Safety-Check vor jedem Filesystem-Zugriff
        safe_path = assert_path_within_project(path, project_id)

        if not safe_path.exists():
            return f"Datei nicht gefunden: {path}"

        return safe_path.read_text(encoding="utf-8")
```

### Tool registrieren

In `tool_registry.py` am Ende:

```python
from .tools.my_tool import MyTool

registry = ToolRegistry()
# ... bestehende Tools ...
registry.register(MyTool())
registry.register(MyFileTool())
```

### Tool in agent.yaml aktivieren

```yaml
tools:
  - my_tool
  - my_file_tool
```

---

## 3. Eigene Skills schreiben

Skills sind Markdown-Dateien mit YAML-Frontmatter unter `/agents/<id>/skills/`.

### Datei anlegen

```markdown
---
skill: Mein Skill Name
version: "1.0"
scope: on-demand
triggers:
  - keyword1
  - keyword2
priority: 20
---

## Skill-Inhalt

Hier steht das Wissen das der Agent erhalten soll wenn dieser Skill geladen wird.

Markdown-Formatierung ist erlaubt und wird dem LLM im System-Prompt angezeigt.
```

### scope-Optionen

| scope | Verhalten |
|---|---|
| `always` | Immer in jedem Request geladen |
| `on-demand` | Nur wenn ein `triggers`-Keyword im User-Text vorkommt |

### Hot-Reload

Skills werden bei jedem Request neu eingelesen. Keine Neustart-Notwendigkeit.

### Via Konsole anlegen

**Agenten** → Agent auswählen → Buch-Icon → **Neuer Skill**

---

## 4. Core-Endpoint hinzufügen

Alle Endpoints sind in `main.py`. Für neue Features:

```python
# main.py

class MyRequest(BaseModel):
    name: str
    value: int

@app.get("/my-feature")
def get_my_feature(username: str = Depends(require_auth)):
    """Kurze Beschreibung des Endpoints."""
    return {"data": "..."}

@app.post("/my-feature", status_code=201)
async def create_my_feature(
    req:      MyRequest,
    username: str = Depends(require_auth),
):
    # Audit-Log schreiben
    audit_log(
        user=username,
        action="my_feature.create",
        target=req.name,
        ip="internal",
    )
    return {"created": True}
```

### Audit-Log

Für alle schreibenden Operationen:

```python
audit_log(
    user=username,        # aus Depends(require_auth)
    action="thing.create",
    target="thing-id",
    project_id="proj-id",  # optional
    ip=request.client.host if request.client else "unknown",
    details={"key": "value"},  # optional
)
```

### Authentifizierung

```python
# Endpoint braucht Auth:
def my_endpoint(username: str = Depends(require_auth)):

# Endpoint ohne Auth (z.B. health, hooks/wake):
def my_public_endpoint():
```

---

## 5. Console-Seite hinzufügen

### 1. Seiten-Komponente erstellen

```typescript
// console/src/pages/MyPage.tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function MyPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    api.get("/my-feature").then(setData).catch(console.error);
  }, []);

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-semibold">Meine Seite</h1>
      {/* Inhalt */}
    </div>
  );
}
```

### 2. API-Methode ergänzen

```typescript
// console/src/lib/api.ts
export const api = {
  // ... bestehende Methoden ...
  myFeature: () => api.get<MyFeatureResponse>("/my-feature"),
  createMyFeature: (d: unknown) => api.post("/my-feature", d),
};

export interface MyFeatureResponse {
  data: string;
}
```

### 3. Route in App.tsx eintragen

```typescript
// console/src/App.tsx
import { MyPage } from "@/pages/MyPage";

// In der Route-Konfiguration:
<Route path="my-feature" element={<MyPage />} />
```

### 4. Sidebar-Eintrag in AdminLayout.tsx

```typescript
// console/src/components/layout/AdminLayout.tsx
import { Star } from "lucide-react";  // Passendes Icon aus lucide-react

const nav = [
  // ... bestehende Einträge ...
  { to: "/my-feature", icon: Star, label: "Mein Feature" },
];
```

### Wiederverwendbare Panel-Komponente

Für aufklappbare Panels innerhalb von Listen-Seiten (wie SkillsPanel, WebhooksPanel):

```typescript
// console/src/components/MyPanel.tsx
interface Props { entityId: string; }

export function MyPanel({ entityId }: Props) {
  // State für Daten, Formular, Ladezustand
  return (
    <div className="border-t">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/20">
        {/* Panel-Header */}
      </div>
      {/* Panel-Inhalt */}
    </div>
  );
}
```

### Styling-Konventionen

- Tailwind CSS mit shadcn/ui-Farbvariablen
- `bg-card border rounded-lg` für Karten
- `text-sm` als Standard-Textgröße
- `text-muted-foreground` für sekundären Text
- `text-destructive` für Fehler, `text-green-500` für Erfolg
- Icons von `lucide-react`

---

## 6. Installer-Modul hinzufügen

```bash
# installer/modules/10_my_module.sh
#!/usr/bin/env bash
# OctopOS Installer - Modul 10: Mein Feature
# Kurze Beschreibung was dieses Modul macht

info "Installiere Mein Feature..."

# Idempotenz-Check: Nur installieren wenn noch nicht vorhanden
if command -v my-tool &>/dev/null; then
    info "my-tool bereits vorhanden — übersprungen"
else
    apt-get install -y my-tool &>/dev/null
    success "my-tool installiert"
fi

# Konfiguration schreiben
cat > /etc/my-tool/config.conf << 'EOF'
setting = value
EOF

success "Mein Feature eingerichtet"
```

In `install.sh` einbinden:

```bash
source "${MODULES_DIR}/10_my_module.sh"
```

**Pflicht-Regeln:**
- `#!/usr/bin/env bash` am Anfang
- `chmod +x` setzen
- Idempotent: Wiederholter Aufruf darf nichts kaputt machen
- `info`/`success`/`warn`/`error` statt `echo` verwenden
- Fehler mit `error "Meldung"` → beendet den Installer (exit 1)

---

## 7. Deploy-Workflow

### Core deployen (Schnell)

```bash
# Einzelne Datei
scp core/src/octopos_core/main.py hydrahive@YOUR-VM-IP:/tmp/main.py
ssh hydrahive@YOUR-VM-IP "sudo cp /tmp/main.py /opt/octopos/core/src/octopos_core/main.py && sudo systemctl restart octopos-core"

# Ganzes Core-Verzeichnis
ssh hydrahive@YOUR-VM-IP "sudo cp -r /tmp/core_src/. /opt/octopos/core/src/octopos_core/"
```

### Console deployen

```bash
cd console
npm run build                                                    # dist/ bauen
scp -r dist/* hydrahive@YOUR-VM-IP:/tmp/console_dist/         # übertragen
ssh hydrahive@YOUR-VM-IP "sudo cp -r /tmp/console_dist/. /opt/octopos/console/"
```

### Core-Status prüfen

```bash
ssh hydrahive@YOUR-VM-IP "sudo systemctl is-active octopos-core && sudo journalctl -u octopos-core -n 20 --no-pager"
```

### Git-Workflow

```bash
# Vor dem Push: immer pullen (andere können committen)
git pull --rebase

# Bei Merge-Konflikten:
# Konflikte manuell lösen → git add → git rebase --continue
```

---

## 8. Coding-Konventionen

### Python (Core)

- **Keine neuen Abhängigkeiten** ohne Diskussion — jede neue lib muss in `pyproject.toml` und im Installer nachgezogen werden
- **Pydantic** für alle Config-Strukturen — kein freies `dict` für Konfiguration
- **`async`/`await`** für alle I/O-Operationen
- **Logging** statt `print`: `logger = logging.getLogger(__name__)`
- **Fehlerbehandlung:** Exceptions loggen und sinnvoll weitergeben, nie still schlucken
- **Path-Safety:** Alle Filesystem-Zugriffe in Tools über `assert_path_within_project()`
- **Audit-Log:** Alle schreibenden Endpoints loggen via `audit_log()`

### TypeScript (Console)

- **Kein `any`** außer bei unvermeidbaren Legacy-Interfaces
- **Interfaces** für alle API-Response-Typen in `api.ts`
- **Keine direkten `fetch`-Aufrufe** in Pages — immer über `api.*`
- **`useEffect` Cleanup:** Intervals und Event-Listener immer bereinigen
- **Stabile Keys:** Nie `key={index}` bei mutierbaren Listen — immer IDs verwenden

### Allgemein

- **Idempotenz:** Alle Installer-Module und Setup-Operationen mehrfach ausführbar
- **Keine Hardcoded Pfade** in Code — Konstanten am Dateianfang oder aus Config
- **Commits:** Feature + Console + Core wenn möglich in einem Commit; Co-Authored-By

### Dateigröße

`main.py` ist aktuell ~1180 Zeilen. Neue Endpoints sollten sparsam sein. Bei >1500 Zeilen: Router-Module aufteilen (`/agents` → `routers/agents.py` etc.).
