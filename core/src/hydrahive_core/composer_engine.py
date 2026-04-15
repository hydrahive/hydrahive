"""AGENT.md Profile-Composer (#645 Phase 1b + 1c).

Stellt einen Katalog von Persona-Bausteinen bereit, aus dem User im
Personal-Agent-Composer ihre AGENT.md zusammenklicken können.

Phase 1c: Presets + Konfliktregeln + Profile-Persistenz (agent_profile.yaml).
Keine KI-Generierung — jeder Block liefert festen Markdown-Text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from pydantic import BaseModel, Field


CURRENT_SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class BlockDef:
    id: str
    label: str
    description: str
    markdown: str


@dataclass(frozen=True)
class CategoryDef:
    id: str
    label: str
    blocks: tuple[BlockDef, ...] = field(default_factory=tuple)


BLOCK_CATALOG: tuple[CategoryDef, ...] = (
    CategoryDef(
        id="work_style",
        label="Arbeitsstil",
        blocks=(
            BlockDef(
                id="work_style.precise",
                label="Präzise & Minimal",
                description="Kleine Schritte, nichts Überflüssiges, keine Scope-Ausweitung.",
                markdown=(
                    "- Arbeite in kleinen, fokussierten Schritten.\n"
                    "- Keine Scope-Ausweitung: genau das tun, was gefragt ist.\n"
                    "- Keine vorgezogenen Refactorings, keine spekulativen Abstraktionen."
                ),
            ),
            BlockDef(
                id="work_style.plan_first",
                label="Plan vor Umsetzung",
                description="Bei nicht-trivialen Aufgaben erst kurz den Plan klären.",
                markdown=(
                    "- Bei nicht-trivialen Aufgaben erst einen kurzen Plan präsentieren "
                    "(Ziel, Schritte, Risiken) und Freigabe abwarten."
                ),
            ),
            BlockDef(
                id="work_style.ask_when_unsure",
                label="Nachfragen statt raten",
                description="Lieber eine kurze Rückfrage als eine falsche Annahme.",
                markdown=(
                    "- Bei Unklarheit eine gezielte Rückfrage stellen, statt eine Annahme zu treffen.\n"
                    "- Ehrlich sagen wenn etwas unklar ist, nicht raten."
                ),
            ),
            BlockDef(
                id="work_style.no_pragmatic_shortcuts",
                label="Keine Schnellschüsse",
                description="Keine „pragmatischen“ Abkürzungen — Ursachen fixen, nicht Symptome.",
                markdown=(
                    "- Keine „pragmatischen“ Schnellschüsse. Lieber kurz richtig planen.\n"
                    "- Root-Cause fixen statt Symptome umgehen (kein `--no-verify`, keine Feature-Flags als Workaround)."
                ),
            ),
        ),
    ),
    CategoryDef(
        id="safety",
        label="Sicherheits- & Freigabe-Verhalten",
        blocks=(
            BlockDef(
                id="safety.confirm_destructive",
                label="Destruktive Aktionen bestätigen",
                description="Löschen, Force-Push, DROP etc. nur mit expliziter Bestätigung.",
                markdown=(
                    "- Destruktive Aktionen (rm -rf, git reset --hard, force-push, DROP TABLE, "
                    "kill -9, ...) erst nach expliziter Bestätigung ausführen."
                ),
            ),
            BlockDef(
                id="safety.prod_hands_off",
                label="Produktivsysteme tabu",
                description="Produktivsysteme nur mit ausdrücklicher Anweisung anfassen.",
                markdown=(
                    "- Produktivsysteme werden ohne ausdrückliche Anweisung nicht verändert.\n"
                    "- Lieber beschreiben, was zu tun wäre, als ungefragt deployen."
                ),
            ),
            BlockDef(
                id="safety.no_ssh_hotfix",
                label="Kein SSH-Hotfix",
                description="Server nicht direkt patchen, immer über Repo + Deploy-Pipeline.",
                markdown=(
                    "- Server nicht direkt per SSH patchen. Änderungen laufen durch "
                    "Commit → Push → Deploy-Pipeline."
                ),
            ),
            BlockDef(
                id="safety.read_only_default",
                label="Read-Only als Default",
                description="Lesen ist sicher, Schreiben braucht einen guten Grund.",
                markdown=(
                    "- Bei Unklarheit erst lesen und verstehen, nicht schreiben.\n"
                    "- Schreibende Aktionen brauchen einen klaren, benannten Grund."
                ),
            ),
        ),
    ),
    CategoryDef(
        id="git",
        label="Git- & Arbeitsdisziplin",
        blocks=(
            BlockDef(
                id="git.small_focused_commits",
                label="Kleine fokussierte Commits",
                description="Ein Thema pro Commit, präzise Commit-Messages.",
                markdown=(
                    "- Ein Thema pro Commit. Keine Sammel-Commits mit mehreren Anliegen.\n"
                    "- Commit-Message: kurzer Imperativ-Titel, Body erklärt das *Warum*."
                ),
            ),
            BlockDef(
                id="git.no_force_push_main",
                label="Kein Force-Push auf main",
                description="Hauptbranches nicht überschreiben.",
                markdown=(
                    "- Kein force-push auf `main`/`master`. Bei Konflikten: mergen oder rebasen, nicht überschreiben."
                ),
            ),
            BlockDef(
                id="git.scope_check_before_commit",
                label="Scope-Check vor Commit",
                description="git status prüfen, nur erwartete Dateien stagen.",
                markdown=(
                    "- Vor jedem Commit `git status` prüfen und nur erwartete Dateien stagen.\n"
                    "- Keine `git add -A` ohne Review, keine ungewollten Artefakte mitnehmen."
                ),
            ),
            BlockDef(
                id="git.verify_before_done",
                label="Live-Verify statt „funktioniert lokal“",
                description="Nach Deploy echte Funktionsnachweise, nicht nur Service-Status.",
                markdown=(
                    "- „Funktioniert lokal“ ist kein Abschluss. Nach Deploy die Änderung real verifizieren "
                    "(Endpoint, Bundle-Grep, UI-Klick)."
                ),
            ),
        ),
    ),
    CategoryDef(
        id="docs",
        label="Doku & Abschlussbericht",
        blocks=(
            BlockDef(
                id="docs.summary_after_change",
                label="Kurzer Abschlussbericht",
                description="Nach jeder Änderung: was wurde gemacht, was ist offen.",
                markdown=(
                    "- Am Ende einer Aufgabe ein kurzer Bericht: was geändert, was geprüft, was bleibt offen."
                ),
            ),
            BlockDef(
                id="docs.why_not_what",
                label="Warum, nicht was",
                description="Kommentare und Commit-Bodies erklären den Kontext.",
                markdown=(
                    "- Kommentare und Commit-Bodies erklären das *Warum*, nicht das *Was* — den Code lesen kann man selbst."
                ),
            ),
            BlockDef(
                id="docs.keep_docs_current",
                label="Doku mit Code aktuell halten",
                description="Features ohne aktualisierte Doku sind nicht fertig.",
                markdown=(
                    "- Feature-Änderungen ziehen die Doku im selben Commit nach.\n"
                    "- Kein Auseinanderdriften von Code und Handbuch."
                ),
            ),
        ),
    ),
    CategoryDef(
        id="comm",
        label="Kommunikation",
        blocks=(
            BlockDef(
                id="comm.german_default",
                label="Deutsch als Standard",
                description="Antworten auf Deutsch, außer explizit anders gewünscht.",
                markdown=(
                    "- Standard-Sprache ist Deutsch. Wechsel nur, wenn der User das explizit so macht."
                ),
            ),
            BlockDef(
                id="comm.concise",
                label="Knapp & sachlich",
                description="Auf den Punkt, ohne Füllsätze oder Eigenwerbung.",
                markdown=(
                    "- Antworten auf den Punkt, ohne Füllsätze, ohne „Gerne helfe ich dir weiter“-Floskeln."
                ),
            ),
            BlockDef(
                id="comm.no_goodbye_loop",
                label="Kein Goodbye-Loop",
                description="Einmal „fertig“ reicht — keine Verabschiedungs-Schleifen.",
                markdown=(
                    "- Einmal abschließen reicht. Keine wiederholten Verabschiedungen oder Bestätigungsloops."
                ),
            ),
            BlockDef(
                id="comm.client_polite",
                label="Kundenmodus: freundlich-förmlich",
                description="Bei Kundenkontakt freundlicher, strukturierter Ton.",
                markdown=(
                    "- Bei Kundenkontakt: freundlicher, strukturierter Ton, Höflichkeitsform, "
                    "klare nächste Schritte."
                ),
            ),
        ),
    ),
)


_BLOCK_INDEX: dict[str, BlockDef] = {
    b.id: b for cat in BLOCK_CATALOG for b in cat.blocks
}


def list_blocks() -> list[dict]:
    """Katalog-Struktur für API-Responses — serialisierbar."""
    return [
        {
            "id": cat.id,
            "label": cat.label,
            "blocks": [
                {"id": b.id, "label": b.label, "description": b.description}
                for b in cat.blocks
            ],
        }
        for cat in BLOCK_CATALOG
    ]


def known_block_ids() -> set[str]:
    return set(_BLOCK_INDEX.keys())


def render_agent_md(selected_ids: Iterable[str]) -> str:
    """Baut aus ausgewählten Block-IDs eine Markdown-AGENT.md.

    Unbekannte IDs werden ignoriert. Reihenfolge folgt dem Katalog, nicht
    der Reihenfolge der Eingabe — damit bleibt die AGENT.md bei gleicher
    Auswahl stabil.
    """
    selected = {sid for sid in selected_ids if sid in _BLOCK_INDEX}
    if not selected:
        return ""

    out: list[str] = ["# Persönliches Agent-Profil", ""]
    out.append(
        "Dieses Profil wurde über den HydraHive Composer erzeugt. "
        "Es beschreibt, wie dieser Agent arbeiten soll."
    )
    out.append("")

    for cat in BLOCK_CATALOG:
        cat_blocks = [b for b in cat.blocks if b.id in selected]
        if not cat_blocks:
            continue
        out.append(f"## {cat.label}")
        out.append("")
        for b in cat_blocks:
            out.append(b.markdown)
            out.append("")

    return "\n".join(out).rstrip() + "\n"


# ===========================================================================
# Phase 1c — Presets, Conflict-Rules, Profile-Modell
# ===========================================================================


class AgentProfile(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    preset: Optional[str] = None
    selected: list[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class PresetDef:
    id: str
    label: str
    description: str
    selected: tuple[str, ...]


PRESETS: tuple[PresetDef, ...] = (
    PresetDef(
        id="read_only_auditor",
        label="Read-only Auditor",
        description="Prüft, dokumentiert, verändert nichts ungefragt.",
        selected=(
            "work_style.ask_when_unsure",
            "work_style.plan_first",
            "safety.read_only_default",
            "safety.prod_hands_off",
            "safety.no_ssh_hotfix",
            "safety.confirm_destructive",
            "docs.summary_after_change",
            "comm.concise",
            "comm.german_default",
        ),
    ),
    PresetDef(
        id="trusted_admin",
        label="Trusted Admin",
        description="Darf selbstständig arbeiten, hält aber Disziplin und Abschlussberichte ein.",
        selected=(
            "work_style.precise",
            "work_style.no_pragmatic_shortcuts",
            "safety.confirm_destructive",
            "safety.prod_hands_off",
            "git.small_focused_commits",
            "git.no_force_push_main",
            "git.scope_check_before_commit",
            "git.verify_before_done",
            "docs.summary_after_change",
            "docs.why_not_what",
            "docs.keep_docs_current",
            "comm.concise",
            "comm.german_default",
        ),
    ),
)


_PRESET_INDEX: dict[str, PresetDef] = {p.id: p for p in PRESETS}


def list_presets() -> list[dict]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "selected": list(p.selected),
        }
        for p in PRESETS
    ]


def known_preset_ids() -> set[str]:
    return set(_PRESET_INDEX.keys())


def preset_selection(preset_id: str) -> list[str]:
    """Liefert die Block-IDs eines Presets in Katalog-Reihenfolge."""
    p = _PRESET_INDEX.get(preset_id)
    if p is None:
        return []
    return list(p.selected)


# ---------------------------------------------------------------------------
# Konfliktregeln
# ---------------------------------------------------------------------------


def _rule_preset_drift(selected: list[str], preset: Optional[str]) -> list[dict]:
    if not preset:
        return []
    p = _PRESET_INDEX.get(preset)
    if p is None:
        return []
    if set(selected) == set(p.selected):
        return []
    return [{
        "rule": "preset_drift",
        "severity": "info",
        "message": (
            f"Auswahl weicht vom Preset '{p.label}' ab. "
            "Beim Speichern wird die aktuelle Auswahl übernommen."
        ),
        "block_ids": sorted(set(selected) ^ set(p.selected)),
    }]


def _rule_empty_selection_with_preset(selected: list[str], preset: Optional[str]) -> list[dict]:
    if preset and not selected:
        return [{
            "rule": "empty_selection_with_preset",
            "severity": "error",
            "message": "Preset gesetzt, aber keine Bausteine ausgewählt. Preset entfernen oder Bausteine wählen.",
            "block_ids": [],
        }]
    return []


def _rule_read_only_incomplete(selected: list[str], _preset: Optional[str]) -> list[dict]:
    ro = "safety.read_only_default" in selected
    prod = "safety.prod_hands_off" in selected
    if ro ^ prod:
        missing = "safety.prod_hands_off" if ro else "safety.read_only_default"
        return [{
            "rule": "read_only_incomplete",
            "severity": "warning",
            "message": (
                "Read-only-Haltung ist nur wirksam, wenn auch Produktivsysteme geschützt sind "
                f"('{missing}' fehlt)."
            ),
            "block_ids": [missing],
        }]
    return []


def _rule_deploy_discipline_partial(selected: list[str], _preset: Optional[str]) -> list[dict]:
    if "git.verify_before_done" in selected and "docs.summary_after_change" not in selected:
        return [{
            "rule": "deploy_discipline_partial",
            "severity": "warning",
            "message": "Live-Verify ohne Abschlussbericht ist schwer nachvollziehbar — 'docs.summary_after_change' ergänzen.",
            "block_ids": ["docs.summary_after_change"],
        }]
    return []


CONFLICT_RULES = (
    _rule_preset_drift,
    _rule_empty_selection_with_preset,
    _rule_read_only_incomplete,
    _rule_deploy_discipline_partial,
)


def evaluate_warnings(selected: list[str], preset: Optional[str]) -> list[dict]:
    """Wendet alle Konfliktregeln an und liefert Liste von Warnings."""
    warnings: list[dict] = []
    for rule in CONFLICT_RULES:
        warnings.extend(rule(selected, preset))
    return warnings


def save_blocked(warnings: list[dict]) -> bool:
    return any(w.get("severity") == "error" for w in warnings)


def render_from_profile(profile: AgentProfile) -> str:
    """Thin wrapper: rendert AGENT.md aus einem AgentProfile."""
    return render_agent_md(profile.selected)
