# pipeline/auditor.py
import json, os, hashlib
from datetime import datetime


def log(session_id, sentence, action, row=None,
         override_page=None, denial_reason=None,
         scores=None):
    """
    Appends one decision record to audit_logs/{session_id}.json.
    Called on every Approve and every Deny in the review panel.
    """
    entry = {
        'timestamp':      datetime.now().isoformat(),
        'session_id':     session_id,
        'sentence':       sentence,
        'sentence_hash':  hashlib.sha256(sentence.encode()).hexdigest(),
        'action':         action,
        'source_id':      row[1]  if row else None,
        'source_title':   row[4]  if row else None,
        'source_year':    row[5]  if row else None,
        'page_extracted': row[17] if row else None,
        'page_method':    row[19] if row else None,
        'page_used':      override_page or (row[17] if row else None),
        'page_overridden': override_page is not None,
        'scores':         scores or {},
        'denial_reason':  denial_reason
    }
    path = os.path.join('audit_logs', f'{session_id}.json')
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)
    existing.append(entry)
    with open(path, 'w') as f:
        json.dump(existing, f, indent=2)


def get_confirmed(session_id):
    """Returns all approved decisions for a given session."""
    path = os.path.join('audit_logs', f'{session_id}.json')
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [e for e in json.load(f) if e['action'] == 'approved']
