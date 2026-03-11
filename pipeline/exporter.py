# pipeline/exporter.py
# Change 3: Complete rebuild of footnote implementation using correct python-docx
# XML API. The original _insert_footnote OxmlElement approach has been removed
# entirely and replaced with a two-pass approach that creates a proper OOXML
# footnotes part before any body paragraphs are written.

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from lxml import etree
import re, json, sqlite3, os


# ── Footnote style helpers ─────────────────────────────────────────────────────

def _set_footnote_style(doc):
    """Sets FootnoteText and FootnoteReference styles to Times New Roman 10pt."""
    styles = doc.styles
    for style_name, style_type in [("Footnote Text", 1), ("Footnote Reference", 2)]:
        try:
            fn_style = styles[style_name]
        except KeyError:
            fn_style = styles.add_style(style_name, style_type)
        fn_style.font.name = "Times New Roman"
        fn_style.font.size = Pt(10)


# ── Footnotes part initialisation ─────────────────────────────────────────────

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FOOTNOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
FOOTNOTES_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.footnotes+xml"
)


def _init_footnotes_part(doc):
    """
    Creates the footnotes.xml part and relates it to the document part.
    Adds the mandatory separator (id=-1) and continuationSeparator (id=0)
    footnotes required by the OOXML spec. Word rejects documents missing these.
    """
    footnotes_xml = etree.fromstring(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes'
        ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:footnote w:type="separator" w:id="-1">'
        '<w:p><w:r><w:separator/></w:r></w:p>'
        '</w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0">'
        '<w:p><w:r><w:continuationSeparator/></w:r></w:p>'
        '</w:footnote>'
        '</w:footnotes>'
    )

    from docx.opc.part import Part
    from docx.opc.packuri import PackURI

    blob = etree.tostring(footnotes_xml, xml_declaration=True,
                          encoding="UTF-8", standalone=True)
    footnotes_part = Part(
        PackURI("/word/footnotes.xml"),
        FOOTNOTES_CONTENT_TYPE,
        blob,
        doc.part.package
    )
    doc.part.relate_to(footnotes_part, FOOTNOTES_REL)

    # Store references so _add_footnote_definition can update the XML blob
    doc._footnotes_part = footnotes_part
    doc._footnotes_xml  = footnotes_xml


def _add_footnote_definition(doc, fn_id, fn_text):
    """
    Appends one footnote definition to the footnotes XML and serialises
    the updated XML back to the footnotes part blob.
    fn_id must be a positive integer starting at 1.
    fn_text is the formatted citation string.
    """
    fn_elem = etree.SubElement(doc._footnotes_xml,
                                f"{{{W_NS}}}footnote")
    fn_elem.set(f"{{{W_NS}}}id", str(fn_id))

    p_elem = etree.SubElement(fn_elem, f"{{{W_NS}}}p")
    pPr    = etree.SubElement(p_elem,  f"{{{W_NS}}}pPr")
    pStyle = etree.SubElement(pPr,     f"{{{W_NS}}}pStyle")
    pStyle.set(f"{{{W_NS}}}val", "FootnoteText")

    # Footnote auto-number reference (the superscript number inside the fn panel)
    r_num   = etree.SubElement(p_elem,  f"{{{W_NS}}}r")
    rPr_num = etree.SubElement(r_num,   f"{{{W_NS}}}rPr")
    rStyle  = etree.SubElement(rPr_num, f"{{{W_NS}}}rStyle")
    rStyle.set(f"{{{W_NS}}}val", "FootnoteReference")
    etree.SubElement(r_num, f"{{{W_NS}}}footnoteRef")

    # Non-breaking space after the number
    r_sp = etree.SubElement(p_elem, f"{{{W_NS}}}r")
    t_sp = etree.SubElement(r_sp,   f"{{{W_NS}}}t")
    t_sp.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_sp.text = "\u00a0"  # non-breaking space

    # Citation text
    r_text = etree.SubElement(p_elem,  f"{{{W_NS}}}r")
    t_text = etree.SubElement(r_text,  f"{{{W_NS}}}t")
    t_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_text.text = fn_text

    # Serialise updated XML back to the part blob
    doc._footnotes_part._blob = etree.tostring(
        doc._footnotes_xml,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True
    )


def _insert_footnote_reference(paragraph, fn_id):
    """
    Inserts a footnote reference marker (superscript number) into the
    paragraph at the current position. This links to the footnote definition
    in footnotes.xml.
    """
    run = paragraph.add_run()

    # Style the run as FootnoteReference (superscript)
    rPr = run._r.get_or_add_rPr()
    rStyle_elem = OxmlElement("w:rStyle")
    rStyle_elem.set(qn("w:val"), "FootnoteReference")
    rPr.insert(0, rStyle_elem)

    # w:footnoteReference pointing to the correct definition id
    fn_ref_elem = OxmlElement("w:footnoteReference")
    fn_ref_elem.set(qn("w:id"), str(fn_id))
    run._r.append(fn_ref_elem)

    return run


# ── Main export function ───────────────────────────────────────────────────────

