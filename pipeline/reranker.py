# pipeline/reranker.py
import math
import sqlite3
from sentence_transformers import CrossEncoder


CE_MODEL = 'cross-encoder/ms-marco-MiniLM-L-6-v2'
_ce = None


def get_cross_encoder():
    global _ce
    if _ce is None:
        _ce = CrossEncoder(CE_MODEL)
    return _ce


def fetch_para(conn, para_idx):
    """Fetch paragraph row from SQLite. para_idx is the FAISS zero-based index."""
    row = conn.execute(
        'SELECT * FROM paragraphs WHERE id = ?', (para_idx + 1,)
    ).fetchone()
    return row


def rerank(sentence, hybrid_results, conn, top_k=5):
    """
    Applies cross-encoder re-ranking to the top 10 hybrid results.
    Returns top_k candidates as dicts with all scores and metadata.
    """
    ce = get_cross_encoder()
    candidates = []
    pairs = []

    for para_idx, hybrid_score, sem_score, bm25_score in hybrid_results[:10]:
        row = fetch_para(conn, para_idx)
        if not row:
            continue
        para_text = row[22]  # text column
        pairs.append((sentence, para_text))
        candidates.append({
            'para_idx':     para_idx,
            'hybrid_score': hybrid_score,
            'sem_score':    sem_score,
            'bm25_score':   bm25_score,
            'row':          row
        })

    if not pairs:
        return []

    ce_scores = ce.predict(pairs)

    for i, cand in enumerate(candidates):
        raw_ce = float(ce_scores[i])
        # Normalise CE score from logit range to approx 0–1
        norm_ce = 1.0 / (1.0 + math.exp(-raw_ce))
        cand['ce_score'] = raw_ce
        cand['ce_norm'] = norm_ce
        cand['confidence'] = (
            0.50 * norm_ce +
            0.30 * cand['sem_score'] +
            0.20 * cand['bm25_score']
        )

    candidates.sort(key=lambda x: x['confidence'], reverse=True)
    return candidates[:top_k]
