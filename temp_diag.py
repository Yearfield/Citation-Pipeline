"""
Search the index DB for text related to the trigger sentence.
"""
import os, sys, json, pickle
sys.path.insert(0, os.getcwd())

from sqlalchemy import select
from pipeline.db import init as db_init, get_connection, paragraphs

with open('config/settings.json') as f:
    settings = json.load(f)

db_init(settings['db_path'])

# The trigger sentence from the log
trigger = "The MTBC comprises several human-adapted lineages known as _M. tuberculosis sensu stricto"

# Search for key terms in the DB
search_terms = ['MTBC', 'lineage', 'tuberculosis', 'sensu stricto', 'human-adapted', 'africanum']

with get_connection() as conn:
    for term in search_terms:
        rows = conn.execute(
            select(paragraphs.c.id, paragraphs.c.text)
            .where(paragraphs.c.text.like(f'%{term}%'))
        ).fetchall()
        print(f'\nSearch for "{term}": {len(rows)} hits')
        for r in rows[:3]:
            print(f'  [id={r[0]}] {r[1][:150]}...')

    # Also do a broader search
    print('\n\n--- All paragraphs containing "MTBC" or "lineage" ---')
    rows = conn.execute(
        select(paragraphs.c.id, paragraphs.c.text)
        .where(
            paragraphs.c.text.like('%MTBC%') | 
            paragraphs.c.text.like('%lineage%')
        )
    ).fetchall()
    print(f'Total: {len(rows)}')
    for r in rows:
        print(f'\n  [id={r[0]}]')
        print(f'  {r[1][:250]}')
