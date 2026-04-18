# HydraHive Console – Theme Setup

Das ist Schritt 1 des Redesigns: das **Design-System** als Tailwind-Theme.
Hier sind nur die Basis-Tokens (Farben, Fonts, Spacing) – die Komponenten
kommen in den nächsten Schritten.

## Was ist enthalten?

- `tailwind.config.js` – komplette Tailwind v3 Config mit dem HydraHive-Farbsystem
- `globals.css` – Base-Styles, Scrollbar, Component-Utilities (`.agent-card`,
  `.type-badge`, `.btn-primary`, etc.)

## Installation

### 1. In dein Repo kopieren

Die beiden Dateien gehören in `console/`:

```
console/
├── tailwind.config.js      ← ersetzen (oder neu anlegen)
└── src/
    └── globals.css         ← ersetzen (oder `index.css` umbenennen)
```

Falls `globals.css` bei dir anders heisst (z.B. `index.css`), übernimm einfach
den Inhalt und behalte deinen Dateinamen.

### 2. Fonts einbinden

Das Theme nutzt **Inter** für UI und **JetBrains Mono** für Code/Logs.
Füge das in dein `index.html` ein:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link
  href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
  rel="stylesheet"
>
```

Oder – besser für Self-Hosting – per `npm install @fontsource/inter @fontsource/jetbrains-mono`.

### 3. Dark-Mode aktivieren

In `index.html` am `<html>`-Tag:

```html
<html lang="de" class="dark">
```

(Die Konsole ist dark-only – falls du später Light-Mode willst, sagen wir Bescheid.)

### 4. Testen

```bash
cd console
npm run dev
```

Dein bisheriges UI sollte sich jetzt schon farblich verändert haben
(Hintergrund wird tiefes Indigo, Text wird hell). Die Komponenten sehen
aber noch nicht perfekt aus – das kommt im nächsten Schritt.

## Farb-System auf einen Blick

| Rolle                | Klasse           | Hex       | Verwendung                           |
| -------------------- | ---------------- | --------- | ------------------------------------ |
| Hintergrund          | `bg-surface-base`| `#0D0B1F` | Haupt-Canvas                         |
| Panel                | `bg-surface-raised` | `#14102A` | Sidebar, Topbar                  |
| Card                 | `bg-surface-card`| `#1A1633` | Cards, Inputs, Log-Panel             |
| Boss-Agent (Primary) | `bg-boss` / `text-boss` | `#7F77DD` | Boss-Agenten, Brand, Buttons  |
| Worker               | `bg-worker`      | `#5DCAA5` | Worker-Agenten, "aktiv"-Status       |
| Personal             | `bg-personal`    | `#ED93B1` | persönliche Agenten, "idle"          |
| Bridge               | `bg-bridge`      | `#EF9F27` | Support-Bots, Matrix-Bridges         |
| Primär-Text          | `text-ink`       | `#EEEDFE` | Standard-Text                        |
| Sekundär-Text        | `text-ink-muted` | `#B4B2A9` | Labels, Meta-Info                    |
| Hint-Text            | `text-ink-subtle`| `#888780` | Timestamps, disabled                 |

## Component-Utilities

Statt jedes Mal viele Klassen zu schreiben, kannst du diese Shortcuts nutzen:

```tsx
// Agent-Card
<div className="agent-card agent-card--boss">
  <span className="type-badge type-badge--boss">BOSS</span>
  <span className="status-dot status-dot--active" />
</div>

// Buttons
<button className="btn-primary">+ Neuer Agent</button>
<button className="btn-ghost">Filter</button>

// Input
<input className="input" placeholder="Agent-Name..." />
```

## Was kommt als nächstes?

**Schritt 2: Layout-Shell** – die Sidebar + Topbar + Content-Bereich als
React-Komponenten, responsive für Mobile und Desktop. Das ist das Gerüst,
in das die einzelnen Views reinkommen.

Sag Bescheid, wenn du mit Schritt 1 durch bist!
