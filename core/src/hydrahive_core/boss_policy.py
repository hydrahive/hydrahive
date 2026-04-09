"""
boss_policy.py — Verification nach Code-Änderungen erzwingen (#520)

Debounce-aware: Sammelt file_write/file_patch Aktionen, triggert
Verification erst wenn die Schreib-Serie vorbei ist (nächstes Nicht-Write-Tool)
oder bei explizitem git_commit.

Feature-Flag: Nur aktiv wenn settings.boss_policy_enabled == True.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .verification_contract import VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)

# Tools die Mutations tracken (Verification-Kandidaten)
MUTATION_TOOLS = {"file_write", "file_patch", "write_system_file", "server_file_write"}
# Tools die sofort Verification triggern
IMMEDIATE_VERIFY = {"git_commit"}
# Cooldown zwischen Verifications (Sekunden)
VERIFY_COOLDOWN = 30
# Flag um Endlos-Loops zu verhindern
_in_verification: set[str] = set()  # project_ids die gerade verifiziert werden


class BossPolicy:
    """Entscheidet ob/wann Verification getriggert wird."""

    def __init__(self):
        self._pending_mutations: dict[str, list[str]] = {}  # project_id → [geänderte Dateien]
        self._last_verification: dict[str, float] = {}       # project_id → timestamp

    def record_mutation(self, project_id: str, tool_name: str, tool_input: dict) -> None:
        """Zeichnet eine Mutation auf (file_write etc.)."""
        if tool_name in MUTATION_TOOLS:
            path = tool_input.get("path", "unknown")
            self._pending_mutations.setdefault(project_id, []).append(path)

    def should_verify(
        self,
        project_id: str,
        tool_name: str,
    ) -> bool:
        """
        Entscheidet ob jetzt Verification getriggert werden soll.

        Logik:
        1. Wenn gerade Verification läuft → nein (Anti-Loop)
        2. Bei git_commit → ja (expliziter Checkpoint)
        3. Bei Nicht-Mutation nach Mutations → ja (Schreib-Serie vorbei)
        4. Cooldown prüfen
        """
        if project_id in _in_verification:
            return False

        pending = self._pending_mutations.get(project_id, [])

        # git_commit → sofort verifizieren
        if tool_name in IMMEDIATE_VERIFY and pending:
            return self._check_cooldown(project_id)

        # Mutation → nur merken, nicht verifizieren
        if tool_name in MUTATION_TOOLS:
            return False

        # Nicht-Mutation nach Mutations → Schreib-Serie ist vorbei
        if pending and tool_name not in MUTATION_TOOLS:
            return self._check_cooldown(project_id)

        return False

    def _check_cooldown(self, project_id: str) -> bool:
        """Prüft ob der Cooldown abgelaufen ist."""
        last = self._last_verification.get(project_id, 0)
        return (time.time() - last) >= VERIFY_COOLDOWN

    def get_pending_files(self, project_id: str) -> list[str]:
        """Gibt die Liste der geänderten Dateien zurück und leert sie."""
        files = list(set(self._pending_mutations.pop(project_id, [])))
        return files

    async def trigger_verification(
        self,
        orch,
        project_id: str,
        boss_cfg,
        affected_files: list[str],
    ) -> VerificationResult:
        """Dispatched den Verify-Worker und gibt das Ergebnis zurück."""
        _in_verification.add(project_id)
        self._last_verification[project_id] = time.time()

        try:
            from .built_in_workers import get_builtin_worker
            verify_profile = get_builtin_worker("verify")
            if not verify_profile:
                return VerificationResult(
                    status=VerificationStatus.PARTIAL,
                    summary="Verify-Worker nicht verfügbar",
                )

            files_str = ", ".join(affected_files[:10])
            dispatch = {
                "worker_id": "verify",
                "task": f"Verifiziere die letzten Änderungen an: {files_str}",
                "context": f"Geänderte Dateien: {files_str}",
                "task_id": f"auto-verify-{int(time.time())}",
                "project_id": project_id,
            }

            from .orchestrator_dispatch import _run_builtin_worker
            result = await asyncio.wait_for(
                _run_builtin_worker(orch, dispatch, verify_profile),
                timeout=120,
            )

            if result.success and result.result:
                return VerificationResult.from_llm_output(result.result)
            else:
                return VerificationResult(
                    status=VerificationStatus.PARTIAL,
                    summary=f"Verification Worker Fehler: {result.error or 'unbekannt'}",
                )
        except asyncio.TimeoutError:
            return VerificationResult(
                status=VerificationStatus.PARTIAL,
                summary="Verification Timeout (120s)",
            )
        except Exception as e:
            logger.warning("Verification fehlgeschlagen: %s", e)
            return VerificationResult(
                status=VerificationStatus.PARTIAL,
                summary=f"Verification Fehler: {e}",
            )
        finally:
            _in_verification.discard(project_id)

    @staticmethod
    def handle_result(result: VerificationResult) -> str:
        """
        Entscheidet was nach Verification passiert.

        Returns: "continue" | "warn" | "ask_user"
        """
        if result.status == VerificationStatus.PASS:
            return "continue"
        if result.is_blocking():
            return "ask_user"
        if result.status == VerificationStatus.FAIL:
            return "warn"
        return "continue"  # PARTIAL ohne blocking findings


# Globale Singleton-Instanz
boss_policy = BossPolicy()
