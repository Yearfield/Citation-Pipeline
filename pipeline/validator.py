# pipeline/validator.py
import re, spacy
nlp = spacy.load('en_core_web_sm')


ENTITY_TYPES = {'PERSON', 'ORG', 'GPE', 'EVENT', 'LAW', 'NORP', 'FAC'}


def validate(sentence, para_text):
    """
    Returns a dict of warning strings. Empty dict = no concerns.
    Warnings are advisory only — never block user approval.
    """
    warnings = {}
    s_doc = nlp(sentence)
    p_doc = nlp(para_text)

    # Named entity alignment
    s_ents = {e.text.lower() for e in s_doc.ents if e.label_ in ENTITY_TYPES}
    p_ents = {e.text.lower() for e in p_doc.ents if e.label_ in ENTITY_TYPES}
    missing_ents = s_ents - p_ents
    if missing_ents and len(s_ents) > 0:
        warnings['entities'] = (
            f'Named entities in your sentence not found in source: '
            f'{missing_ents}'
        )

    # Year consistency
    s_years = set(re.findall(r'\b(1[0-9]{3}|20[0-9]{2})\b', sentence))
    p_years = set(re.findall(r'\b(1[0-9]{3}|20[0-9]{2})\b', para_text))
    missing_years = s_years - p_years
    if missing_years:
        warnings['years'] = (
            f'Years in your sentence not present in source: {missing_years}'
        )

    # Keyword anchor check
    s_lemmas = {
        w.lemma_.lower() for w in s_doc
        if not w.is_stop and not w.is_punct and len(w.text) > 3
    }
    p_lemmas = {
        w.lemma_.lower() for w in p_doc
        if not w.is_stop and not w.is_punct and len(w.text) > 3
    }
    overlap = s_lemmas & p_lemmas
    if len(overlap) < 2:
        warnings['keywords'] = (
            'Fewer than 2 shared content words. Verify this source genuinely '
            'supports your claim.'
        )

    return warnings
