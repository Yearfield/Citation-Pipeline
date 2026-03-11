# review/app.py
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
import sqlite3, json, os, re, io
from datetime import datetime

from pipeline.formatter import build_citation, load_profile
from pipeline.auditor import log as audit_log, get_confirmed
from pipeline.validator import validate
from pipeline.style_parser import parse_style_pdf as _parse_style_pdf

app = FastAPI()
templates = Jinja2Templates(directory='review/templates')

STYLES_LIBRARY_PATH = "config/styles_library.json"


# ── Styles library helpers ─────────────────────────────────────────────────────

def load_styles_library():
    if os.path.exists(STYLES_LIBRARY_PATH):
        with open(STYLES_LIBRARY_PATH) as f:
            return json.load(f)
    return {}


def save_styles_library(library):
    with open(STYLES_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)


# ── Review interface ───────────────────────────────────────────────────────────

@app.get('/review/{session_id}', response_class=HTMLResponse)
async def review(request: Request, session_id: str):
    session_path = f'temp/session_{session_id}.json'
    if not os.path.exists(session_path):
        return HTMLResponse('<h2>Session not found.</h2>', status_code=404)
    with open(session_path) as f:
        session = json.load(f)
    return templates.TemplateResponse('review.html', {
        'request': request, 'session': session
    })


@app.post('/approve')
async def approve(request: Request):
    data = await request.json()
    session_id    = data['session_id']
    sentence_id   = data['sentence_id']
    para_idx      = data['para_idx']
    override_page = data.get('override_page')
    unknown_edits = data.get('unknown_edits', {})

    session_path = f'temp/session_{session_id}.json'
    with open(session_path) as f:
        session = json.load(f)

    cand = next(
        (c for c in session['candidates'].get(str(sentence_id), [])
         if c.get('para_idx') == para_idx),
        None
    )

    if cand and cand.get('row'):
        profile = load_profile(session['style_profile'])
        row = list(cand['row'])
        if override_page:
            row[17] = override_page
            row[19] = 'user_override'
        citation, _ = build_citation(row, profile, override_page)
        # Apply any inline edits to unknown fields
        for field, value in unknown_edits.items():
            citation = citation.replace('[UNKNOWN — VERIFY]', value, 1)

        session.setdefault('confirmed', {})
        session['confirmed'][str(sentence_id)] = {
            'citation':    citation,
            'row':         row,
            'override_page': override_page
        }
        with open(session_path, 'w') as f:
            json.dump(session, f)

        audit_log(session_id,
                  session['sentences'][str(sentence_id)]['sentence'],
                  'approved', row, override_page,
                  scores={
                      'sem':        cand.get('sem_score'),
                      'bm25':       cand.get('bm25_score'),
                      'ce':         cand.get('ce_score'),
                      'confidence': cand.get('confidence')
                  })

    return JSONResponse({'status': 'approved'})


@app.post('/deny')
async def deny(request: Request):
    data = await request.json()
    reason = data.get('reason', 'unspecified')
    audit_log(data['session_id'],
              data.get('sentence', ''), 'denied',
              denial_reason=reason)
    return JSONResponse({'status': 'denied', 'reason': reason})


@app.get('/pdf_page')
async def pdf_page(pdf_path: str, page_num: int):
    """Renders a PDF page as base64 PNG for display in the review panel."""
    try:
        import fitz, base64
        doc = fitz.open(pdf_path)
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        return JSONResponse({'image': base64.b64encode(pix.tobytes('png')).decode()})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


# ── Tab import ─────────────────────────────────────────────────────────────────

@app.get('/tab_import_queue', response_class=HTMLResponse)
async def tab_queue(request: Request):
    pending_path = 'temp/tab_import_pending.json'
    if not os.path.exists(pending_path):
        items = []
    else:
        with open(pending_path) as f:
            items = json.load(f)
    return templates.TemplateResponse('review.html', {
        'request': request,
        'tab_import_items': items,
        'session': {'session_id': 'tab_import'}
    })


@app.post('/tab_import/confirm')
async def confirm_tab_import(request: Request):
    data = await request.json()
    item = data['item']
    from pipeline.tab_importer import add_to_zotero
    success = add_to_zotero(item)
    if success:
        pending_path = 'temp/tab_import_pending.json'
        if os.path.exists(pending_path):
            with open(pending_path) as f:
                pending = json.load(f)
            pending = [p for p in pending if p.get('url') != item.get('url')]
            with open(pending_path, 'w') as f:
                json.dump(pending, f)
    return JSONResponse({'success': success})


