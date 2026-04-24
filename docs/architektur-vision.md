# HydraHive — Architektur-Vision: Projekte + Agenten

> Dieses Dokument beschreibt das Ziel-Design des Systems und dient als
> Referenz für alle Entwicklungsarbeiten. Letzte Aktualisierung: 2026-04-24.

---

## Die Grundidee in einem Satz

Jedes Projekt hat **einen Master-Agenten** der das Projekt kennt und steuert.
Dieser Master kann sich bei Bedarf **Spezialisten** aus einem globalen Pool holen
und parallel an Aufgaben arbeiten lassen.

---

## Das Konzept — bildlich erklärt

```
Till: "Ich will eine Rezeptdatenbank bauen"
  │
  └──► Projekt "rezepdb" anlegen
         │
         └──► Master-Agent "rezepdb" wird automatisch miterstellt
                │  (kennt das Projekt, hat Zugriff auf alle Projekt-Dateien,
                │   Git, Gitea, Samba, Memory des Projekts)
                │
                ├──► holt sich: db-agent       → baut die Datenbank
                ├──► holt sich: doku-agent     → schreibt die Dokumentation
                └──► holt sich: rezept-agent   → beschafft Rezepte + Design

                      (alle drei arbeiten gleichzeitig, parallel)
```

**Analogie aus der echten Welt (2026-04-24):**
- HydraHive = das Projekt
- Claude (ich) = der Master-Agent
- OpenClaw = der spezialisierte Worker-Agent

---

## Rollen erklärt

### Master-Agent (Projekt-Boss)
- Wird **automatisch** beim Anlegen eines Projekts erstellt
- Heißt gleich wie das Projekt (z.B. Projekt `rezepdb` → Agent `rezepdb`)
- Hat vollen Zugriff auf sein Projekt: Dateien, Git, Gitea, Samba, Memory
- Kennt das Ziel des Projekts (steht in seiner `soul.md`)
- Kann Spezialisten beauftragen und ihre Ergebnisse zusammenführen
- Ist der einzige Agent der direkt mit Till chattet (im Projekt-Chat)

### Spezialist-Agent (Worker)
- Existiert **einmal global**, wird von vielen Projekten genutzt
- Hat eine klar definierte Spezialität (nur Coding, nur Doku, nur Rezepte...)
- Bekommt Aufgaben vom Master, liefert Ergebnisse zurück
- Kein eigenes Projekt, kein direkter Chat mit Till (normalerweise)
- Kann parallel in mehreren Projekten eingesetzt werden

### Personal-Agent
- Einer pro Nutzer (`personal_<username>`)
- Für persönliche Aufgaben die kein Projekt-Kontext brauchen
- Zugriff über "Mein Agent" in der Navigation

---

## Aktueller Zustand (2026-04-24)

### Was schon funktioniert ✅
- Agenten anlegen und verwalten (AgentsPage)
- Projekte anlegen und verwalten
- Boss-Agent einem Projekt zuweisen
- Teams definieren (welche Agenten zusammenarbeiten dürfen)
- `dispatch_task_dag` — parallele Aufgaben an mehrere Agenten senden
- `ask_agent` — Agent-zu-Agent Kommunikation
- Per-Agent Memory, Soul, Skills

### Was noch fehlt / kaputt ist ❌
1. **Projekt erstellen → kein Auto-Agent** — man muss Agent und Projekt separat anlegen und dann manuell verknüpfen (umständlich, verwirrend)
2. **Kein Spezialist-Pool in der UI** — man sieht nicht welche Agenten "globale Spezialisten" sind
3. **Team-Zuweisung umständlich** — man muss extra in die Teams-Seite gehen statt direkt im Projekt
4. **Handbuch veraltet** — beschreibt alten Stand, verwirrt mehr als es hilft
5. **Projekte auf .177 ohne Boss** — bestehende Projekte haben keinen Agent zugewiesen

---

## Implementierungsplan

### Phase 1 — Projekt + Agent automatisch koppeln
**Ziel:** Ein Klick "Neues Projekt" → Projekt UND Master-Agent werden erstellt, Boss wird automatisch gesetzt.

