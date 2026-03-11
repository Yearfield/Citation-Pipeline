# run.py
import sys, os, json, uuid, sqlite3, subprocess, webbrowser
from datetime import datetime
from pipeline.processor import (preserve_original, create_working_copy,
    preflight, detect_triggers, get_context_window)
from pipeline.retrieval import load_indices, hybrid_search, hardcopy_search
from pipeline.reranker import rerank
from pipeline.validator import validate
from pipeline.formatter import build_citation, load_profile
from pipeline.incremental import filter_new, mark_confirmed
from pipeline.exporter import build_docx


def load_settings():
    with open('config/settings.json') as f:
        return json.load(f)


def process(doc_path, style_profile_path=None):
    settings = load_settings()
    profile_path = style_profile_path or settings['default_style_profile']
    profile = load_profile(profile_path)

    print(f'\n=== Citation Pipeline ===')
    print(f'Document : {os.path.basename(doc_path)}')
    print(f'Style    : {profile["style_name"]}')

    # 1. Archive original — always first
    file_hash = preserve_original(doc_path)
    working   = create_working_copy(doc_path)

    # 2. Read content
    with open(working, 'r', encoding='utf-8') as f:
        content = f.read()

    # 3. Pre-flight validation
    warnings = preflight(content)
    if warnings:
        print('\n  Pre-flight warnings:')
        for w in warnings:
            print(f'   {w}')
        ans = input('\nContinue despite warnings? (yes/no): ')
        if ans.strip().lower() != 'yes':
            print('Cancelled. Resolve warnings and rerun.')
            return

    # 4. Detect triggers — filter already-confirmed sentences
    all_triggers = detect_triggers(content)
    triggers     = filter_new(all_triggers)
    print(f'\nTriggers found: {len(all_triggers)}  New: {len(triggers)}')
    if not triggers:
        print('No new triggers to process.')
        return

    # 5. Load indices
    print('Loading indices...')
    faiss_idx, bm25, texts = load_indices(settings)
    conn = sqlite3.connect(settings['db_path'])

    # 6. Retrieve and rank candidates
    session_id = str(uuid.uuid4())[:8]
    session = {
        'session_id':    session_id,
        'document':      os.path.basename(doc_path),
        'document_path': working,
        'document_hash': file_hash,
        'style_profile': profile_path,
        'created':       datetime.now().isoformat(),
        'sentences':     {},
        'candidates':    {},
        'confirmed':     {}
    }

    w = settings.get('hybrid_weights', {'semantic': 0.6, 'bm25': 0.4})

    for i, trigger in enumerate(triggers):
        print(f'  [{i+1}/{len(triggers)}] {trigger["sentence"][:60]}...')
        ctx = get_context_window(content, trigger['start'], trigger['sentence'])
        hybrid = hybrid_search(
            trigger['sentence'], ctx, faiss_idx, bm25,
            sem_weight=w['semantic'], bm25_weight=w['bm25'],
            k=settings.get('retrieval_top_k', 20)
        )
        ranked = rerank(trigger['sentence'], hybrid, conn,
                         top_k=settings.get('rerank_top_k', 5))
        for cand in ranked:
            if cand.get('row'):
                cand['validation'] = validate(
                    trigger['sentence'], cand['row'][22])
                cand['citation_preview'], cand['unknowns'] = build_citation(
                    list(cand['row']), profile)
                cand['row'] = list(cand['row'])  # JSON-serialisable

        # Merge hard-copy candidates
        hc_candidates = hardcopy_search(trigger['sentence'])
        for hc in hc_candidates:
            if hc.get('row'):
                hc['citation_preview'], hc['unknowns'] = build_citation(
                    list(hc['row']), profile)
                hc['row'] = list(hc['row'])

        all_candidates = ranked + hc_candidates
        all_candidates.sort(key=lambda x: x.get('confidence', 0), reverse=True)

        session['sentences'][str(i)] = trigger
        session['candidates'][str(i)] = all_candidates[:settings.get('rerank_top_k', 5)]

    conn.close()

    # 7. Save session and launch review
    sess_path = f'temp/session_{session_id}.json'
    with open(sess_path, 'w') as f:
        json.dump(session, f, indent=2, default=str)

    print(f'\nSession ID: {session_id}')
    print('Starting review interface...')
    subprocess.Popen([sys.executable, '-m', 'uvicorn',
                       'review.app:app',
                       '--port', str(settings.get('review_port', 8000)),
                       '--reload'], cwd=os.getcwd())
    import time; time.sleep(2)
    webbrowser.open(f'http://localhost:{settings.get("review_port", 8000)}/review/{session_id}')