# ── Hard-copy library endpoints ────────────────────────────────────────────────

@app.get('/hardcopy/search')
async def search_hardcopy(q: str = ''):
    from pipeline.hardcopy import search as hc_search
    results = hc_search(q) if q else []
    return JSONResponse({'results': results})


@app.get('/hardcopy/all')
async def all_hardcopy():
    from pipeline.hardcopy import get_all
    return JSONResponse({'sources': get_all()})


@app.post('/hardcopy/add')
async def add_hardcopy(request: Request):
    data = await request.json()
    from pipeline.hardcopy import add_source
    source_id = add_source(data['fields'])
    return JSONResponse({'source_id': source_id})


@app.post('/hardcopy/cite')
async def cite_hardcopy(request: Request):
    """
    Confirms a citation from the hard-copy library for a specific sentence.
    Updates use_count, logs the decision, and adds to session confirmed map.
    """
    data = await request.json()
    session_id  = data['session_id']
    sentence_id = data['sentence_id']
    source_id   = data['source_id']
    page        = data.get('page', '')

    from pipeline.hardcopy import get_by_id, to_row, record_use

    source = get_by_id(source_id)
    if not source:
        return JSONResponse({'error': 'source not found'}, status_code=404)

    source['page'] = page
    row = list(to_row(source))

    session_path = f'temp/session_{session_id}.json'
    with open(session_path) as f:
        session = json.load(f)

    profile = load_profile(session['style_profile'])
    citation, _ = build_citation(row, profile, page)

    session.setdefault('confirmed', {})
    session['confirmed'][str(sentence_id)] = {
        'citation':    citation,
        'row':         row,
        'source_type': 'hardcopy',
        'source_id':   source_id
    }
    with open(session_path, 'w') as f:
        json.dump(session, f)

    record_use(source_id)
    audit_log(session_id,
              session['sentences'][str(sentence_id)]['sentence'],
              'approved', row, page,
              scores={'source_type': 'hardcopy', 'source_id': source_id})
    return JSONResponse({'status': 'confirmed', 'citation': citation})


# ── Citation Style Manager endpoints (Change 1) ────────────────────────────────

@app.post('/styles/parse_pdf')
async def parse_style_pdf_endpoint(file: UploadFile = File(...)):
    """
    Receives a PDF upload, extracts its text, runs the style parser,
    and returns the parsed result for user review. Nothing is saved yet.
    """
    try:
        import pdfplumber
        contents = await file.read()
        text_pages = []
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text_pages.append(extracted)
        raw_text = "\n".join(text_pages)
        suggested_name = os.path.splitext(file.filename)[0].replace("_", " ").replace("-", " ")
        parsed = _parse_style_pdf(raw_text, suggested_name)
        parsed["source_pdf"] = file.filename
        return JSONResponse({"parsed": parsed, "filename": file.filename})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post('/styles/save')
async def save_style(request: Request):
    """
    Receives the user-reviewed and corrected style profile and saves it
    permanently to styles_library.json.
    """
    data = await request.json()
    style = data["style"]
    # Generate a stable ID from the name
    style_id = re.sub(r"[^a-zA-Z0-9]", "_", style.get("name", "style")).strip("_")[:40]
    style_id = style_id + "_" + datetime.now().strftime("%Y%m%d")
    style["id"]    = style_id
    style["added"] = datetime.now().isoformat()
    # Remove raw_examples (review artefact only, not stored)
    style.pop("raw_examples", None)
    library = load_styles_library()
    library[style_id] = style
    save_styles_library(library)
    return JSONResponse({"status": "saved", "id": style_id})


@app.get('/styles/list')
async def list_styles():
    """Returns all saved styles for the dropdown in the main UI."""
    library = load_styles_library()
    return JSONResponse({"styles": [
        {"id": s["id"], "name": s["name"], "added": s.get("added", "")}
        for s in library.values()
    ]})


@app.get('/styles/{style_id}')
async def get_style(style_id: str):
    """Returns a single style for editing."""
    library = load_styles_library()
    style = library.get(style_id)
    if not style:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"style": style})


@app.delete('/styles/{style_id}')
async def delete_style(style_id: str):
    library = load_styles_library()
    if style_id in library:
        del library[style_id]
        save_styles_library(library)
    return JSONResponse({"status": "deleted"})