- [ ] Backend: `POST /admin/projects` erstellt automatisch einen gleichnamigen Agent in `/agents/<project-id>/`
- [ ] Backend: `agents.boss` wird direkt auf den neuen Agent gesetzt
- [ ] Frontend: Formular "Neues Projekt" hat optionales Feld "Master-Agent Name" (Default = Projekt-ID)
- [ ] Frontend: Anzeige im Projekt-Header welcher Agent aktiv ist ("Gesteuert von: rezepdb")
- [ ] Migration: Bestehende Projekte ohne Boss können über einen Button in der UI nachträglich einen Agent bekommen

### Phase 2 — Spezialist-Pool
**Ziel:** Globale Spezialisten anlegen, die allen Projekten zur Verfügung stehen.

- [ ] `agent.yaml` bekommt Feld `specialist: true` und `specialty: "coding"` (oder ähnlich)
- [ ] AgentsPage zeigt zwei Sektionen: "Projekt-Master" und "Spezialisten"
- [ ] Default-Spezialisten werden beim ersten Start automatisch angelegt (siehe Katalog unten)
- [ ] Im Projekt: Tab "Team" zeigt welche Spezialisten verfügbar sind und ermöglicht Zuweisung per Klick

### Phase 3 — Delegation in der Praxis
**Ziel:** Master kann aus dem Chat heraus Spezialisten beauftragen, Ergebnisse kommen zusammen.

- [ ] Spezialist-Liste ist Teil des System-Prompts des Masters ("Du hast Zugriff auf: coder-agent, doku-agent...")
- [ ] `dispatch_task_dag` ist standardmäßig im Tool-Set aller Master-Agenten
- [ ] Im Chat: Ergebnisse von Sub-Agenten werden sichtbar angezeigt (wer hat was gemacht)
- [ ] Spezialist-Ausgaben landen optional als Dateien im Projekt-Workspace

### Phase 4 — Dokumentation aufräumen
**Ziel:** Till und andere Nutzer können jederzeit nachschlagen wie alles funktioniert.

- [ ] `docs/handbook.md` komplett neu schreiben — einfach, klar, kein Entwickler-Jargon
- [ ] Kernkonzepte mit Beispielen (genau wie das Rezepte-Beispiel oben)
- [ ] "Mein erster Agent" Quickstart (5 Minuten, fertig)
- [ ] Regel: Jede Code-Änderung kommt mit Handbuch-Update im selben Commit

---

## Spezialist-Katalog (geplant)

Diese Agenten sollen im System vorinstalliert sein und sofort nutzbar:

| Agent-ID | Spezialität | Fähigkeiten |
|---|---|---|
| `coder` | Programmierung | Code schreiben, debuggen, refactorn, alle Sprachen |
| `reviewer` | Code-Review | Code prüfen, Sicherheit, Best Practices, Feedback |
| `doku` | Dokumentation | READMEs, Handbücher, API-Docs, Kommentare schreiben |
| `tester` | Testing | Tests schreiben, Testpläne, Fehler reproduzieren |
| `researcher` | Recherche | Web-Suche, Zusammenfassen, Quellen prüfen |
| `designer` | Design & UX | UI-Konzepte, Farben, Layout-Ideen, Nutzererfahrung |
| `dba` | Datenbanken | Schema-Design, Queries, Migration, Optimierung |
| `devops` | Infrastruktur | Server, Docker, CI/CD, Deployment |
| `analyst` | Datenanalyse | Zahlen auswerten, Reports, Visualisierungen |
| `chef` | Rezepte & Küche | Rezepte, Einkaufslisten, Küchenplanung (Beispiel ^^) |

---

## Mentales Modell für Till

```
Wenn ich ein neues Projekt starte:
1. Neues Projekt anlegen → Master-Agent wird automatisch erstellt
2. Im Projekt-Chat mit dem Master sprechen
3. Master holt sich selbst die richtigen Spezialisten
4. Ich sehe was jeder macht und das Ergebnis kommt zusammen

Spezialisten muss ich nur einmal anlegen — dann stehen sie für immer bereit.
```

---

## Verwandte Dokumente
- `docs/handbook.md` — Benutzerhandbuch (in Überarbeitung)
- `docs/ARCHITECTURE.md` — Technische Architektur
- GitHub Issues: #886ff (Implementierungstickets zu diesem Plan)
