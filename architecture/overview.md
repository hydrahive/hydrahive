# Architektur-Übersicht

## 5-Schichten-Modell

```
┌─────────────────────────────────────────────────────┐
│  CLIENT LAYER                                       │
│  Web-Chat UI · REST/WebSocket API · Matrix-Bridge   │
│  CLI/Dev-Tools                                      │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│  MANAGEMENT LAYER                                   │
│  Projekt-Manager · Agenten-Config-UI                │
│  Monitoring/Logs · Auth/RBAC                        │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│  KOMMUNIKATIONS-BUS                                 │
│  Conduit (Matrix) · AgentLink State Transfer        │
│  Redis Pub/Sub · Event Sourcing                     │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│  CORE RUNTIME                                       │
│  Agent-Orchestrator · Task-Queue (ephemeral)        │
│  Plugin-System · Memory/Qdrant                      │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│  LLM-ADAPTER-LAYER                                  │
│  Ollama (lokal/GPU) · Claude API (OAuth)            │
│  OpenAI/ChatGPT · weitere Ollama-kompatible         │
└─────────────────────────────────────────────────────┘
```

## Projekt-Modell

```
Projekt anlegen
    │
    ├── Linux-User erstellen (proj_name)
    ├── Heimverzeichnis /projects/name
    ├── Samba-Share automatisch einrichten
    ├── Matrix-Room erstellen
    └── Agenten zuweisen
            │
            ├── Boss-Agent (koordiniert Swarm)
            └── Worker-Agenten (Skill-Pool)
```

## Agent-Typen

### Core-Agenten (permanent)
- Eigener Matrix-User
- Lauschen dauerhaft auf zugewiesene Rooms
- Beispiele: Boss-Agent, Spezialist-Agenten

### Task-Agenten (ephemeral)
- On-demand gespawnt
- Erben Context vom rufenden Agenten
- Sterben nach Task-Completion
- State via AgentLink zurückgeschrieben

## Deployment

### Lite (ohne GPU)
- VM ohne PCIe-Passthrough
- Nur Cloud-APIs (Claude, ChatGPT)
- Ideal für VPS oder Test-Umgebungen

### Full (mit GPU)
- VM mit PCIe GPU-Passthrough
- Ollama mit CUDA lokal
- Referenz: GTX 1080 auf Proxmox

## Services (Systemd, kein Docker)

| Service | Technologie | Port |
|---|---|---|
| Matrix Homeserver | Conduit (Rust) | 8448 |
| AgentLink Backend | FastAPI + PostgreSQL | 8000 |
| State Store | Redis | 6379 |
| Vector DB | Qdrant / ChromaDB | 6333 |
| Local LLM | Ollama | 11434 |
| AgentOS Core | TBD | TBD |
| Web Console | TBD | 443 |

---
*Stand: Session 1 — 19. März 2026*
