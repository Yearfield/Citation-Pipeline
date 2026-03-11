# pipeline/indexer.py
import os, json, pickle, sqlite3, re
import pdfplumber, pytesseract
from PIL import Image
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode


MODEL = 'all-MiniLM-L6-v2'


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS paragraphs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   TEXT,
            entrytype   TEXT,
            author      TEXT,
            title       TEXT,
            year        TEXT,
            journal     TEXT,
            volume      TEXT,
            issue       TEXT,
            publisher   TEXT,
            place       TEXT,
            editor      TEXT,
            booktitle   TEXT,
            institution TEXT,
            url         TEXT,
            urldate     TEXT,
            pdf_path    TEXT,
            page_printed TEXT,
            page_pdf    INTEGER,
            page_method TEXT,
            section     TEXT,
            para_index  INTEGER,
            text        TEXT
        )
    ''')
    conn.execute('DELETE FROM paragraphs')
    conn.commit()
    return conn


def get_printed_page(page, page_num_pdf):
    # Strategy 1: PDF logical page labels
    try:
        label = page.page_number
        if label:
            return str(label), 'label'
    except Exception:
        pass
    # Strategy 2: OCR footer
    try:
        footer = page.crop((0, page.height * 0.92, page.width, page.height))
        img = footer.to_image(resolution=200)
        text = pytesseract.image_to_string(img.original)
        nums = re.findall(r'\b(\d{1,4})\b', text)
        if nums:
            return nums[-1], 'ocr'
    except Exception:
        pass
    # Strategy 3: Fallback — flagged as unverified
    return str(page_num_pdf + 1), 'pdf_index_unverified'


def extract_paragraphs(pdf_path, entry):
    results = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text(layout=True)
                if not text:
                    continue
                printed_page, method = get_printed_page(page, page_num)
                paras = [p.strip() for p in text.split('\n\n')
                         if len(p.strip()) > 80]
                for idx, para_text in enumerate(paras):
                    results.append({
                        'source_id':   entry.get('ID', ''),
                        'entrytype':   entry.get('ENTRYTYPE', 'misc'),
                        'author':      entry.get('author', ''),
                        'title':       entry.get('title', ''),
                        'year':        entry.get('year', ''),
                        'journal':     entry.get('journal', ''),
                        'volume':      entry.get('volume', ''),
                        'issue':       entry.get('number', ''),
                        'publisher':   entry.get('publisher', ''),
                        'place':       entry.get('address', ''),
                        'editor':      entry.get('editor', ''),
                        'booktitle':   entry.get('booktitle', ''),
                        'institution': entry.get('school', ''),
                        'url':         entry.get('url', ''),
                        'urldate':     entry.get('urldate', ''),
                        'pdf_path':    pdf_path,
                        'page_printed': printed_page,
                        'page_pdf':    page_num,
                        'page_method': method,
                        'section':     '',
                        'para_index':  idx,
                        'text':        para_text
                    })
    except Exception as e:
        print(f'  Error reading {pdf_path}: {e}')
    return results


def build_index(settings):
    model = SentenceTransformer(MODEL)
    conn = init_db(settings['db_path'])

    parser = BibTexParser()
    parser.customization = convert_to_unicode
    with open(settings['bib_path'], encoding='utf-8') as f:
        bib_db = bibtexparser.load(f, parser=parser)

    all_paras, all_texts = [], []

    for entry in bib_db.entries:
        file_field = entry.get('file', '')
        # Better BibTeX file format: :relative/path.pdf:PDF
        parts = file_field.split(':')
        filename = parts[1].strip() if len(parts) >= 2 else ''
        pdf_path = os.path.join(settings['pdfs_dir'], filename)

        if not filename or not os.path.exists(pdf_path):
            print(f'  SKIP — PDF not found: {entry.get("title","")[:50]}')
            continue

        print(f'  Indexing: {entry.get("title","")[:55]}...')
        paras = extract_paragraphs(pdf_path, entry)

        for p in paras:
            conn.execute('''
                INSERT INTO paragraphs
                (source_id, entrytype, author, title, year, journal,
                 volume, issue, publisher, place, editor, booktitle,
                 institution, url, urldate, pdf_path, page_printed,
                 page_pdf, page_method, section, para_index, text)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', tuple(p.values()))
            all_paras.append(p)
            all_texts.append(p['text'])

    conn.commit()
    conn.close()
    print(f'\nIndexed {len(all_texts)} paragraphs. Building search indices...')

    # FAISS semantic index
    embeddings = model.encode(
        all_texts,
        normalize_embeddings=True,
        batch_size=64,
        show_progress_bar=True
    )
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, settings['faiss_path'])

    # BM25 lexical index
    tokenised = [t.lower().split() for t in all_texts]
    bm25 = BM25Okapi(tokenised)
    with open(settings['bm25_path'], 'wb') as f:
        pickle.dump({'bm25': bm25, 'texts': all_texts}, f)

    print('Index build complete.')


if __name__ == '__main__':
    with open('config/settings.json') as f:
        settings = json.load(f)
    print('Building citation index...')
    build_index(settings)
