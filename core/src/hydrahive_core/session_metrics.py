"""
session_metrics.py — Kontext- und Turn-Metriken (#512, Phase 0)

Erfasst pro Session strukturierte Metriken für jeden LLM-Call,
Compaction-Events, Tool-Rounds und Fehler. Voraussetzung für
alle Phase-1-Tickets (Context Lifecycle, Verification etc.).

Nutzung:
    from .session_metrics import metrics
    metrics.record_llm_call("proj-1", prompt_tokens=..., ...)
    snapshot = metrics.snapshot("proj-1")
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LLMCallRecord:
    """Einzelner LLM-Call mit Token-Breakdown."""
    timestamp:    float
    model:        str
    prompt_tokens:  int = 0   # System-Prompt
    history_tokens: int = 0   # History/Context
    tool_tokens:    int = 0   # Tool-Schemas
    input_tokens:   int = 0   # Gesamt-Input (von API)
    output_tokens:  int = 0
    cache_read:     int = 0
    cache_write:    int = 0
    latency_ms:     float = 0.0
    is_compaction:  bool = False  # War das ein Compaction-Call?


@dataclass
class SessionMetricsData:
    """Aggregierte Metriken für eine aktive Session."""
    project_id:   str
    started_at:   float = field(default_factory=time.time)

    # LLM-Call History
    llm_calls:    list[LLMCallRecord] = field(default_factory=list)

    # Aggregierte Zähler
    total_input_tokens:   int = 0
    total_output_tokens:  int = 0
    total_cache_read:     int = 0
    total_cache_write:    int = 0

    # Context-Events
    compaction_count:     int = 0
    compaction_stages:    list[int] = field(default_factory=list)  # [1, 2, 3, ...]
    overflow_count:       int = 0      # Context-Overflow-Fehler
    session_resets:       int = 0      # Session-Resets wegen Overflow

    # Tool-Loop
    tool_rounds_total:    int = 0
    tool_calls_total:     int = 0
    signature_aborts:     int = 0      # Repeated-Signature-Abbrüche
    max_rounds_hits:      int = 0      # max_rounds erreicht

    # Retries / Failovers
    retries:              int = 0
    failovers:            int = 0

    # #516: Tool-Result-Budgeting
    tool_results_budgeted: int = 0      # Wie oft wurde ein Result gekürzt
    tool_results_bytes_saved: int = 0   # Eingespartes Volumen in Zeichen

    def cache_hit_rate(self) -> float:
        """Cache-Hit-Rate über alle Calls."""
        total_input = self.total_input_tokens
        if total_input == 0:
            return 0.0
        return self.total_cache_read / total_input

    def avg_latency_ms(self) -> float:
        """Durchschnittliche LLM-Latenz."""
        if not self.llm_calls:
            return 0.0
        return sum(c.latency_ms for c in self.llm_calls) / len(self.llm_calls)

    def to_dict(self) -> dict:
        """Snapshot für API-Response."""
        return {
            "project_id":         self.project_id,
            "started_at":         self.started_at,
            "llm_call_count":     len(self.llm_calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read":   self.total_cache_read,
            "total_cache_write":  self.total_cache_write,
            "cache_hit_rate":     round(self.cache_hit_rate(), 3),
            "compaction_count":   self.compaction_count,
            "compaction_stages":  self.compaction_stages,
            "overflow_count":     self.overflow_count,
            "session_resets":     self.session_resets,
            "tool_rounds_total":  self.tool_rounds_total,
            "tool_calls_total":   self.tool_calls_total,
            "signature_aborts":   self.signature_aborts,
            "max_rounds_hits":    self.max_rounds_hits,
            "retries":            self.retries,
            "failovers":          self.failovers,
            "tool_results_budgeted": self.tool_results_budgeted,
            "tool_results_bytes_saved": self.tool_results_bytes_saved,
            "avg_latency_ms":     round(self.avg_latency_ms(), 1),
            "last_calls":         [
                {
                    "timestamp":      c.timestamp,
                    "model":          c.model,
                    "input_tokens":   c.input_tokens,
                    "output_tokens":  c.output_tokens,
                    "cache_read":     c.cache_read,
                    "cache_write":    c.cache_write,
                    "prompt_tokens":  c.prompt_tokens,
                    "history_tokens": c.history_tokens,
                    "tool_tokens":    c.tool_tokens,
                    "latency_ms":     round(c.latency_ms, 1),
                    "is_compaction":  c.is_compaction,
                }
                for c in self.llm_calls[-20:]  # Letzte 20 Calls
            ],
        }


class SessionMetrics:
    """Globaler Metrics-Collector — ein SessionMetricsData pro Projekt."""

    def __init__(self) -> None:
        self._data: dict[str, SessionMetricsData] = {}

    def _get(self, project_id: str) -> SessionMetricsData:
        if project_id not in self._data:
            self._data[project_id] = SessionMetricsData(project_id=project_id)
        return self._data[project_id]

    def reset(self, project_id: str) -> None:
        """Session-Metriken zurücksetzen (z.B. bei neuem Session-Start)."""
        self._data.pop(project_id, None)

    # ── LLM-Call Recording ──────────────────────────────────────────

    def record_llm_call(
        self,
        project_id: str,
        *,
        model: str = "",
        prompt_tokens: int = 0,
        history_tokens: int = 0,
        tool_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        latency_ms: float = 0.0,
        is_compaction: bool = False,
    ) -> None:
        d = self._get(project_id)
        d.llm_calls.append(LLMCallRecord(
            timestamp=time.time(),
            model=model,
            prompt_tokens=prompt_tokens,
            history_tokens=history_tokens,
            tool_tokens=tool_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
            latency_ms=latency_ms,
            is_compaction=is_compaction,
        ))
        d.total_input_tokens += input_tokens
        d.total_output_tokens += output_tokens
        d.total_cache_read += cache_read
        d.total_cache_write += cache_write

        # Alte Calls begrenzen (behalte letzte 100)
        if len(d.llm_calls) > 100:
            d.llm_calls = d.llm_calls[-100:]

    # ── Context-Events ──────────────────────────────────────────────

    def record_compaction(self, project_id: str, stage: int) -> None:
        d = self._get(project_id)
        d.compaction_count += 1
        d.compaction_stages.append(stage)

    def record_overflow(self, project_id: str) -> None:
        d = self._get(project_id)
        d.overflow_count += 1

    def record_session_reset(self, project_id: str) -> None:
        d = self._get(project_id)
        d.session_resets += 1

    # ── Tool-Loop Events ────────────────────────────────────────────

    def record_tool_round(self, project_id: str, tool_call_count: int) -> None:
        d = self._get(project_id)
        d.tool_rounds_total += 1
        d.tool_calls_total += tool_call_count

    def record_signature_abort(self, project_id: str) -> None:
        d = self._get(project_id)
        d.signature_aborts += 1

    def record_max_rounds_hit(self, project_id: str) -> None:
        d = self._get(project_id)
        d.max_rounds_hits += 1

    # ── Retry / Failover ────────────────────────────────────────────

    def record_retry(self, project_id: str) -> None:
        d = self._get(project_id)
        d.retries += 1

    def record_failover(self, project_id: str) -> None:
        d = self._get(project_id)
        d.failovers += 1

    def record_tool_budget(self, project_id: str, original_len: int, budgeted_len: int) -> None:
        """#516: Tool-Result wurde durch Budgeting gekürzt."""
        d = self._get(project_id)
        d.tool_results_budgeted += 1
        d.tool_results_bytes_saved += (original_len - budgeted_len)

    # ── Snapshots ───────────────────────────────────────────────────

    def snapshot(self, project_id: str) -> dict:
        """Aktueller Metriken-Stand für ein Projekt."""
        return self._get(project_id).to_dict()

    def snapshot_all(self) -> dict[str, dict]:
        """Alle aktiven Projekt-Metriken."""
        return {pid: d.to_dict() for pid, d in self._data.items()}

    def active_projects(self) -> list[str]:
        return list(self._data.keys())


# Globale Singleton-Instanz
metrics = SessionMetrics()
