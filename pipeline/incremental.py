# pipeline/incremental.py
import hashlib, json, os


CONFIRMED_PATH = 'index/confirmed_sentences.json'


def _load():
    if os.path.exists(CONFIRMED_PATH):
        with open(CONFIRMED_PATH) as f:
            return json.load(f)
    return {}


def mark_confirmed(sentence, session_id, citation):
    """Permanently records a confirmed sentence so it is never re-processed."""
    confirmed = _load()
    key = hashlib.sha256(sentence.encode()).hexdigest()
    confirmed[key] = {
        'sentence': sentence[:120],
        'session_id': session_id,
        'citation': citation
    }
    with open(CONFIRMED_PATH, 'w') as f:
        json.dump(confirmed, f, indent=2)


def filter_new(triggers):
    """
    Returns only triggers not already confirmed.
    Already-confirmed triggers are frozen and will not be re-processed.
    """
    confirmed = _load()
    return [
        t for t in triggers
        if hashlib.sha256(t['sentence'].encode()).hexdigest() not in confirmed
    ]


def clear_document(doc_name):
    """
    Removes confirmed entries for a specific document.
    Use this if you need to re-process an entire document from scratch.
    """
    confirmed = _load()
    # Requires that session metadata records the document name
    updated = {k: v for k, v in confirmed.items()
               if v.get('doc') != doc_name}
    with open(CONFIRMED_PATH, 'w') as f:
        json.dump(updated, f, indent=2)
