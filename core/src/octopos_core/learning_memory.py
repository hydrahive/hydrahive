from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LEARNING_MEMORY_FILENAME = "learned-facts.md"
LEARNING_INDEX_FILENAME = "learning-index.jsonl"
MAX_LEARNING_SUMMARY_CHARS = 2048
MAX_LEARNING_PROMPT_CHARS = 4096
MAX_LEARNING_PROMPT_ENTRIES = 12
MAX_DEDUP_LOOKBACK_ENTRIES = 12


def _normalize_source_group(source: str) -> str:
    normalized = (source or "").strip().lower()
    if not normalized:
        return "manual"
    return normalized.split(".", 1)[0] or "manual"


def _derive_learning_topic_and_tags(source: str, summary: str) -> tuple[str, list[str]]:
    text = f"{source} {summary}".lower()
    source_group = _normalize_source_group(source)
    topic = source_group
    tags: list[str] = [source_group]

    keyword_groups = [
        ("memory", ("memory", "a-mem", "learned-facts", "learning", "compaction")),
        ("repo", ("repo", "repository", "gitea", "github", "commit", "issue", "diff", "tree")),
        ("ui", ("ui", "frontend", "console", "web", "layout", "screen")),
        ("ops", ("deploy", "release", "installer", "system", "service", "daemon", "backup")),
        ("chat", ("chat", "agent", "message", "session")),
        ("security", ("auth", "token", "rate limit", "permission", "audit")),
    ]

    for candidate_topic, keywords in keyword_groups:
        if any(keyword in text for keyword in keywords):
            topic = candidate_topic
            if candidate_topic not in tags:
                tags.append(candidate_topic)
            for keyword in keywords:
                if keyword in text and keyword not in tags:
                    tags.append(keyword)
            break

    return topic, tags


def _truncate_text(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    suffix = "\n\n[gekürzt]"
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


def _normalize_learning_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _hash_learning_text(text: str) -> str:
    return hashlib.sha256(_normalize_learning_text(text).encode("utf-8")).hexdigest()


def _split_learning_entries(text: str) -> list[str]:
    return [block.strip() for block in text.split("\n---\n\n") if block.strip()]


def _entry_summary_and_hash(entry: str) -> tuple[str, str | None]:
    parts = entry.split("\n\n", 1)
    if len(parts) < 2:
        return "", None
    metadata = parts[0]
    summary = parts[1].strip()
    entry_hash = None
    for line in metadata.splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("- hash:"):
            entry_hash = stripped.split(":", 1)[1].strip() or None
            break
    return summary, entry_hash


def _recent_learning_hashes(
    target: Path,
    *,
    max_entries: int = MAX_DEDUP_LOOKBACK_ENTRIES,
) -> set[str]:
    if not target.exists():
        return set()

    text = target.read_text(encoding="utf-8").strip()
    if not text:
        return set()

    entries = _split_learning_entries(text)
    if max_entries > 0:
        entries = entries[-max_entries:]

    hashes: set[str] = set()
    for entry in entries:
        summary, entry_hash = _entry_summary_and_hash(entry)
        if entry_hash:
            hashes.add(entry_hash)
        elif summary:
            hashes.add(_hash_learning_text(summary))
    return hashes


def _learning_entry_body(summary: str, source: str, entry_hash: str, now: str) -> str:
    return (
        f"## {now}\n"
        f"- source: {source}\n"
        f"- hash: {entry_hash}\n\n"
        f"{summary}\n\n"
        "---\n\n"
    )


def _append_learning_index_record(
    agent_dir: Path,
    *,
    summary: str,
    source: str,
    entry_hash: str,
    filename: str = LEARNING_INDEX_FILENAME,
) -> Path:
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / filename
    topic, tags = _derive_learning_topic_and_tags(source, summary)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent_dir.name,
        "project": agent_dir.name,
        "source": source,
        "source_group": _normalize_source_group(source),
        "topic": topic,
        "tags": tags,
        "summary": summary,
        "hash": entry_hash,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


def build_learning_prompt_snippet(
    agent_dir: str | Path,
    *,
    max_entries: int = MAX_LEARNING_PROMPT_ENTRIES,
    max_chars: int = MAX_LEARNING_PROMPT_CHARS,
    filename: str = LEARNING_MEMORY_FILENAME,
) -> str:
    agent_path = Path(agent_dir)
    target = agent_path / "memory" / filename
    if not target.exists():
        return ""

    text = target.read_text(encoding="utf-8").strip()
    if not text:
        return ""

    entries = _split_learning_entries(text)
    if max_entries > 0:
        entries = entries[-max_entries:]

    snippet = "## Lernfakten (zuletzt)\n\n" + "\n\n---\n\n".join(entries)
    return _truncate_text(snippet, max_chars)


def append_learning_snapshot(
    agent_dir: str | Path,
    summary: str,
    *,
    source: str = "session.compact",
    filename: str = LEARNING_MEMORY_FILENAME,
    index_filename: str = LEARNING_INDEX_FILENAME,
    logger=None,
) -> Path:
    """
    Persistiert eine lernrelevante Zusammenfassung im persönlichen Memory.

    Der Eintrag bleibt bewusst menschlich lesbar, damit spätere Skills oder
    manuelle Reviews daraus verwertbare Fakten und Muster ableiten können.
    """
    agent_path = Path(agent_dir)
    memory_dir = agent_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    target = memory_dir / filename
    summary_text = _truncate_text(summary, MAX_LEARNING_SUMMARY_CHARS)
    entry_hash = _hash_learning_text(summary_text)
    if entry_hash in _recent_learning_hashes(target):
        if logger is not None:
            logger.info("learning-memory: exakter Duplikat-Shadow skip (%s)", entry_hash[:12])
        return target

    now = datetime.now(timezone.utc).isoformat()
    entry = _learning_entry_body(summary_text, source, entry_hash, now)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(existing + entry, encoding="utf-8")

    try:
        _append_learning_index_record(
            agent_path,
            summary=summary_text,
            source=source,
            entry_hash=entry_hash,
            filename=index_filename,
        )
    except Exception as e:
        if logger is not None:
            logger.warning("learning-memory: Index konnte nicht gespeichert werden: %s", e)

    return target
