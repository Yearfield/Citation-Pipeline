# run.py
import sys, os, json, uuid, subprocess, webbrowser
from datetime import datetime
import pdfplumber

from pipeline.log import get_logger
from pipeline.processor import (preserve_original, create_working_copy,
                                  preflight, detect_triggers, get_context_window)
from pipeline.retrieval import load_indices, hybrid_search, hardcopy_search
from pipeline.reranker import rerank
from pipeline.validator import validate
from pipeline.formatter import build_citation, load_profile
from pipeline.incremental import filter_new, mark_confirmed
from pipeline.exporter import build_docx
from pipeline.style_parser import parse_style_pdf
from sqlalchemy import select, func
from pipeline.db import init as db_init, get_connection, paragraphs

logger = get_logger('citation_pipeline.run')

def load_settings():
    with open('config/settings.json') as f:
        return json.load(f)

def process(doc_path, style_profile_path=None, auto_accept_warnings=False):
    settings     = load_settings()
    profile_path = style_profile_path or settings['default_style_profile']
    profile      = load_profile(profile_path)

    logger.info('=== Citation Pipeline ===')
    logger.info('Document : %s', os.path.basename(doc_path))
    logger.info('Style    : %s', profile['style_name'])

    # 1. Archive original — always first
    file_hash = preserve_original(doc_path)
    working   = create_working_copy(doc_path)

    # 2. Read content
    with open(working, 'r', encoding='utf-8') as f:
        content = f.read()

    # 3. Pre-flight validation
    warnings = preflight(content)
    if warnings:
        logger.warning('Pre-flight warnings:')
        for w in warnings:
            logger.warning('  %s', w)
        if auto_accept_warnings:
            logger.info('Auto-accepting preflight warnings (--yes)')
        else:
            ans = input('\nContinue despite warnings? (yes/no): ')
            if ans.strip().lower() != 'yes':
                logger.info('Cancelled. Resolve warnings and rerun.')
                return

    # 4. Detect triggers — filter already-confirmed sentences
    all_triggers = detect_triggers(content)
    triggers     = filter_new(all_triggers)
    logger.info('Triggers found: %d  New: %d', len(all_triggers), len(triggers))
    if not triggers:
        logger.info('No new triggers to process.')
        return

    # 5. Load indices
    logger.info('='*70)
    logger.info('LOADING INDICES')
    logger.info('='*70)
    faiss_idx, bm25, texts, row_ids = load_indices(settings)
    db_init(settings['db_path'])
    logger.info('✓ Index Summary:')
    logger.info('  - FAISS vectors: %d documents', len(texts))
    logger.info('  - BM25 corpus: %d paragraphs', len(texts))
    logger.info('')
    if len(texts) == 0:
        logger.error('✗ ERROR: Index is empty! No PDFs have been processed.')
        logger.error('   Please run the indexer to build the index first.')
        logger.error('   Build → Build Index from the main menu.')
        return

    # Integrity check: database row count must match FAISS/BM25 paragraph count
    with get_connection() as conn:
        db_count = conn.execute(
            select(func.count()).select_from(paragraphs)
        ).scalar() or 0
    if db_count == 0:
        logger.error('✗ DATABASE IS EMPTY: FAISS/BM25 index has %d paragraphs but database has 0 rows.', len(texts))
        logger.error('  The indices are out of sync — the database was likely recreated without rebuilding the index.')
        logger.error('  Fix: Build → Build Index → Force Full Rebuild')
        return
    elif abs(db_count - len(texts)) > 5:
        logger.warning('⚠ INDEX/DB COUNT MISMATCH: FAISS has %d paragraphs, database has %d rows.', len(texts), db_count)
        logger.warning('  Results may be incomplete. Consider: Build → Build Index → Force Full Rebuild')

    logger.info('='*70)

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
    cw = settings.get('confidence_weights',
                      {'cross_encoder': 0.50, 'semantic': 0.30, 'bm25': 0.20})
    with get_connection() as conn:
        for i, trigger in enumerate(triggers):
            logger.info('┌─ \033[91mSTEP 1\033[0m: SENTENCE DETECTED')
            logger.info('│ [%d/%d] Sentence: "%s"', i+1, len(triggers), trigger['sentence'][:60])
            logger.info('│ Position: chars %d-%d', trigger['start'], trigger['end'])

            ctx    = get_context_window(content, trigger['start'], trigger['sentence'])
            logger.info('├─ \033[91mSTEP 2\033[0m: CONTEXT EXTRACTED')
            logger.info('│ Context (first 80 chars): "%s..."', ctx[:80])

            logger.info('├─ \033[91mSTEP 3\033[0m: RETRIEVAL — Hybrid Search')
            logger.info('│ Settings: semantic_weight=%.2f, bm25_weight=%.2f, k=%d',
                       w['semantic'], w['bm25'], settings.get('retrieval_top_k', 20))
            hybrid = hybrid_search(
                trigger['sentence'], ctx, faiss_idx, bm25,
                sem_weight=w['semantic'], bm25_weight=w['bm25'],
                k=settings.get('retrieval_top_k', 20)
            )
            logger.info('│ ✓ Hybrid results: %d candidates found', len(hybrid))
            if hybrid:
                for j, (para_idx, final_score, sem_score, bm25_score) in enumerate(hybrid[:3]):
                    logger.info('│   [%d] para_idx=%d | final=%.3f | sem=%.3f | bm25=%.3f',
                               j+1, para_idx, final_score, sem_score, bm25_score)
                if len(hybrid) > 3:
                    logger.info('│   ... and %d more', len(hybrid) - 3)
            else:
                logger.warning('│ ✗ NO CANDIDATES from hybrid search')

            logger.info('├─ \033[91mSTEP 4\033[0m: RERANKING')
            logger.info('│ Cross-encoder: top %d candidates pre-rerank, keeping top %d',
                       settings.get('rerank_candidates', 10), settings.get('rerank_top_k', 5))
            ranked = rerank(
                trigger['sentence'], hybrid, conn,
                row_ids=row_ids,
                top_k=settings.get('rerank_top_k', 5),
                candidates_pre_rerank=settings.get('rerank_candidates', 10),
                confidence_weights=cw,
            )
            logger.info('│ ✓ Reranked: %d candidates after cross-encoder', len(ranked))
            if not ranked:
                logger.warning('│ ✗ NO CANDIDATES after reranking')
            else:
                for j, cand in enumerate(ranked[:3]):
                    logger.info('│   [%d] conf=%.3f | ce=%.3f | sem=%.3f | bm25=%.3f',
                               j+1, cand['confidence'], cand['ce_score'],
                               cand['sem_score'], cand['bm25_score'])

            for cand in ranked:
                if cand.get('row'):
                    logger.info('├─ \033[91mSTEP 5\033[0m: VALIDATION')
                    logger.info('│ Source: %s (ID=%s)', cand['row'][4], cand['row'][1])
                    logger.info('│ PDF path: %s', cand['row'][20] if len(cand['row']) > 20 else '?')
                    cand['validation'] = validate(
                        trigger['sentence'], cand['row'][22],
                        min_keyword_overlap=settings.get('min_keyword_overlap', 2),
                    )
                    logger.info('│ Validation: %s', 'PASS' if cand['validation'].get('match') else 'FAIL')

                    logger.info('├─ \033[91mSTEP 6\033[0m: BUILD CITATION')
                    cand['citation_preview'], cand['unknowns'], cand['compliance_warnings'] = build_citation(
                        list(cand['row']), profile)
                    logger.info('│ Citation preview: %s...', cand['citation_preview'][:60])
                    if cand['unknowns']:
                        logger.warning('│ Unknown fields: %s', cand['unknowns'])
                    cand['row'] = list(cand['row'])  # JSON-serialisable
                else:
                    logger.warning('├─ STEP 5-6: SKIPPED (no row data)')

            # Merge hard-copy candidates
            logger.info('├─ \033[91mSTEP 7\033[0m: HARDCOPY LIBRARY SEARCH')
            hc_candidates  = hardcopy_search(
                trigger['sentence'],
                top_k=settings.get('rerank_top_k', 5),
                confidence_scale=settings.get('hardcopy_confidence_scale', 0.7),
            )
            logger.info('│ ✓ Hard-copy results: %d candidates', len(hc_candidates))
            if hc_candidates:
                for j, hc in enumerate(hc_candidates[:2]):
                    logger.info('│   [%d] %s (conf=%.3f)',
                               j+1, hc.get('row', ['?', '?', '?', '?', '?'])[4][:40],
                               hc['confidence'])

            logger.info('├─ \033[91mSTEP 8\033[0m: MERGE & FINALIZE')
            all_candidates = ranked + hc_candidates
            all_candidates.sort(key=lambda x: x['confidence'], reverse=True)
            logger.info('│ Total candidates (ranked + hardcopy): %d', len(all_candidates))
            logger.info('│ Keeping top %d for review', settings.get('rerank_top_k', 5))

            session['sentences'][str(i)] = trigger
            session['candidates'][str(i)] = all_candidates[:settings.get('rerank_top_k', 5)]
            logger.info('└─ COMPLETE\n')

    # 7. Save session and launch review
    sess_path = f'temp/session_{session_id}.json'
    with open(sess_path, 'w') as f:
        json.dump(session, f, indent=2, default=str)

    # Summary statistics
    logger.info('='*70)
    logger.info('PIPELINE COMPLETE — SUMMARY')
    logger.info('='*70)
    total_candidates = sum(len(session['candidates'].get(str(i), [])) for i in range(len(triggers)))
    logger.info('✓ Sentences processed: %d', len(triggers))
    logger.info('✓ Total candidates found: %d', total_candidates)
    logger.info('✓ Average candidates per sentence: %.1f', total_candidates / len(triggers) if triggers else 0)

    # Check for sentences with no candidates
    empty_sentences = sum(1 for i in range(len(triggers)) if len(session['candidates'].get(str(i), [])) == 0)
    if empty_sentences > 0:
        logger.warning('⚠ Sentences with NO candidates: %d', empty_sentences)
        logger.warning('  These sentences will need manual citation in the review panel.')

    logger.info('✓ Session saved: temp/session_%s.json', session_id)
    logger.info('='*70)
    logger.info('')
    logger.info('Starting review interface...')
    subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'review.app:app',
         '--port', '8000', '--reload'],
        cwd=os.getcwd()
    )
    import time; time.sleep(2)
    webbrowser.open(f'http://localhost:8000/review/{session_id}')
    return session_id

