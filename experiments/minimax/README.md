# MiniMax Multimodal Integration — PoC / Exploration

**Status:** Proof-of-Concept, **nicht** im Core-Pfad geroutet, **nicht** produktiv.
**Tracking-Issue:** hydrahive/hydrahive#680
**Erste produktionsreife Phase:** hydrahive/hydrahive#679 (Image-Generierung)

Dieser Ordner liegt bewusst außerhalb `core/src/hydrahive_core/`. Die hier
abgelegten Dateien werden von nichts importiert — sie dienen als
Architektur-Referenz und Kannibalisierungsquelle, während wir die einzelnen
Modalitäten schrittweise nach `core/` überführen.

## Inhalt

| Datei | Zweck | Zeilen |
|---|---|---|
| `minimax_client.py` | Async/Sync API-Client für alle MiniMax-Endpoints | ~670 |
| `minimax_mcp_server.py` | MCP-Server (streamableHttp) für MiniMax-Tools | ~722 |
| `minimax_tools.py` | Native Agent-Tool-Klassen (Image/Video/Music/TTS/STT/Vision) | ~531 |
| `__init__.py` | Re-Exports | ~38 |

## Drei Integrations-Wege (aus dem PoC)

1. **MCP-Server** — `python -m experiments.minimax.minimax_mcp_server --port 8182`
   und in HydraHive unter `/mcp/servers` registrieren.
2. **Native Tools** — Tool-Klassen direkt in `tool_registry.register()` einhängen
   und per `tools:` in der Agent-Config aktivieren.
3. **Direkter Client** — `from experiments.minimax.minimax_client import MiniMaxClient`
   für eigene Integrationen oder Tests.

## Wichtige Aspekte (für jede Phase relevant)

- **Async-Jobs**: Video- und Musik-Generierung laufen asynchron. PoC nutzt
  5s-Polling-Intervall, bis 10min Timeout. Das reißt das aktuelle
  120s-Tool-Timeout von HydraHive — braucht eigenes Task-Handling.
- **Output-Dir**: Generierte Artefakte müssen wohin. PoC-Default
  `/tmp/hydrahive-media`; produktiv wahrscheinlich `/projects/{id}/artifacts/`.
- **Quota**: MiniMax-Limit 1500 Requests / 5h. Eigener Quota-Tracker oder
  Respekt der bestehenden Provider-Infrastruktur?
- **MCP-Kompatibilität**: MiniMax bietet selbst MCP an — prüfen, ob direkt
  nutzbar, statt eigenen MCP-Server zu pflegen.

## Kein Merge gegen main

Dieser Branch ist **nicht** als Merge-Quelle gedacht. Einzelne Modalitäten
wandern als saubere, getestete PRs in den Produktions-Pfad:

- Phase 1: Image (#679)
- Phase 2+: Video / Music / TTS-STT als Follow-up-Tickets unter #680

Wenn #679 Phase 1 abgeschlossen ist, wird der Image-Code aus
`minimax_client.py` + `minimax_tools.py` produktionsreif extrahiert
(Error-Handling, Key-Redaction, Tests, Permission-Classifier-Eintrag).
