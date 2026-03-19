# Session 5 — 19. März 2026

## Teilnehmer
- Till (Product Vision & Anwenderschicht)
- Claude Code (Systemebene & Technische Entscheidungen)

## Thema: GPU & Installer-Flow

## Installer (entschieden)

Einzelner Befehl, läuft als root, erkennt GPU automatisch:

```bash
curl -sSL https://get.agentos.io | bash
```

Idempotent — kann mehrfach ausgeführt werden ohne Schaden.
Farbige Ausgabe, Fortschrittsanzeigen, klare Fehlermeldungen.

## GPU-Erkennung & Profil-Auswahl (entschieden)

```
nvidia-smi vorhanden + CUDA-fähig  →  Full Profile
kein GPU / kein CUDA               →  Lite Profile (automatisch, kein Fehler)
```

Lite ist kein Fallback — es ist ein vollwertiges Profil für VPS und
GPU-lose VMs. Kein Ollama, nur Cloud-APIs.

## Installer-Schritte (entschieden)

1. OS-Check (Debian 12 / Ubuntu 22.04+ — andere ablehnen mit Hinweis)
2. GPU-Erkennung → Profil setzen, User informieren
3. System-Dependencies: python3, pip, git, samba, nginx
4. Conduit installieren + konfigurieren (Matrix-Homeserver)
5. AgentOS Core installieren (Python, Systemd-Unit)
6. Ollama installieren + Default-Modell ziehen *(Full only)*
7. Web-Console bauen + nginx einrichten (Port 443)
8. Admin-User anlegen (interaktiv: Name + Passwort)
9. Setup-Wizard im Browser öffnen

## Systemd-Services (entschieden)

| Service | Profil | Beschreibung |
|---|---|---|
| `agentos-core` | Lite + Full | Python FastAPI Core + Orchestrator |
| `agentos-conduit` | Lite + Full | Matrix-Homeserver |
| `agentos-console` | Lite + Full | Web-Console via nginx |
| `ollama` | Full only | Lokale LLM-Inference |

Alle Services: `Restart=always`, `RestartSec=5`, starten automatisch nach Boot.

## Default-Modell Full Profile (entschieden)

**GTX 1080 = 8GB VRAM** → `llama3.1:8b` mit Q4-Quantisierung (~4.7GB VRAM)

Ollama zieht das Modell beim ersten Start automatisch.
Weitere Modelle können nachträglich über die Webkonsole hinzugefügt werden.

Modell-Strategie pro Agent-Typ (Richtwert):
- Task-Agenten (ephemeral, schnell): `llama3.2:3b` (~2GB)
- Specialist-Agenten: `llama3.1:8b` (~4.7GB)
- Boss-Agent: Claude API (Reasoning-Qualität wichtiger als Kosten)

## Offene Punkte für Session 6
- QMD-Format: 1:1 von OpenClaw oder angepasst?
- Tool-Format: Wie sieht eine tool.yaml konkret aus?
- Tool-Implementierung: Wer stellt die Tools bereit — Core oder Agent?

---
*Stand: Session 5 — 19. März 2026*
