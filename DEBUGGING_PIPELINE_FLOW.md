# Debugging Pipeline Flow — Error Messages at Each Step

## Overview
The citation pipeline now shows detailed diagnostic messages at each step of processing. These messages help diagnose why PDFs aren't being found after sentence detection.

## Pipeline Steps & Messages

### STEP 1: SENTENCE DETECTED
```
┌─ STEP 1: SENTENCE DETECTED
│ [1/5] Sentence: "This research shows that climate..."
│ Position: chars 1234-5678
```
**What it means:**
- Shows which trigger sentence is being processed
- Displays the character position in the document
- If this doesn't appear, the trigger detection failed

**Troubleshooting:**
- Verify the `..` trigger is placed correctly after the sentence
- Check that the sentence is at least 15 characters long
- Ensure the trigger isn't inside quotes or abbreviations

---

### STEP 2: CONTEXT EXTRACTED
```
├─ STEP 2: CONTEXT EXTRACTED
│ Context (first 80 chars): "The previous sentence. This research shows..."
```
**What it means:**
- Shows the context window used for semantic search (previous 2 sentences + trigger + next sentence)
- The context is weighted with the trigger sentence repeated for emphasis

**Troubleshooting:**
- If context looks wrong, check the sentence boundaries
- Look for unusual characters or formatting

---

### STEP 3: RETRIEVAL — Hybrid Search
```
├─ STEP 3: RETRIEVAL — Hybrid Search
│ Settings: semantic_weight=0.60, bm25_weight=0.40, k=20
│ ✓ Hybrid results: 12 candidates found
│   [1] para_idx=42 | final=0.823 | sem=0.91 | bm25=0.65
│   [2] para_idx=155 | final=0.756 | sem=0.80 | bm25=0.58
│   [3] para_idx=89 | final=0.712 | sem=0.75 | bm25=0.55
│   ... and 9 more
```
**What it means:**
- **para_idx**: The paragraph ID in the FAISS index (database ID = para_idx + 1)
- **final**: The combined hybrid score (weighted semantic + BM25)
- **sem**: Semantic embedding similarity score (0-1)
- **bm25**: Lexical/keyword match score (0-1)

**✗ ERROR: NO CANDIDATES from hybrid search**
- The search found nothing
- Likely causes:
  - Index is empty (no PDFs processed)
  - Index contains PDFs but they don't match the sentence keywords

**Troubleshooting:**
- Check that indices were built: `Build → Build Index` in the GUI
- Verify that the sentence keywords appear in your PDFs
- Try searching for very specific keywords from your documents

---

### STEP 4: RERANKING
```
├─ STEP 4: RERANKING
│ Cross-encoder: top 10 candidates pre-rerank, keeping top 5
│ ✓ Reranked: 5 candidates after cross-encoder
│   [1] conf=0.612 | ce=0.925 | sem=0.91 | bm25=0.65
│   [2] conf=0.548 | ce=0.856 | sem=0.80 | bm25=0.58
│   [3] conf=0.503 | ce=0.734 | sem=0.75 | bm25=0.55
```
**What it means:**
- **conf**: Final confidence score (weighted blend of CE, semantic, and BM25)
- **ce**: Cross-encoder semantic similarity (raw logit before normalization)
- **sem, bm25**: Same as above
- Takes top candidates from retrieval and applies fine-tuned semantic reranking

**✗ NO CANDIDATES after reranking**
- Retrieval found results but none passed database lookup
- This indicates a database/index mismatch

**Troubleshooting:**
- Check that the database index is not corrupted
- Try rebuilding the index: `Build → Build Index → Force Full Rebuild`

---

### STEP 5: VALIDATION
```
├─ STEP 5: VALIDATION
│ Source: "Smith et al., 2019" (ID=smith2019)
│ PDF path: /path/to/sources/pdfs/smith2019.pdf
│ Validation: PASS
```
**What it means:**
- Shows which source is being validated
- Checks if the PDF path exists and is accessible
- Validates keyword overlap with the source text

**✓ PASS**: Keywords found in source document
**✗ FAIL**: Keywords not found in source document (may still be cited but with lower confidence)

**Troubleshooting:**
- If PDF path is missing or incorrect, update `sources/library.bib`
- Ensure PDF file exists at the specified location
- Check file permissions

