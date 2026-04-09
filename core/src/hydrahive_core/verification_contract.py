"""
verification_contract.py — Standardisiertes Verification-Ergebnis (#518)

PASS / FAIL / PARTIAL Contract mit strukturierten Findings.
Wird vom Verify-Worker produziert und von der Boss-Policy konsumiert.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    SECURITY = "security"


@dataclass
class VerificationFinding:
    category: FindingCategory
    severity: FindingSeverity
    message: str
    file_path: str | None = None
    line: int | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d


@dataclass
class VerificationResult:
    status: VerificationStatus
    findings: list[VerificationFinding] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    summary: str = ""
    duration_ms: float = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks_run: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)

    def is_blocking(self) -> bool:
        """True wenn CRITICAL oder HIGH Findings den Workflow blockieren sollten."""
        return any(
            f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
            for f in self.findings
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "findings": [f.to_dict() for f in self.findings],
            "affected_files": self.affected_files,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "is_blocking": self.is_blocking(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> VerificationResult:
        findings = [
            VerificationFinding(
                category=FindingCategory(f["category"]),
                severity=FindingSeverity(f["severity"]),
                message=f["message"],
                file_path=f.get("file_path"),
                line=f.get("line"),
                detail=f.get("detail", ""),
            )
            for f in data.get("findings", [])
        ]
        return cls(
            status=VerificationStatus(data["status"]),
            findings=findings,
            affected_files=data.get("affected_files", []),
            summary=data.get("summary", ""),
            duration_ms=data.get("duration_ms", 0),
            timestamp=data.get("timestamp", ""),
            checks_run=data.get("checks_run", []),
            checks_passed=data.get("checks_passed", []),
            checks_failed=data.get("checks_failed", []),
        )

    @classmethod
    def from_llm_output(cls, raw_text: str) -> VerificationResult:
        """Parsed die Markdown-Ausgabe des Verify-Workers in ein strukturiertes Result.

        Erwartet Format:
            ### Ergebnis: PASS / FAIL / PARTIAL
            ### Geprüft
            - ...
            ### Fehler
            - ...
            ### Empfehlung
            - ...
        """
        text = raw_text.strip()

        # Status extrahieren
        status = VerificationStatus.PARTIAL  # Default bei Parse-Fehlern
        status_match = re.search(
            r'###?\s*Ergebnis\s*:\s*(PASS|FAIL|PARTIAL)',
            text, re.IGNORECASE,
        )
        if status_match:
            status = VerificationStatus(status_match.group(1).lower())
        elif "pass" in text.lower()[:100] and "fail" not in text.lower()[:100]:
            status = VerificationStatus.PASS
        elif "fail" in text.lower()[:100]:
            status = VerificationStatus.FAIL

        # Findings extrahieren
        findings: list[VerificationFinding] = []

        # Fehler-Section parsen
        error_section = re.search(
            r'###?\s*(?:Fehler|Errors?|Findings?)\s*\n(.*?)(?=###|\Z)',
            text, re.IGNORECASE | re.DOTALL,
        )
        if error_section:
            for line in error_section.group(1).strip().split("\n"):
                line = line.strip().lstrip("- •*")
                if not line or len(line) < 5:
                    continue
                # File:Line Pattern erkennen
                file_match = re.match(r'(?:\*\*)?([^\s:]+(?:\.\w+)):(\d+)(?:\*\*)?', line)
                severity = FindingSeverity.MEDIUM
                if any(w in line.lower() for w in ("critical", "kritisch")):
                    severity = FindingSeverity.CRITICAL
                elif any(w in line.lower() for w in ("high", "hoch", "error", "fehler")):
                    severity = FindingSeverity.HIGH
                elif any(w in line.lower() for w in ("low", "niedrig", "info", "hinweis")):
                    severity = FindingSeverity.LOW

                # Kategorie erkennen
                category = FindingCategory.RUNTIME
                if any(w in line.lower() for w in ("build", "compile", "kompilier")):
                    category = FindingCategory.BUILD
                elif any(w in line.lower() for w in ("test", "assert", "expect")):
                    category = FindingCategory.TEST
                elif any(w in line.lower() for w in ("lint", "style", "format")):
                    category = FindingCategory.LINT
                elif any(w in line.lower() for w in ("syntax", "parse", "indent")):
                    category = FindingCategory.SYNTAX
                elif any(w in line.lower() for w in ("security", "sicherheit", "xss", "injection")):
                    category = FindingCategory.SECURITY

                findings.append(VerificationFinding(
                    category=category,
                    severity=severity,
                    message=line[:200],
                    file_path=file_match.group(1) if file_match else None,
                    line=int(file_match.group(2)) if file_match else None,
                ))

        # Geprüft-Section parsen
        checks_run: list[str] = []
        checked_section = re.search(
            r'###?\s*(?:Geprüft|Checked|Checks)\s*\n(.*?)(?=###|\Z)',
            text, re.IGNORECASE | re.DOTALL,
        )
        if checked_section:
            for line in checked_section.group(1).strip().split("\n"):
                line = line.strip().lstrip("- •*")
                if line and len(line) > 2:
                    checks_run.append(line[:80])

        # Summary: erste Zeile oder Empfehlung
        summary = ""
        rec_section = re.search(
            r'###?\s*(?:Empfehlung|Recommendation|Summary)\s*\n(.*?)(?=###|\Z)',
            text, re.IGNORECASE | re.DOTALL,
        )
        if rec_section:
            summary = rec_section.group(1).strip()[:300]
        elif text:
            summary = text.split("\n")[0][:200]

        return cls(
            status=status,
            findings=findings,
            summary=summary,
            checks_run=checks_run,
            checks_passed=[c for c in checks_run if status == VerificationStatus.PASS],
            checks_failed=[f.message[:60] for f in findings if f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)],
        )
