# pipeline/style_parser.py
import re, json


# Labels that style guides use to introduce citation examples.
# Extend this list if a particular style guide uses different labels.
ENTRY_TYPE_LABELS = {
    "book":         ["book", "monograph", "authored book", "single author"],
    "article":      ["journal", "article", "journal article", "periodical"],
    "incollection": ["chapter", "book chapter", "contribution", "in a book", "edited volume"],
    "case":         ["case", "judgment", "court decision", "law report"],
    "legislation":  ["act", "statute", "legislation", "regulation"],
    "phdthesis":    ["thesis", "dissertation", "unpublished thesis"],
    "misc":         ["website", "internet", "online", "url", "electronic"],
}


# BibTeX field names and the patterns used to detect them in an example citation.
FIELD_PATTERNS = [
    ("author",  r"[A-Z][a-z]+(?:\s+(?:and|&|et al)\s+[A-Z][a-z]+)*"),
    ("year",    r"\b(19|20)\d{2}\b"),
    ("title",   r"['\u2018\u2019](.*?)['\u2018\u2019]|[A-Z][^.,:]+"),
    ("journal", r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Journal|Review|Law|Gazette)\b"),
    ("volume",  r"\b\d{1,3}\b"),
    ("page",    r"\b\d+(?:[–\-]\d+)?\b$"),
    ("url",     r"https?://\S+"),
]


def extract_text_blocks(raw_text):
    """
    Splits the raw PDF text into logical blocks.
    Returns a list of (label, example_text) tuples where
    label is the detected entry type and example_text is the
    citation example that follows it in the style guide.
    """
    blocks = []
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line_lower = lines[i].lower()
        for entry_type, labels in ENTRY_TYPE_LABELS.items():
            if any(lbl in line_lower for lbl in labels):
                # The example typically follows on the next 1-3 lines
                example_lines = []
                j = i + 1
                while j < min(i + 5, len(lines)) and lines[j]:
                    example_lines.append(lines[j])
                    j += 1
                if example_lines:
                    blocks.append((entry_type, " ".join(example_lines)))
                break
        i += 1
    return blocks


def example_to_template(example_text, entry_type):
    """
    Converts a raw citation example string into a template string
    with {field_name} placeholders.
    This is a best-effort conversion. The user reviews and corrects
    the output before the template is saved.
    """
    template = example_text

    # Replace detected year with placeholder
    template = re.sub(r"\b(19|20)\d{2}\b", "{year}", template, count=1)

    # Replace URL with placeholder
    template = re.sub(r"https?://\S+", "{url}", template)

    # Replace what looks like a page number at the end
    template = re.sub(r"\s(\d+(?:[–\-]\d+)?)\s*$", " {page}", template)

    # Mark the template as needing review
    return {
        "entry_type":   entry_type,
        "raw_example":  example_text,
        "template":     template,
        "needs_review": True
    }


def parse_style_pdf(raw_text, suggested_name=""):
    """
    Main entry point. Takes raw text from a style guide PDF.
    Returns a dict in the styles_library.json format,
    with needs_review=True on each template.
    This dict is shown to the user for correction before saving.
    """
    blocks = extract_text_blocks(raw_text)
    templates = {}
    raw_examples = {}

    for entry_type, example in blocks:
        if entry_type not in templates:  # Take first occurrence only
            result = example_to_template(example, entry_type)
            templates[entry_type]    = result["template"]
            raw_examples[entry_type] = result["raw_example"]

    # Detect ibid usage
    ibid_used = bool(re.search(r"\bibid\b", raw_text, re.IGNORECASE))

    # Detect short form usage
    short_form_used = bool(re.search(
        r"subsequent\s+reference|abbreviated\s+form|short\s+form|op\s+cit",
        raw_text, re.IGNORECASE))

    return {
        "id":               "",  # Set by UI from user-entered name
        "name":             suggested_name,
        "source_pdf":       "",  # Set by caller
        "ibid_used":        ibid_used,
        "short_form_used":  short_form_used,
        "citation_mode":    "footnote",
        "author_separator": " and ",
        "unknown_placeholder": "[UNKNOWN — VERIFY]",
        "templates":        templates,
        "short_form_templates": {},
        "raw_examples":     raw_examples,
        "notes":            ""
    }