def build_docx(doc_path, session_id, style_profile):
    """
    Reads the working copy, inserts confirmed citations as Word footnotes,
    appends the chapter bibliography, and saves to documents/confirmed/.

    Two-pass approach (Change 3):
    Pass 1 — scan document, collect all (sentence_key, citation) with fn_ids.
    Pass 2 — initialise Document and footnotes part with all definitions upfront.
    Pass 3 — write body paragraphs with _insert_footnote_reference at triggers.
    """
    with open(f"temp/session_{session_id}.json") as f:
        session = json.load(f)

    confirmed = session.get("confirmed", {})
    cit_map = {
        session["sentences"][sid]["sentence"]: data["citation"]
        for sid, data in confirmed.items()
    }

    with open(doc_path, "r", encoding="utf-8") as f:
        content = f.read()

    citation_mode = style_profile.get("citation_mode", "footnote")

    # ── Pass 1: Collect all footnote insertions ───────────────────────────────
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    fn_counter = 0
    # insertion_map: sentence_key fragment → fn_id
    insertion_map = {}

    for block in blocks:
        if block.startswith(("#", "##", "###")):
            continue
        parts = re.split(r"(\.\.)(?!\.)", block)
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and parts[i + 1] == "..":
                seg = parts[i].strip()
                citation = next(
                    (v for k, v in cit_map.items() if k.strip() in seg),
                    None
                )
                if citation and citation_mode == "footnote":
                    fn_counter += 1
                    insertion_map[seg] = (fn_counter, citation)
                i += 2
            else:
                i += 1

    # ── Pass 2: Create Document and footnotes part ────────────────────────────
    doc = Document()
    doc.styles["Normal"].font.name = "Times New Roman"
    doc.styles["Normal"].font.size = Pt(12)

    _set_footnote_style(doc)

    if fn_counter > 0:
        _init_footnotes_part(doc)
        for seg, (fn_id, fn_text) in sorted(insertion_map.items(),
                                              key=lambda x: x[1][0]):
            _add_footnote_definition(doc, fn_id, fn_text)

    # ── Pass 3: Write body paragraphs ────────────────────────────────────────
    for block in blocks:
        if not block:
            continue
        if block.startswith("### "):
            doc.add_heading(block[4:], level=3)
        elif block.startswith("## "):
            doc.add_heading(block[3:], level=2)
        elif block.startswith("# "):
            doc.add_heading(block[2:], level=1)
        else:
            _write_paragraph_with_footnotes(
                doc, block, insertion_map, citation_mode, cit_map
            )

    _append_bibliography(doc, session_id)

    out_name = os.path.splitext(os.path.basename(doc_path))[0] + "_cited.docx"
    out_path = os.path.join("documents/confirmed", out_name)
    doc.save(out_path)
    print(f"Exported: {out_path}")
    return out_path


def _write_paragraph_with_footnotes(doc, block, insertion_map,
                                     mode, cit_map):
    """
    Writes one body paragraph, inserting footnote reference markers
    at the correct positions after trigger sentences.
    """
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    parts = re.split(r"(\.\.)(?!\.)", block)
    i = 0
    while i < len(parts):
        seg = parts[i]
        if i + 1 < len(parts) and parts[i + 1] == "..":
            # Write sentence text ending with a single period
            para.add_run(seg.rstrip() + ".")
            if mode == "footnote":
                # Find fn_id by matching sentence key
                fn_id = next(
                    (v[0] for k, v in insertion_map.items()
                     if k.strip() in seg.strip()),
                    None
                )
                if fn_id:
                    _insert_footnote_reference(para, fn_id)
            elif mode == "author-date":
                # APA: insert the citation string inline
                citation = next(
                    (v for k, v in cit_map.items() if k.strip() in seg.strip()),
                    None
                )
                if citation:
                    para.add_run(" " + citation)
            i += 2
        else:
            para.add_run(seg)
            i += 1


# ── Bibliography ──────────────────────────────────────────────────────────────

def _append_bibliography(doc, session_id):
    """Generates and appends a chapter-scoped bibliography."""
    audit_path = f"audit_logs/{session_id}.json"
    if not os.path.exists(audit_path):
        return

    with open(audit_path) as f:
        audit = json.load(f)

    seen = {}
    session_path = f"temp/session_{session_id}.json"
    session = {}
    if os.path.exists(session_path):
        with open(session_path) as f:
            session = json.load(f)

    for entry in audit:
        if entry["action"] == "approved" and entry.get("source_id"):
            sid = entry["source_id"]
            if sid not in seen:
                seen[sid] = entry

    # Collect hard-copy sources from session confirmed map
    hc_entries = []
    for sid_str, data in session.get("confirmed", {}).items():
        if data.get("source_type") == "hardcopy":
            from pipeline.hardcopy import get_by_id, to_row
            source = get_by_id(data["source_id"])
            if source and source["source_id"] not in seen:
                seen[source["source_id"]] = True
                row = to_row(source)
                hc_entries.append(_format_bib_entry(row))

    if not seen and not hc_entries:
        return

    doc.add_page_break()
    doc.add_heading("Bibliography", level=1)

    for bib in hc_entries:
        p = doc.add_paragraph(bib)
        p.paragraph_format.left_indent = Inches(0.5)
        p.paragraph_format.first_line_indent = Inches(-0.5)

    conn = sqlite3.connect("index/metadata.db")
    for entry in sorted(seen.values(), key=lambda x: x.get("source_title", "") or ""):
        if not isinstance(entry, dict):
            continue
        row = conn.execute(
            "SELECT * FROM paragraphs WHERE source_id = ? LIMIT 1",
            (entry["source_id"],)
        ).fetchone()
        if row:
            bib = _format_bib_entry(row)
            p = doc.add_paragraph(bib)
            p.paragraph_format.left_indent = Inches(0.5)
            p.paragraph_format.first_line_indent = Inches(-0.5)
    conn.close()


def _format_bib_entry(row):
    author = row[3] or "[Author unknown]"
    title  = row[4] or "[Title unknown]"
    year   = row[5] or "n.d."
    pub    = row[9] or ""
    place  = row[10] or ""
    return (
        f"{author}, {title} ({place}: {pub}, {year})."
        if pub else f"{author}, {title} ({year})."
    )