def export(doc_path, session_id=None):
    settings = load_settings()
    profile  = load_profile(settings['default_style_profile'])
    if not session_id:
        # Find most recent session for this document
        doc_name = os.path.basename(doc_path)
        sessions = [f for f in os.listdir('temp') if f.startswith('session_')]
        for sf in sorted(sessions, reverse=True):
            with open(f'temp/{sf}') as f:
                s = json.load(f)
            if s.get('document') == doc_name:
                session_id = s['session_id']
                break
    if not session_id:
        logger.warning('No session found for this document.')
        return
    build_docx(doc_path, session_id, profile)

def ingest_style(pdf_path, style_name):
    """
    Reads a citation style guide PDF, parses templates from it,
    and saves the draft to config/styles_library.json.
    """
    logger.info('Reading style guide: %s', pdf_path)
    raw_text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    raw_text += page_text + "\n"
    except Exception as e:
        logger.error('Error reading PDF: %s', e)
        return

    logger.info('Parsing citation templates...')
    # Add a unique ID
    style_id = str(uuid.uuid4())[:8]
    parsed_profile = parse_style_pdf(raw_text, suggested_name=style_name)
    parsed_profile['id'] = style_id

    # Load existing library
    lib_path = 'config/styles_library.json'
    library = {}
    if os.path.exists(lib_path):
        with open(lib_path, 'r', encoding='utf-8') as f:
            try:
                library = json.load(f)
            except json.JSONDecodeError:
                pass

    # Save the new profile
    library[style_id] = parsed_profile
    with open(lib_path, 'w', encoding='utf-8') as f:
        json.dump(library, f, indent=4)

    logger.info("Style '%s' successfully ingested and saved to library.", style_name)
    logger.info('NOTE: Templates were extracted programmatically. Review styles_library.json')
    logger.info('to correct any {placeholders} before using this style.')

