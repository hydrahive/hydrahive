# Redesign v1 — April 2026

## Kontext

Claude Web hat Anfang April 2026 ein „greenfield" Theme entworfen: semantische
Agent-Type-Farben (Boss lila, Worker teal, Personal pink, Bridge amber), kompakte
Admin-UI-Typografie-Scale, dezente Surface-Palette für eine dark-only Admin-
Console.

Das Repo ist kein Greenfield: es hat bereits ein shadcn-artiges Design-System
mit HSL-CSS-Variablen (`--primary`, `--card`, `--radius`), Light-**und**-Dark-
Mode, Custom-Components (`.app-shell`, `.app-sidebar`, `.app-panel`, `.hero-panel`,
`.metric-card`, `.status-pill`) und den AdminFun-Beat-Visuals am `body::after`,
`aside`, `svg`, `h1/h2`, `main`, `.adminfun-brain-stage`.

Ein Drop-in-Overwrite hätte all das zerschossen.

## Entscheidung: additiver Minimal-Einbau

Umgesetzt wurde **nur** das Nicht-Konfligierende:

- Neue Farb-Scales in `tailwind.config.js`: `surface.*`, `boss.*`, `worker.*`,
  `personal.*`, `bridge.*`, `ink.*`. Zusätzlich zu den bestehenden HSL-Vars.
- `boxShadow.card/pop/focus` und `spacing.sidebar/sidebar-sm` additiv.
- **Nicht übernommen:** `fontFamily` (würde globale Default-Font ändern ohne
  Font-Loader), `fontSize` (Scale-Shift im ganzen UI), `borderRadius`
  (würde `--radius`-CSS-Var-System brechen).
- Utility-Klassen aus dem Quell-Theme (`.agent-card`, `.type-badge`,
  `.status-dot`, `.btn-ghost`, sowie umbenannt `.btn-boss`, `.input-boss`)
  liegen in **`src/styles/theme-next.css`** — **opt-in**, wird nicht
  automatisch geladen.
- **Keine Base-Layer-Overrides:** kein neuer Body-Background, kein globaler
  Focus-Ring, keine neue Scrollbar-Regel — würde AdminFun und Light-Mode
  brechen.

## Namenskollisionen, die zum Umbenennen geführt haben

Das Quell-Theme hatte `.btn-primary` und `.input`. Beide sind zu generisch:

| Quell-Name | Neuer Name | Grund |
|---|---|---|
| `.btn-primary` | `.btn-boss` | `ExtensionsPage.tsx` + `CodeEditorPage.tsx` nutzen schon `className="btn btn-primary"` (ohne eigene CSS-Definition). Neue Klasse mit altem Namen hätte unerwartetes Styling erzeugt. |
| `.input` | `.input-boss` | Zu generisch; Risiko von Styling-Leaks in bestehende `<input>`-Elemente. |

## Wie neue Components das Theme nutzen

Farb-Tokens direkt per Tailwind-Utility — **kein Import nötig**:

```tsx
<div className="bg-surface-card border border-surface-border rounded-md p-3">
  <span className="text-ink font-medium">Worker Alice</span>
  <span className="text-boss-soft ml-2">Boss Lena</span>
</div>
```

Utility-Klassen aus `theme-next.css` — **Opt-in per Import**:

```tsx
import "../styles/theme-next.css";

<button className="btn-boss">Run</button>
<div className="agent-card agent-card--worker">…</div>
```

Der Import kann pro Component oder zentral in einer Shell-Component erfolgen.

## Migrations-Pfad (später, falls gewünscht)

Wenn genug neue Components auf den neuen Tokens laufen, kann das shadcn-HSL-
System schrittweise abgelöst werden. Das ist bewusst **kein** Teil dieses
Schritts.

Mögliche nächste Schritte (separat zu entscheiden):

- Mobile-Chat-Fix (Viewport + Input-Anker) — hat Priorität vor Polishing
- Layout-Shell-Refactor mit `spacing.sidebar`
- Agent-Card-Component mit `.agent-card--*`-Klassen

## Archiv

- [`_source-theme.js`](./_source-theme.js) — Original-Quell-Config (CJS-Form) vom Claude-Web-Entwurf, **unbenutzt**
- [`_source-globals.css`](./_source-globals.css) — Original-Quell-CSS mit Base-Layer + Utility-Klassen, **unbenutzt**
- [`_source-README.md`](./_source-README.md) — Original-Anleitung des Quell-Themes

Der Unterstrich-Prefix signalisiert: **nicht importieren, nicht builden**. Sie
liegen hier nur als Kontext-Referenz für künftige Redesigns.
