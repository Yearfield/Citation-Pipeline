# pipeline/hardcopy.py
import json, os, uuid, hashlib
from datetime import datetime


LIBRARY_PATH = 'index/hardcopy_library.json'


def _load():
    if os.path.exists(LIBRARY_PATH):
        with open(LIBRARY_PATH) as f:
            return json.load(f)
    return {}


def _save(library):
    tmp = LIBRARY_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(library, f, indent=2)
    os.replace(tmp, LIBRARY_PATH)


def add_source(fields):
    """
    Adds a new hard-copy source to the persistent library.
    fields is a dict with keys: entrytype, author, title, year,
    publisher, place, journal, volume, issue, editor, booktitle,
    institution, url, notes, and any other BibTeX-compatible fields.
    Returns the new source's unique ID.
    """
    library = _load()
    source_id = 'HC_' + str(uuid.uuid4())[:8].upper()
    library[source_id] = {
        **fields,
        'source_id':  source_id,
        'added':      datetime.now().isoformat(),
        'last_used':  None,
        'use_count':  0,
        'origin':     'hardcopy'
    }
    _save(library)
    return source_id


def update_source(source_id, fields):
    """Updates an existing hard-copy source record."""
    library = _load()
    if source_id in library:
        library[source_id].update(fields)
        library[source_id]['modified'] = datetime.now().isoformat()
        _save(library)
        return True
    return False


def record_use(source_id):
    """Records a citation use — increments counter and updates last_used date."""
    library = _load()
    if source_id in library:
        library[source_id]['use_count'] = library[source_id].get('use_count', 0) + 1
        library[source_id]['last_used'] = datetime.now().isoformat()
        _save(library)


def get_all():
    """Returns all hard-copy sources as a list of dicts."""
    return list(_load().values())


def get_by_id(source_id):
    return _load().get(source_id)


def search(query):
    """
    Simple keyword search across author, title, and notes fields.
    Returns matching sources sorted by use_count descending.
    Used by the retrieval engine and the review panel library browser.
    """
    query_lower = query.lower()
    results = []
    for src in _load().values():
        searchable = ' '.join([
            src.get('author', ''),
            src.get('title', ''),
            src.get('notes', '')
        ]).lower()
        if query_lower in searchable:
            results.append(src)
    results.sort(key=lambda x: x.get('use_count', 0), reverse=True)
    return results


def to_row(source):
    """
    Converts a hard-copy source dict to the same tuple format
    as a paragraphs database row, so the formatter can process
    hard-copy sources identically to Zotero-sourced ones.
    Column order matches the paragraphs table schema.
    """
    return (
        None,                          # id
        source.get('source_id', ''),   # source_id
        source.get('entrytype', 'book'),# entrytype
        source.get('author', ''),       # author
        source.get('title', ''),        # title
        source.get('year', ''),         # year
        source.get('journal', ''),      # journal
        source.get('volume', ''),       # volume
        source.get('issue', ''),        # issue
        source.get('publisher', ''),    # publisher
        source.get('place', ''),        # place
        source.get('editor', ''),       # editor
        source.get('booktitle', ''),    # booktitle
        source.get('institution', ''),  # institution
        source.get('url', ''),          # url
        source.get('urldate', ''),      # urldate
        'hardcopy',                    # pdf_path (sentinel)
        source.get('page', ''),         # page_printed
        None,                          # page_pdf
        'user_entered',                # page_method
        '',                            # section
        0,                             # para_index
        source.get('notes', source.get('title', ''))  # text (used for BM25 matching)
    )
