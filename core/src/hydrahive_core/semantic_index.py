"""
semantic_index.py — FAISS Semantic Index für HydraHive (#44)

Vektor-Embeddings via litellm + FAISS-Index pro Agent-Verzeichnis.
Wird für Memory-Hybrid-Suche und Skill-Relevanz-Scoring genutzt.

Fallback: BM25-only wenn faiss-cpu oder litellm-Embeddings nicht verfügbar.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optionale Imports ──────────────────────────────────────────────────────────

try:
    import faiss          # type: ignore
    import numpy as np    # type: ignore
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.info("faiss-cpu nicht installiert — semantische Suche deaktiviert")

try:
    import litellm        # type: ignore
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

# ── Embedding-Modell-Konfiguration ────────────────────────────────────────────

_LLM_CFG_PATH = Path("/etc/hydrahive/llm_config.json")
_DEFAULT_MODEL = "text-embedding-3-small"

def _get_embedding_model() -> str:
    """Embedding-Modell: Env-Var → llm_config.json → Default."""
    env = os.environ.get("HYDRAHIVE_EMBEDDING_MODEL")
    if env:
        return env
    if _LLM_CFG_PATH.exists():
        try:
            raw = json.loads(_LLM_CFG_PATH.read_text(encoding="utf-8"))
            for entry in (raw if isinstance(raw, list) else [raw]):
                if "embedding_model" in entry:
                    return entry["embedding_model"]
        except Exception:
            pass
    return _DEFAULT_MODEL


# ── Embedding ──────────────────────────────────────────────────────────────────

def _embed(texts: list[str]) -> "Optional[np.ndarray]":
    """Texte → L2-normalisierte Embedding-Matrix (N × D). None bei Fehler.

    Sync — muss von Callers im async Context per run_in_executor aufgerufen werden.
    """
    if not FAISS_AVAILABLE or not LITELLM_AVAILABLE or not texts:
        return None
    model = _get_embedding_model()
    try:
        kwargs: dict = {"model": model, "input": texts}
        if model.startswith("ollama/"):
            base = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")
            kwargs["model"]    = f"openai/{model[len('ollama/'):]}"
            kwargs["api_base"] = f"{base.rstrip('/')}/v1"
            kwargs["api_key"]  = "ollama"  # litellm braucht non-empty key auch für lokale Endpoints
        resp = litellm.embedding(**kwargs)
        vecs = np.array([e["embedding"] for e in resp.data], dtype=np.float32)
        faiss.normalize_L2(vecs)
        return vecs
    except Exception as e:
        logger.warning("Embedding fehlgeschlagen (%s): %s", model, e)
        return None


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _index_path(agent_dir: Path, name: str) -> tuple[Path, Path]:
    return agent_dir / f"{name}_index.faiss", agent_dir / f"{name}_index_meta.json"


# ── Persistierter FAISS-Index ──────────────────────────────────────────────────

def _cache_path(agent_dir: Path, name: str) -> Path:
    return agent_dir / f"{name}_embed_cache.json"


def _load_embed_cache(cache_path: Path, model: str) -> dict[str, list[float]]:
    """Lädt per-Chunk-Embedding-Cache {hash: vector}. Leer bei Modell-Wechsel."""
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if raw.get("model") != model:
            return {}  # Modell gewechselt → Cache ungültig
        return raw.get("vectors", {})
    except Exception:
        return {}


def _save_embed_cache(cache_path: Path, model: str, vectors: dict[str, list[float]]) -> None:
    try:
        cache_path.write_text(
            json.dumps({"model": model, "vectors": vectors}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Embed-Cache speichern fehlgeschlagen: %s", e)


def update_index(agent_dir: Path, texts: list[str], name: str) -> bool:
    """
    Erstellt / aktualisiert FAISS-Index für eine Textliste.
    Nur geänderte Chunks werden neu embedded (per-Chunk-Hash-Cache).
    Gibt True zurück wenn Index danach nutzbar ist.
    """
    if not FAISS_AVAILABLE or not texts:
        return False

    faiss_path, meta_path = _index_path(agent_dir, name)
    current_model = _get_embedding_model()
    new_hashes = [_hash(t) for t in texts]

    # Update überspringen wenn alles identisch
    if meta_path.exists() and faiss_path.exists():
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            if (m.get("hashes") == new_hashes and m.get("model") == current_model):
                return True
        except Exception:
            pass

    # Per-Chunk-Embedding-Cache laden — nur neue/geänderte Chunks embedden
    cache_path = _cache_path(agent_dir, name)
    embed_cache = _load_embed_cache(cache_path, current_model)

    missing_indices = [i for i, h in enumerate(new_hashes) if h not in embed_cache]
    if missing_indices:
        missing_texts = [texts[i] for i in missing_indices]
        new_vecs = _embed(missing_texts)
        if new_vecs is None:
            return False
        for idx, vec in zip(missing_indices, new_vecs):
            embed_cache[new_hashes[idx]] = vec.tolist()
        _save_embed_cache(cache_path, current_model, embed_cache)
        logger.debug(
            "semantic_index '%s': %d neue Chunks embedded, %d aus Cache",
            name, len(missing_indices), len(texts) - len(missing_indices),
        )

    # FAISS-Index aus Cache aufbauen
    import numpy as np  # noqa: PLC0415
    try:
        all_vecs = np.array([embed_cache[h] for h in new_hashes], dtype=np.float32)
    except KeyError:
        return False  # Sollte nicht vorkommen

    dim   = all_vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(all_vecs)
    faiss.write_index(index, str(faiss_path))

    meta_path.write_text(
        json.dumps({
            "texts":  texts,
            "hashes": new_hashes,
            "dim":    dim,
            "model":  current_model,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug("semantic_index '%s': %d Vektoren gespeichert", name, len(texts))
    return True


def search_index(
    agent_dir: Path,
    query: str,
    k: int = 8,
    name: str = "semantic",
) -> list[tuple[str, float]]:
    """
    Semantische Suche im FAISS-Index.
    Gibt Liste von (text, cosine_score) Tupeln zurück (score 0–1).
    """
    if not FAISS_AVAILABLE:
        return []

    faiss_path, meta_path = _index_path(agent_dir, name)
    if not faiss_path.exists() or not meta_path.exists():
        return []

    try:
        meta  = json.loads(meta_path.read_text(encoding="utf-8"))
        texts = meta.get("texts", [])
        if not texts:
            return []
    except Exception:
        return []

    q_vec = _embed([query])
    if q_vec is None:
        return []

    try:
        index    = faiss.read_index(str(faiss_path))
        k_actual = min(k, len(texts))
        scores, indices = index.search(q_vec, k_actual)
        return [
            (texts[int(i)], float(s))
            for s, i in zip(scores[0], indices[0])
            if i >= 0
        ]
    except Exception as e:
        logger.warning("search_index Fehler (evtl. Modell-Wechsel?): %s", e)
        return []


# ── Ähnlichkeitssuche für Dedup ───────────────────────────────────────────────

def find_similar_chunk(
    agent_dir: Path,
    new_text: str,
    threshold: float = 0.65,
    name: str = "memory",
) -> tuple[str, float] | None:
    """
    Findet den ähnlichsten Chunk im FAISS-Index über dem Threshold.
    Gibt (text, similarity_score) zurück oder None.
    Genutzt für semantische Deduplizierung in WriteMemoryTool.
    """
    results = search_index(agent_dir, new_text, k=1, name=name)
    if not results:
        return None
    text, score = results[0]
    return (text, score) if score >= threshold else None


# ── Echtzeit-Scoring (ohne Persistenz) ────────────────────────────────────────

def score_texts(texts: list[str], query: str) -> list[float]:
    """
    Semantischer Cosine-Score (0.0–1.0) für eine kleine Textliste.
    Kein persistierter Index — direkte Embedding-Berechnung.
    Geeignet für Skill-Scoring (selten >30 Einträge).
    Gibt leere Liste zurück wenn Embeddings nicht verfügbar.
    """
    if not FAISS_AVAILABLE or not texts:
        return []

    all_vecs = _embed(texts + [query])
    if all_vecs is None:
        return []

    query_vec  = all_vecs[-1:]    # (1, D)
    text_vecs  = all_vecs[:-1]    # (N, D)
    raw        = (text_vecs @ query_vec.T).flatten()
    return [max(0.0, float(s)) for s in raw]
