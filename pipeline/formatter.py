# pipeline/formatter.py
import json, re


def load_profile(profile_path):
    with open(profile_path) as f:
        return json.load(f)


def format_authors(author_string, sep=' and '):
    if not author_string or author_string == '[UNKNOWN — VERIFY]':
        return '[UNKNOWN — VERIFY]'
    authors = re.split(r'\s+and\s+', author_string, flags=re.IGNORECASE)
    authors = [a.strip() for a in authors if a.strip()]
    if len(authors) == 1:
        return authors[0]
    elif len(authors) == 2:
        return sep.join(authors)
    else:
        return ', '.join(authors[:-1]) + sep + authors[-1]


def build_citation(row, profile, override_page=None):
    """
    Constructs a full citation string from a paragraph database row
    and a style profile. Returns (citation_string, [unknown_fields]).
    """
    UNK = profile.get('unknown_placeholder', '[UNKNOWN — VERIFY]')
    entrytype = (row[2] or 'misc').lower()
    templates = profile.get('templates', {})
    template = templates.get(entrytype, templates.get('misc', '{author}, {title} ({year}), {page}.'))

    page = override_page or row[17] or UNK  # page_printed
    if row[19] == 'pdf_index_unverified' and not override_page:
        page = page + ' [page unverified — check PDF]'

    fields = {
        'author':      format_authors(row[3] or UNK, profile.get('author_separator', ' and ')),
        'title':       row[4]  or UNK,
        'year':        row[5]  or UNK,
        'journal':     row[6]  or UNK,
        'volume':      row[7]  or UNK,
        'issue':       row[8]  or UNK,
        'publisher':   row[9]  or UNK,
        'place':       row[10] or UNK,
        'editor':      row[11] or UNK,
        'booktitle':   row[12] or UNK,
        'institution': row[13] or UNK,
        'url':         row[14] or UNK,
        'accessdate':  row[15] or UNK,
        'page':        page
    }

    citation = template
    for key, val in fields.items():
        citation = citation.replace('{' + key + '}', str(val))

    unknown_fields = [k for k, v in fields.items()
                       if v == UNK and '{' + k + '}' in template]
    return citation, unknown_fields
