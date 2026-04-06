"""
config_loader.py — Zentrale Config Load/Save Utility (#388)

Einheitliches JSON/YAML Loading statt überall anders implementiert.
Immer UTF-8, immer try/catch, immer chmod 600 für Secrets.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json(path: str | Path, default: Any = None) -> Any:
    """JSON laden — bei Fehler default zurückgeben."""
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("JSON load failed %s: %s", p, e)
        return default if default is not None else {}


def save_json(path: str | Path, data: Any, *, mode: int = 0o600, indent: int = 2) -> bool:
    """JSON speichern — atomic write + chmod."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.chmod(mode)
        tmp.replace(p)  # atomic auf POSIX
        return True
    except OSError as e:
        logger.warning("JSON save failed %s: %s", p, e)
        tmp.unlink(missing_ok=True)
        return False


def load_yaml(path: str | Path, default: Any = None) -> Any:
    """YAML laden — bei Fehler default zurückgeben."""
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or (default if default is not None else {})
    except Exception as e:
        logger.warning("YAML load failed %s: %s", p, e)
        return default if default is not None else {}


def save_yaml(path: str | Path, data: Any, *, mode: int = 0o600) -> bool:
    """YAML speichern — atomic write + chmod."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    try:
        import yaml
        tmp.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        tmp.chmod(mode)
        tmp.replace(p)
        return True
    except Exception as e:
        logger.warning("YAML save failed %s: %s", p, e)
        tmp.unlink(missing_ok=True)
        return False
