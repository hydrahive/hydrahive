#!/usr/bin/env python3
"""
dedupe_legacy_feedback.py — Entfernt Legacy-Feedback-Duplikate aus Projekt-Memories (#716)

Seit #708 stehen Core-Regeln (Token-Disziplin, Memory-Konventionen, Bulk-Lookups
etc.) im System-Prompt. Die frueher pro Projekt gepflegten feedback_*.md-Dateien
sind damit redundant und schaden der Cache-Effizienz.

Dieses Script entfernt nur Dateien aus der expliziten Allowlist (siehe
`hydrahive_core.memory_diagnose.LEGACY_CORE_POLICY_FEEDBACK_FILES`) und legt
vor der Loeschung ein Backup pro Projekt an.

Default ist --dry-run. --execute loescht wirklich.

Usage:
    scripts/dedupe_legacy_feedback.py --dry-run
    scripts/dedupe_legacy_feedback.py --project hydrahive_support --dry-run
    scripts/dedupe_legacy_feedback.py --execute
    scripts/dedupe_legacy_feedback.py --execute --force-uncertain
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import time
from pathlib import Path

# memory_diagnose.py direkt laden (ohne hydrahive_core-__init__, das den
# vollen Orchestrator-Stack zieht und z.B. litellm voraussetzt). Das Modul
# ist selbst nur stdlib, keine transitive dependency noetig.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DIAG_PATH = _REPO_ROOT / "core" / "src" / "hydrahive_core" / "memory_diagnose.py"
_spec = importlib.util.spec_from_file_location("_memory_diagnose", _DIAG_PATH)
if _spec is None or _spec.loader is None:
    print(f"[ERR] memory_diagnose.py nicht ladbar: {_DIAG_PATH}", file=sys.stderr)
    raise SystemExit(2)
_memory_diagnose = importlib.util.module_from_spec(_spec)
# sys.modules-Registrierung vor exec_module: sonst findet @dataclass das
# Modul nicht beim Type-Annotation-Resolve und crasht mit AttributeError.
sys.modules["_memory_diagnose"] = _memory_diagnose
_spec.loader.exec_module(_memory_diagnose)
scan_legacy_feedback = _memory_diagnose.scan_legacy_feedback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legacy-Feedback-Duplikate entfernen")
    parser.add_argument("--projects-dir", default="/projects",
                        help="Root-Verzeichnis der Projekte (Default: /projects)")
    parser.add_argument("--project", default=None,
                        help="Nur ein bestimmtes Projekt bearbeiten (id = Dirname)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Standard — zeigt nur was entfernt wuerde")
    mode.add_argument("--execute", action="store_true",
                      help="Wirklich loeschen (nach Backup)")
    parser.add_argument("--force-uncertain", action="store_true",
                        help="Auch Dateien entfernen, deren Content den Allowlist-"
                             "Sanity-Check nicht erfuellt. STANDARD: sicherheitshalber "
                             "werden solche Files nur gemeldet, nicht entfernt.")
    args = parser.parse_args(argv)

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"[ERR] projects-dir existiert nicht: {projects_dir}", file=sys.stderr)
        return 2

    hits = scan_legacy_feedback(projects_dir)
    if args.project:
        hits = [h for h in hits if h.project_id == args.project]

    if not hits:
        print(f"[ok] Keine Legacy-Feedback-Dateien gefunden in {projects_dir}")
        return 0

    # Partitionieren
    safe = [h for h in hits if h.keyword_match]
    uncertain = [h for h in hits if not h.keyword_match]

    print(f"== Scan-Ergebnis ({projects_dir}) ==")
    print(f"  Gefunden: {len(hits)}  |  safe: {len(safe)}  |  uncertain: {len(uncertain)}")
    print()

    for h in safe:
        kw = ", ".join(h.matched_keywords)
        print(f"  [safe]       {h.path}  ({h.size_bytes} B, keywords: {kw})")
    for h in uncertain:
        print(f"  [uncertain]  {h.path}  ({h.size_bytes} B, kein erwartetes Keyword im Content)")

    to_remove = safe + (uncertain if args.force_uncertain else [])
    skipped_uncertain = len(uncertain) if not args.force_uncertain else 0

    if skipped_uncertain:
        print()
        print(f"[hint] {skipped_uncertain} uncertain-Dateien werden ohne --force-uncertain NICHT entfernt.")

    if not to_remove:
        print()
        print("[ok] Nichts zu tun (alle Treffer sind uncertain und --force-uncertain nicht gesetzt).")
        return 0

    if args.dry_run and not args.execute:
        print()
        print(f"[dry-run] {len(to_remove)} Datei(en) wuerden entfernt. Mit --execute wirklich loeschen.")
        return 0

    # Execute: pro Projekt ein Backup-Dir + move
    ts = time.strftime("%Y%m%d-%H%M%S")
    by_project: dict[str, list] = {}
    for h in to_remove:
        by_project.setdefault(h.project_id, []).append(h)

    total_moved = 0
    for pid, items in sorted(by_project.items()):
        memory_dir = projects_dir / pid / "memory"
        backup_dir = memory_dir / f".legacy_backup_{ts}"
        try:
            backup_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            print(f"[ERR] Backup-Dir existiert bereits: {backup_dir}", file=sys.stderr)
            continue
        except OSError as e:
            print(f"[ERR] Backup-Dir nicht anlegbar ({backup_dir}): {e}", file=sys.stderr)
            continue
        for h in items:
            target = backup_dir / h.path.name
            try:
                shutil.move(str(h.path), str(target))
                total_moved += 1
                print(f"  moved: {h.path}  ->  {target}")
            except OSError as e:
                print(f"  [ERR] {h.path}: {e}", file=sys.stderr)

    print()
    print(f"[done] {total_moved} Datei(en) in Backup verschoben.")
    print("       Backups liegen pro Projekt unter <projects-dir>/<id>/memory/.legacy_backup_<ts>/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
