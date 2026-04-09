"""
coordinator_mode.py — Multi-Agent Worker-Orchestrierung (#484)

Boss analysiert komplexe Aufgaben und erstellt automatisch einen Workplan:
1. Aufgabe in Teilaufgaben zerlegen
2. Passende Worker zuweisen (Built-in oder Projekt-Worker)
3. Abhängigkeiten erkennen (DAG)
4. Worker parallel dispatchen
5. Ergebnisse sammeln und synthetisieren

Wird als Tool "coordinate" dem Boss zur Verfügung gestellt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class WorkplanStep:
    """Ein Schritt im Workplan."""
    id: str
    task: str
    worker_id: str
    depends_on: list[str] = field(default_factory=list)
    context: str = ""


@dataclass
class Workplan:
    """Automatisch generierter Plan für eine komplexe Aufgabe."""
    goal: str
    steps: list[WorkplanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dispatches(self) -> list[dict]:
        """Konvertiert in das dispatch_task Format."""
        return [
            {
                "worker_id": step.worker_id,
                "task": step.task,
                "context": step.context,
                "task_id": step.id,
                "depends_on": step.depends_on,
            }
            for step in self.steps
        ]

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [
                {"id": s.id, "task": s.task, "worker_id": s.worker_id,
                 "depends_on": s.depends_on, "context": s.context}
                for s in self.steps
            ],
        }


async def create_workplan(
    orch,
    boss_cfg,
    goal: str,
    available_workers: list[str],
) -> Workplan:
    """
    Lässt den Boss-LLM einen Workplan für eine komplexe Aufgabe erstellen.

    Der LLM bekommt die verfügbaren Worker und soll die Aufgabe in
    Teilschritte zerlegen mit Worker-Zuweisung und Abhängigkeiten.
    """
    from .built_in_workers import list_builtin_workers

    builtin = list_builtin_workers()
    all_workers = [
        {"id": w["id"], "name": w["name"], "description": w["description"]}
        for w in builtin
    ] + [
        {"id": w, "name": w, "description": "Projekt-Worker"}
        for w in available_workers
    ]

    workers_desc = "\n".join(
        f"- **{w['id']}**: {w['description']}"
        for w in all_workers
    )

    prompt = [
        {"role": "system", "content": (
            "Du bist ein Koordinator der komplexe Aufgaben in Teilschritte zerlegt.\n\n"
            "Verfügbare Worker:\n" + workers_desc + "\n\n"
            "Erstelle einen JSON-Workplan mit diesem Format:\n"
            '{"steps": [{"id": "step1", "task": "Was tun", "worker_id": "explore", '
            '"depends_on": [], "context": "Zusatzinfo"}]}\n\n'
            "Regeln:\n"
            "- Nutze 'explore' für Recherche/Code-Suche\n"
            "- Nutze 'plan' für Architektur-Planung\n"
            "- Nutze 'verify' für Tests/Validation\n"
            "- Nutze 'review' für Code-Review\n"
            "- Nutze Projekt-Worker für spezifische Aufgaben\n"
            "- Maximal 5 Steps\n"
            "- Abhängigkeiten als depends_on Array\n"
            "- Antworte NUR mit dem JSON, kein anderer Text"
        )},
        {"role": "user", "content": goal},
    ]

    try:
        import litellm
        from .orchestrator_llm import _llm_with_retry

        resp = await _llm_with_retry(lambda: litellm.acompletion(
            model="claude-haiku-4-5-20251001",
            messages=prompt,
            max_tokens=2000,
            temperature=0,
            drop_params=True,
        ))
        raw = resp.choices[0].message.content or ""

        # JSON extrahieren
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        steps = [
            WorkplanStep(
                id=s.get("id", f"step{i}"),
                task=s["task"],
                worker_id=s.get("worker_id", "explore"),
                depends_on=s.get("depends_on", []),
                context=s.get("context", ""),
            )
            for i, s in enumerate(data.get("steps", []))
        ]

        plan = Workplan(goal=goal, steps=steps[:5])
        logger.info("Workplan erstellt: %d Steps für '%s'", len(plan.steps), goal[:60])
        return plan

    except Exception as e:
        logger.warning("Workplan-Erstellung fehlgeschlagen: %s — Fallback auf explore", e)
        return Workplan(
            goal=goal,
            steps=[WorkplanStep(id="fallback", task=goal, worker_id="explore")],
        )


async def execute_workplan(
    orch,
    project_cfg,
    boss_cfg,
    plan: Workplan,
) -> str:
    """
    Führt einen Workplan aus — dispatcht alle Steps und synthetisiert die Ergebnisse.
    """
    from .orchestrator_dispatch import _dispatch_dag, _synthesize

    dispatches = plan.to_dispatches()
    logger.info("Coordinator: Führe %d Steps aus für '%s'", len(dispatches), plan.goal[:60])

    results = await _dispatch_dag(orch, project_cfg, dispatches, context=plan.goal)

    # Ergebnisse zusammenfassen
    summary_parts = [f"## Workplan: {plan.goal}\n"]
    for step, result in zip(plan.steps, results):
        status = "✅" if result.success else "❌"
        summary_parts.append(
            f"### {status} Step: {step.task}\n"
            f"**Worker:** {step.worker_id}\n\n"
            f"{result.result[:2000] if result.result else result.error or 'Kein Ergebnis'}\n"
        )

    return "\n".join(summary_parts)
