# pipeline/retrieval.py
import pickle, sqlite3, json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi


MODEL = 'all-MiniLM-L6-v2'
_model = None


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL)
    return _model


def load_indices(settings):
    faiss_idx = faiss.read_index(settings['faiss_path'])
    with open(settings['bm25_path'], 'rb') as f:
        bm25_data = pickle.load(f)
    return faiss_idx, bm25_data['bm25'], bm25_data['texts']


def semantic_search(context_window, faiss_idx, k=20):
    """Search by dense vector similarity. Uses context window for richness."""
    emb = get_model().encode([context_window], normalize_embeddings=True)
    scores, indices = faiss_idx.search(emb, k)
    return {int(i): float(s) for i, s in zip(indices[0], scores[0]) if i >= 0}


def bm25_search(sentence, bm25, k=20):
    """Search by token frequency. Uses trigger sentence only (not context)."""
    tokens = sentence.lower().split()
    raw = bm25.get_scores(tokens)
    top = np.argsort(raw)[::-1][:k]
    max_s = raw[top[0]] if raw[top[0]] > 0 else 1.0
    return {int(i): float(raw[i] / max_s) for i in top if raw[i] > 0}


def hybrid_search(sentence, context_window, faiss_idx, bm25,
                   sem_weight=0.6, bm25_weight=0.4, k=20):
    """
    Merges semantic and BM25 results with weighted scoring.
    Final score = sem_weight * semantic + bm25_weight * normalised_bm25
    Returns list of (para_db_id, final_score, sem_score, bm25_score)
    sorted by final_score descending.
    """
    sem = semantic_search(context_window, faiss_idx, k)
    lex = bm25_search(sentence, bm25, k)
    all_ids = set(sem) | set(lex)
    results = []
    for idx in all_ids:
        s = sem.get(idx, 0.0)
        b = lex.get(idx, 0.0)
        final = sem_weight * s + bm25_weight * b
        results.append((idx, final, s, b))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:k]


def hardcopy_search(sentence, top_k=5):
    """
    Searches the hard-copy library using simple keyword overlap.
    Returns the top_k most relevant entries as pseudo-candidates
    in the same format as reranker output, so the review panel
    can display them alongside FAISS/BM25 candidates without
    any special handling.
    """
    from pipeline.hardcopy import get_all, to_row
    sources = get_all()
    if not sources:
        return []

    # Build mini BM25 index from titles, authors, and notes
    texts = [
        ' '.join([s.get('title', ''), s.get('author', ''), s.get('notes', '')])
        for s in sources
    ]
    bm25 = BM25Okapi([t.lower().split() for t in texts])
    scores = bm25.get_scores(sentence.lower().split())

    results = []
    for i, (source, score) in enumerate(zip(sources, scores)):
        if score > 0:
            results.append({
                'para_idx':     -1,  # Sentinel — not a FAISS index
                'hybrid_score': float(score),
                'sem_score':    0.0,
                'bm25_score':   float(score),
                'ce_score':     0.0,
                'confidence':   float(score) * 0.7,  # Conservative for unindexed
                'source_type':  'hardcopy',
                'hardcopy_id':  source.get('source_id'),
                'row':          list(to_row(source)),
                'validation':   {}
            })
    results.sort(key=lambda x: x['confidence'], reverse=True)
    return results[:top_k]
