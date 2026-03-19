# Entschiedene Punkte

> Nur was wirklich entschieden ist. Kein "vielleicht", kein "offen".  
> Basis für spätere Claude Code Prompts.

---

## Produkt

| # | Entscheidung | Begründung |
|---|---|---|
| P1 | Standalone Server-Produkt | Install Linux → AgentOS → fertig |
| P2 | Webkonsole ist das Produkt | Kein SSH, kein Config-Editieren |
| P3 | Zwei Deployment-Profile: Lite + Full | Lite = kein GPU, Full = GPU-Passthrough |
| P4 | Till ist Referenz-Kunde | TrueNAS/Proxmox Setup mit GTX 1080 |

## Infrastruktur

| # | Entscheidung | Begründung |
|---|---|---|
| I1 | Kein Docker | Netzwerkprobleme in VMs zu fehleranfällig |
| I2 | Systemd-Services direkt auf Linux | Transparent, debuggbar, kein virtuelles Netz |
| I3 | Linux-User pro Projekt | Isolation + Netzwerkfreigaben über OS-Berechtigungen |
| I4 | Samba/NFS auf Linux-User-Basis | Kein extra Usermanagement nötig |

## Architektur

| # | Entscheidung | Begründung |
|---|---|---|
| A1 | Matrix als Kommunikations-Bus | Standard-Protokoll, Client-Apps verfügbar |
| A2 | Conduit als Matrix-Homeserver | Single binary, RocksDB, kein Postgres |
| A3 | AgentLink bleibt State-Layer | Bereits validiert, wird erweitert nicht ersetzt |
| A4 | 5-Schichten-Modell | Client → Management → Bus → Runtime → LLM |

## Agenten-Modell

| # | Entscheidung | Begründung |
|---|---|---|
| AG1 | Skill-Pool-Modell | Agenten skill-gebunden, nicht projekt-gebunden |
| AG2 | Boss-Agent pro Projekt | Koordiniert Swarm, bewährt durch Lilith |
| AG3 | Hybride Persistenz | Core permanent, Task-Agenten ephemeral |
| AG4 | Soul + QMD Skills | Von OpenClaw übernehmen |
| AG5 | Projektverwaltung mit Agenten-Zuweisung | Explizit, kein Magic-Routing |

## LLM-Anbindung

| # | Entscheidung | Begründung |
|---|---|---|
| L1 | Multi-LLM pro Agent konfigurierbar | Ollama + Claude API + OpenAI + weitere |
| L2 | Graceful Degradation ohne GPU | Cloud-only Mode wenn kein GPU |

## Von OpenClaw übernehmen

| # | Komponente | Änderung |
|---|---|---|
| O1 | Heartbeat | Besser konfigurierbar (Interval, Timeout, Fallback) |
| O2 | Sitzungskonzept | Unverändert übernehmen |
| O3 | Kanäle | → Matrix-Rooms |
| O4 | Agenten-Config + Berechtigungen | Übersichtlicher gestalten |

---
*Zuletzt aktualisiert: Session 1 — 19. März 2026*