---

### STEP 6: BUILD CITATION
```
├─ STEP 6: BUILD CITATION
│ Citation preview: Smith, John, et al. "Title of Article." Journal, 2019.
```
**What it means:**
- Shows the formatted citation that will appear in the output document
- Built using your selected citation style

**⚠ Unknown fields**: [UNKNOWN — VERIFY]
- Some fields from the bibliography are missing
- You'll need to manually fill these in during review

**Troubleshooting:**
- Update your .bib file with complete metadata for all sources
- Use your citation style manager to auto-populate missing fields

---

### STEP 7: HARDCOPY LIBRARY SEARCH
```
├─ STEP 7: HARDCOPY LIBRARY SEARCH
│ ✓ Hard-copy results: 3 candidates
│   [1] Smith, John (conf=0.45)
│   [2] Johnson, Mary (conf=0.38)
```
**What it means:**
- Searches the hard-copy library (non-Zotero sources you've manually added)
- Shows candidates from non-digitized sources

**Troubleshooting:**
- If you want to use hard-copy sources, add them via the GUI: `Library → Add Source`

---

### STEP 8: MERGE & FINALIZE
```
├─ STEP 8: MERGE & FINALIZE
│ Total candidates (ranked + hardcopy): 8
│ Keeping top 5 for review
└─ COMPLETE
```
**What it means:**
- Combines all candidates (from FAISS/BM25 reranking + hard-copy library)
- Sorts by confidence
- Returns top 5 for human review in the review panel

---

### FINAL SUMMARY
```
======================================================================
PIPELINE COMPLETE — SUMMARY
======================================================================
✓ Sentences processed: 5
✓ Total candidates found: 18
✓ Average candidates per sentence: 3.6
✓ Session saved: temp/session_a1b2c3d4.json
======================================================================
```

**⚠ Sentences with NO candidates: 2**
- These will need manual citation in the review panel
- You can add them from the hard-copy library or skip them

---

## Common Issues & Solutions

### 1. "Index is empty! No PDFs have been processed."
**Problem:** The citation index hasn't been built yet
**Solution:**
1. Click `Build → Build Index`
2. Select your `.bib` file (sources/library.bib)
3. Select your PDFs folder (sources/pdfs)
4. Click "Build & Index PDFs"
5. Wait for completion

### 2. "NO CANDIDATES from hybrid search"
**Problem:** Search returned nothing for this sentence
**Solution:**
- Your trigger sentence doesn't match any text in your indexed PDFs
- Try:
  - Making the sentence more specific with unique keywords
  - Adding more PDFs to your library
  - Checking that the PDFs contain the relevant text
  - Rebuilding the index with `Force Full Rebuild`

### 3. "Failed to fetch X candidates from database"
**Problem:** Para indices from FAISS don't match database IDs
**Solution:**
- Index and database are out of sync
- Click `Build → Build Index → Force Full Rebuild`
- This recreates both the FAISS index and database

### 4. "Sentences with NO candidates: 3"
**Problem:** Some sentences had no good matches
**Solution:**
1. In the review panel, you can:
   - Search the hard-copy library for these sentences
   - Manually add sources via "Add Citation"
   - Skip the sentence and add it manually later
2. Or:
   - Add more relevant PDFs to your library
   - Rebuild the index
   - Reprocess the document

---

## Logging Files

All detailed logs are saved to: `logs/pipeline.log`

View them with any text editor. This file contains:
- Every step with timestamps
- Detailed debug information
- Error traces if something goes wrong

**During GUI operation:**
- All INFO and WARNING messages appear in the "Activity Log" panel
- DEBUG messages go only to pipeline.log

---

## Testing Your Setup

To test the pipeline with sample data:

```bash
# 1. Build the index (GUI: Build → Build Index)

# 2. Process a test document
python run.py process documents/test.md

# 3. Review the terminal output for step-by-step messages

# 4. Check the review panel at http://localhost:8000/review/{session_id}
```

---

## Next Steps

If you still can't find PDFs:
1. Check `logs/pipeline.log` for detailed errors
2. Verify your `.bib` file references correct PDF paths
3. Ensure all PDF files exist and are readable
4. Try with a simpler sentence using exact phrases from your PDFs
5. Rebuild the index with `Force Full Rebuild`
