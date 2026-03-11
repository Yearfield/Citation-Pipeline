# pipeline/tab_importer.py
import requests, json, re, sys
from urllib.parse import urlparse


CHROME_DEBUG_URL = 'http://localhost:9222/json/list'
ZOTERO_TRANSLATE  = 'http://localhost:23119/web'
ZOTERO_API        = 'http://localhost:23119/zotero'


# Domains and URL patterns that are never citable sources
SKIP_PATTERNS = [
    r'^chrome://', r'^chrome-extension://', r'^about:',
    r'^file://', r'^localhost', r'^127\.',
    r'google\.com/search', r'google\.com/#',
    r'mail\.google', r'gmail\.com',
    r'twitter\.com', r'x\.com', r'facebook\.com',
    r'instagram\.com', r'reddit\.com',
    r'youtube\.com', r'netflix\.com',
    r'amazon\.com(?!/dp/|/gp/product)',
]


def get_open_tabs():
    """
    Reads open Chrome tabs via the remote debugging port.
    Returns list of {url, title} dicts, filtered to citable candidates.
    """
    try:
        response = requests.get(CHROME_DEBUG_URL, timeout=3)
        tabs = response.json()
    except Exception as e:
        print(f'Cannot reach Chrome debugging port: {e}')
        print('Ensure Chrome was launched with --remote-debugging-port=9222')
        return []

    candidates = []
    for tab in tabs:
        url   = tab.get('url', '')
        title = tab.get('title', '')
        if not url or _should_skip(url):
            continue
        candidates.append({'url': url, 'title': title})
    return candidates


def _should_skip(url):
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def translate_via_zotero(url):
    """
    Uses Zotero's translation server to extract metadata from a URL.
    This is the same engine the Zotero Connector uses in the browser.
    Returns a Zotero item dict or None if translation fails.
    """
    try:
        r = requests.post(
            ZOTERO_TRANSLATE,
            json={'url': url},
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        if r.status_code == 200:
            items = r.json()
            if items and isinstance(items, list):
                return items[0]
    except Exception:
        pass
    return None


def scrape_metadata(url):
    """
    Fallback metadata extraction by scraping Open Graph, Dublin Core,
    and standard HTML meta tags from the page directly.
    """
    try:
        r = requests.get(url, timeout=8,
                          headers={'User-Agent': 'Mozilla/5.0'})
        html = r.text

        def meta(prop, attr='name'):
            m = re.search(
                f'<meta[^>]+{attr}=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']*)["\']',
                html, re.I)
            return m.group(1) if m else ''

        title   = meta('og:title', 'property') or meta('title') or ''
        author  = meta('author') or meta('dc.creator') or ''
        date    = meta('date') or meta('dc.date') or meta('article:published_time') or ''
        year    = date[:4] if len(date) >= 4 else ''
        site    = urlparse(url).netloc
        return {
            'itemType': 'webpage',
            'title':    title,
            'author':   author,
            'year':     year,
            'url':      url,
            'websiteTitle': site,
            'accessDate': '',
            'source':   'html_scrape'
        }
    except Exception as e:
        return {'itemType': 'webpage', 'url': url, 'title': '', 'source': 'failed'}


def add_to_zotero(item):
    """
    Adds a confirmed item to Zotero via the local API.
    Zotero then updates library.bib automatically via Better BibTeX.
    """
    try:
        r = requests.post(
            f'{ZOTERO_API}/items',
            json=[item],
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        print(f'Failed to add to Zotero: {e}')
        return False


def run_import(auto_add=False):
    """
    Main import function.
    auto_add=False means each item is presented for review before import.
    auto_add=True adds all translated items without individual confirmation.
    Always use auto_add=False for academic citation work.
    """
    print('Scanning open Chrome tabs...')
    tabs = get_open_tabs()
    print(f'Found {len(tabs)} candidate tabs.')

    results = []
    for tab in tabs:
        print(f'  Processing: {tab["url"][:70]}...')
        metadata = translate_via_zotero(tab['url'])
        source = 'zotero_translation'
        if not metadata:
            metadata = scrape_metadata(tab['url'])
            source = metadata.get('source', 'html_scrape')
        metadata['_source'] = source
        metadata['_tab_title'] = tab['title']
        results.append(metadata)

    # Save results for review panel
    with open('temp/tab_import_pending.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nTab import scan complete. {len(results)} items ready for review.')
    print('Open the review panel and click Tab Import to confirm items.')
    return results


if __name__ == '__main__':
    run_import()