def source_coverage_report(session_id):
    """
    Generates a plain-text coverage report for a completed session.
    Saved to audit_logs/{session_id}_coverage.txt.
    """
    from collections import Counter
    with open(f'audit_logs/{session_id}.json') as f:
        audit = json.load(f)
    cited = Counter(
        e['source_id'] for e in audit
        if e['action'] == 'approved' and e.get('source_id')
    )
    settings    = load_settings()
    db_init(settings['db_path'])
    with get_connection() as conn:
        all_sources = conn.execute(
            select(
                paragraphs.c.source_id,
                paragraphs.c.title,
                paragraphs.c.author,
            ).distinct()
        ).fetchall()

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
    logger.info('Coverage report saved: %s', report_path)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        logger.error('Usage:')
        logger.error('  python run.py process  <document.md> [style_profile.json]')
        logger.error('  python run.py export   <document.md> [session_id]')
        logger.error('  python run.py coverage <session_id>')
        logger.error('  python run.py ingest_style <style_guide.pdf> <Style Name>')
        sys.exit(1)

    cmd = sys.argv[1]

    try:
        if cmd == 'process':
            if len(sys.argv) < 3:
                logger.error('Usage: python run.py process <document.md> [style_profile.json] [--yes]')
                sys.exit(1)
            auto_yes = '--yes' in sys.argv
            args = [a for a in sys.argv[2:] if a != '--yes']
            doc_path = args[0]
            style    = args[1] if len(args) > 1 else None
            process(doc_path, style, auto_accept_warnings=auto_yes)

        elif cmd == 'export':
            if len(sys.argv) < 3:
                logger.error('Usage: python run.py export <document.md> [session_id]')
                sys.exit(1)
            doc_path   = sys.argv[2]
            session_id = sys.argv[3] if len(sys.argv) > 3 else None
            export(doc_path, session_id)

        elif cmd == 'coverage':
            if len(sys.argv) < 3:
                logger.error('Usage: python run.py coverage <session_id>')
                sys.exit(1)
            source_coverage_report(sys.argv[2])

        elif cmd == 'ingest_style':
            if len(sys.argv) < 4:
                logger.error('Usage: python run.py ingest_style <style_guide.pdf> "Style Name"')
                sys.exit(1)
            ingest_style(sys.argv[2], sys.argv[3])

        else:
            logger.error('Unknown command: %s', cmd)
            sys.exit(1)

    except Exception:
        logger.exception('Unhandled error in run.py')
        sys.exit(1)