def export(doc_path, session_id=None):
    settings = load_settings()
    profile  = load_profile(settings['default_style_profile'])
    if not session_id:
        # Find most recent session for this document
        doc_name = os.path.basename(doc_path)
        sessions = [f for f in os.listdir('temp') if f.startswith('session_')]
        for sf in sorted(sessions, reverse=True):
            try:
                with open(f'temp/{sf}') as f:
                    s = json.load(f)
                if s.get('document') == doc_name:
                    session_id = s['session_id']
                    break
            except Exception:
                continue
    if not session_id:
        print('No session found for this document.')
        return
    build_docx(doc_path, session_id, profile)


def source_coverage_report(session_id):
    """
    Generates a plain-text coverage report for a completed session.
    Saved to audit_logs/{session_id}_coverage.txt.
    """
    from collections import Counter

    audit_path = f'audit_logs/{session_id}.json'
    if not os.path.exists(audit_path):
        print(f'No audit log found for session {session_id}.')
        return

    with open(audit_path) as f:
        audit = json.load(f)

    cited = Counter(
        e['source_id'] for e in audit
        if e['action'] == 'approved' and e.get('source_id')
    )

    settings = load_settings()
    conn = sqlite3.connect(settings['db_path'])
    all_sources = conn.execute(
        'SELECT DISTINCT source_id, title, author FROM paragraphs'
    ).fetchall()
    conn.close()

    lines = [
        f'Source Coverage Report — Session {session_id}',
        '=' * 60,
        '',
        'CITED IN THIS DOCUMENT:',
    ]
    for src_id, title, author in sorted(all_sources, key=lambda x: x[1] or ''):
        count = cited.get(src_id, 0)
        if count > 0:
            lines.append(f'  [{count}x]  {author or "?"} — {title or src_id}')

    lines += ['', 'NOT CITED (present in library but unmatched):', '']
    for src_id, title, author in sorted(all_sources, key=lambda x: x[1] or ''):
        if cited.get(src_id, 0) == 0:
            lines.append(f'  {author or "?"} — {title or src_id}')

    report_path = f'audit_logs/{session_id}_coverage.txt'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f'Coverage report saved: {report_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage:')
        print('  python run.py process  <document.md> [style_profile.json]')
        print('  python run.py export   <document.md> [session_id]')
        print('  python run.py coverage <session_id>')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'process':
        if len(sys.argv) < 3:
            print('Usage: python run.py process <document.md> [style_profile.json]')
            sys.exit(1)
        doc_path = sys.argv[2]
        style    = sys.argv[3] if len(sys.argv) > 3 else None
        process(doc_path, style)

    elif cmd == 'export':
        if len(sys.argv) < 3:
            print('Usage: python run.py export <document.md> [session_id]')
            sys.exit(1)
        doc_path   = sys.argv[2]
        session_id = sys.argv[3] if len(sys.argv) > 3 else None
        export(doc_path, session_id)

    elif cmd == 'coverage':
        if len(sys.argv) < 3:
            print('Usage: python run.py coverage <session_id>')
            sys.exit(1)
        source_coverage_report(sys.argv[2])

    else:
        print(f'Unknown command: {cmd}')
        print('Available commands: process, export, coverage')
        sys.exit(1)
