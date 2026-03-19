# Session 1 — 19. März 2026

## Teilnehmer
- Till (Product Vision)
- Claude Web (Strukturierung & Architektur)

## Themen dieser Session

### Vision & Produkt
- Standalone Server-Produkt: Linux installieren → AgentOS drüber → fertig
- Alles über Webkonsole managebar — kein SSH, kein manuelles Config-Editieren
- Ziel: vermarktbares Produkt nach Rente
- "Proxmox für AI-Agenten" — Markt existiert noch kaum, Vorsprung möglich
- Till ist erster Kunde — baut für sich, testet real an eigener Infrastruktur

### Deployment
- Referenz-Setup: GTX 1080 PCIe-Passthrough auf Proxmox VM
- Lite-Profil: VM ohne GPU, nur Cloud-APIs
- Full-Profil: VM mit GPU-Passthrough, Ollama lokal
- Kein Docker wegen Netzwerkproblemen in VMs → Systemd-Services direkt auf Linux

### Agenten-Modell
- Skill-Pool-Modell: Agenten sind skill-gebunden, nicht projekt-gebunden
- Beispiele: Steuer-Agent, Medizin-Agent, Code-Agent, Research-Agent
- Jeder Agent hat Soul (Persona) + QMD-Skills (angelerntes Wissen)
- Boss-Agent pro Projekt koordiniert Worker (Beweis: Lilith funktioniert)
- Worker können parallel beauftragt werden (Swarm-Prinzip)
- Hybride Persistenz: Core-Agenten permanent, Task-Agenten ephemeral

### Projektverwaltung
- Projekt anlegen → Agenten zuweisen → die lauschen auf Projekt-Matrix-Room
- Kein Magic-Routing, explizite Konfiguration
- Linux-User pro Projekt für Isolation + Netzwerkfreigaben
- Samba/NFS direkt auf Linux-User-Berechtigungen — kein extra Usermanagement

### Kommunikations-Bus
- Matrix als Protokoll für Agent-zu-Agent-Kommunikation
- Empfehlung: Conduit (Rust, single binary, RocksDB, kein Postgres-Overhead)
- AgentLink bleibt State-Layer — wird erweitert, nicht ersetzt

### Von OpenClaw übernehmen
- Heartbeat (besser konfigurierbar)
- Sitzungskonzept
- Kanäle (→ Matrix-Rooms)
- Agenten-Config + Berechtigungskonzept (übersichtlicher gestalten)

### Neu bauen
- Admin-Oberfläche (OpenClaw UI ist Graus — kompletter Neustart)
- Installer (ein Befehl, GPU-Erkennung automatisch)
- Netzwerkfreigaben-Verwaltung aus Webkonsole

## Offene Fragen
- Tech-Stack Core-Runtime (TypeScript / Python / Go?)
- Monorepo oder separate Services?
- Agent-Konfigurationsformat (YAML/JSON/TOML?)
- User-Support Client-Flow
- Modell-Strategie (lokal vs. Cloud, Kostenmodell)
- Monetarisierung (Community vs. Pro Edition?)
