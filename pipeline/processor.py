# pipeline/processor.py
import re, hashlib, shutil, os, json
from datetime import datetime


ABBREVIATIONS = [
    'et al', 'ibid', 'op cit', 'loc cit', 'cf', 'viz',
    'e.g', 'i.e', 'etc', 'vol', 'no', 'pp', 'ed', 'eds',
    'trans', 'rev', 'Dr', 'Prof', 'Mr', 'Mrs', 'Ms',
    'U.S', 'U.K', 'E.U', 'fig', 'tab', 'sec'
]


def preserve_original(doc_path):
    """Hash and archive the original. Nothing else runs until this completes."""
    with open(doc_path, 'rb') as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f'{timestamp}_{os.path.basename(doc_path)}'
    dest = os.path.join('documents/originals', archive_name)
    shutil.copy2(doc_path, dest)
    print(f'Original archived. SHA-256: {file_hash[:16]}...')
    return file_hash


def create_working_copy(doc_path):
    """All operations run on this copy. The original is never touched again."""
    dest = os.path.join('documents/processing', os.path.basename(doc_path))
    shutil.copy2(doc_path, dest)
    return dest


def preflight(text):
    """
    Structural validation before any processing begins.
    Returns a list of warning strings. Processing pauses if any exist.
    """
    warnings = []
    lines = text.split('\n')
    for i, line in enumerate(lines, 1):
        # Trigger inside quotation marks
        if re.search(r'"[^"]+\.\.[^"]*"', line):
            warnings.append(f'Line {i}: trigger appears inside quotation marks.')
        # Trigger in a Markdown heading
        if line.strip().startswith('#') and '..' in line:
            warnings.append(f'Line {i}: trigger appears inside a heading.')
        # Trigger adjacent to an existing footnote marker
        if re.search(r'\[\^\d+\]\.\.', line):
            warnings.append(f'Line {i}: trigger adjacent to an existing footnote.')
        # Ellipsis near trigger (potential false positive)
        if re.search(r'\.{3}', line) and '..' in line:
            warnings.append(f'Line {i}: ellipsis detected near trigger — verify.')
        # Abbreviation immediately before trigger
        for abbr in ABBREVIATIONS:
            if re.search(re.escape(abbr) + r'\.\.', line, re.I):
                warnings.append(f'Line {i}: trigger may follow abbreviation "{abbr}".')
    return warnings


def detect_triggers(text):
    """
    Detects (..) trigger sentences.
    Returns list of dicts: sentence, full_match, start position, end position.
    Excludes very short matches and known abbreviation false positives.
    """
    pattern = r'(?<![\r\n])([A-Z][^.!?]{14,})\.(?=\.(?!\.))'
    triggers = []
    for m in re.finditer(pattern, text):
        sentence = m.group(1).strip()
        # Exclude if sentence ends with a known abbreviation
        last_word = sentence.rstrip().split()[-1].rstrip('.')
        if last_word.lower() in [a.split('.')[-1].lower() for a in ABBREVIATIONS]:
            continue
        triggers.append({
            'sentence': sentence,
            'full_match': m.group(0) + '.',
            'start': m.start(),
            'end': m.end() + 1
        })
    return triggers


def get_context_window(text, trigger_start, trigger_sentence):
    """
    Returns the two sentences before + trigger + one sentence after.
    The trigger is repeated once to increase its embedding weight.
    This richer context improves semantic matching for paraphrase.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    target = trigger_sentence.lower()[:40]
    idx = next((i for i, s in enumerate(sentences)
               if s.lower()[:40] == target), 0)
    window = sentences[max(0, idx-2): idx+2]
    return ' '.join(window) + ' ' + trigger_sentence  # weight trigger
