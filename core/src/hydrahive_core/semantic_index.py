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
    """Texte → L2-normalisierte Embedding-Matrix (N × D). None bei Fehler."""
    if not FAISS_AVAILABLE or not LITELLM_AVAILABLE or not texts:
        return None
    model = _get_embedding_model()
    try:
        resp = litellm.embedding(model=model, input=texts)
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

def update_index(agent_dir: Path, texts: list[str], name: str) -> bool:
    """
    Erstellt / aktualisiert FAISS-Index für eine Textliste.
    Nur re-embedded wenn sich Inhalte geändert haben (Hash-Vergleich).
    Gibt True zurück wenn Index danach nutzbar ist.
    """
    if not FAISS_AVAILABLE or not texts:
        return False

    faiss_path, meta_path = _index_path(agent_dir, name)

    # Bestehende Metadaten laden
    existing_hashes: list[str] = []
    existing_model  = ""
    if meta_path.exists():
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            existing_hashes = m.get("hashes", [])
            existing_model  = m.get("model", "")
        except Exception:
            pass

    new_hashes = [_hash(t) for t in texts]
    current_model = _get_embedding_model()

    # Update überspringen wenn identisch
    if (existing_hashes == new_hashes and
            existing_model == current_model and
            faiss_path.exists()):
        return True

    vecs = _embed(texts)
    if vecs is None:
        return False

    dim   = vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)
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
