============================================================
  CITATION PIPELINE — SETUP GUIDE
============================================================

What this tool does
-------------------
This pipeline helps you add citations to academic documents
without writing them manually. You write normally and add (..)
after any sentence that needs a citation. The pipeline finds
matching sources from your Zotero library, shows you each
proposal for approval, and exports a Word document with
properly formatted footnotes.

PREREQUISITES — Install these first
------------------------------------
You need to install these separately before running the pipeline:

  1. PYTHON 3.11
     https://www.python.org/downloads/
     IMPORTANT: During installation, tick "Add Python to PATH"

  2. ZOTERO (reference manager)
     https://www.zotero.org/download/
     - Create a free Zotero account
     - Install the "Better BibTeX" plugin from:
       https://retorque.re/zotero-better-bibtex/installation/

  3. TESSERACT OCR (for reading page numbers from PDFs)
     https://github.com/UB-Mannheim/tesseract/wiki
     Download the Windows installer and run it.

FIRST-TIME SETUP
----------------
  1. Extract this folder anywhere on your computer
     (e.g. C:\citation_pipeline\ or your Desktop)

  2. Double-click: install.bat
     Wait for it to finish (10-20 minutes, downloads ~600 MB)

  3. Configure Zotero:
     a. Open Zotero
     b. Go to File > Export Library
     c. Format: "Better BibTeX", tick "Keep Updated"
     d. Save to: sources\library.bib  (inside this folder)
     e. Copy your source PDFs to: sources\pdfs\
     f. Enable the Zotero local API:
        Zotero Preferences > Advanced > Miscellaneous
        > tick "Enable Zotero Local API"

EVERY TIME YOU USE IT
---------------------
  1. Open Zotero (it must be running)

  2. Double-click: Launch Citation Pipeline.bat

  3. In the launcher window:
     - Click "Browse" to select your .md document
     - Choose your citation style from the dropdown
     - Click "Build Source Index" if you added new sources to Zotero
     - Click "Process Document" — your browser will open with
       the review panel after a moment
     - Work through citations in the browser (Y to approve, N to deny)
     - When done, click "Finalise" in the browser
     - Back in the launcher, click "Export Document"
     - Your finished .docx is in: documents\confirmed\

WRITING YOUR DOCUMENT
---------------------
Write in any plain text editor or Obsidian (.md files).
To mark a sentence for citation, type (..) immediately after
the last word (before the newline):

  The prevalence of inequality correlates with lower civic
  participation across democratic societies.. The mechanisms
  through which this operates remain debated.

The (..) is the only trigger. Sentences without it are
never read or processed.

CITATION STYLES
---------------
The default style is Chicago Notes-Bibliography.
To change it, edit config\style_profile.json to match your
required style. An APA 7th edition template is also included
as config\apa_profile.json — select it from the dropdown
in the launcher.

FOLDER STRUCTURE
----------------
  sources\library.bib     <- Zotero live export (auto-updated)
  sources\pdfs\           <- Your source PDFs go here
  sources\styles\         <- .csl style files (optional)
  config\                 <- Settings and style profiles
  documents\confirmed\    <- Your finished .docx files appear here
  audit_logs\             <- Complete decision log for every session

TROUBLESHOOTING
---------------
  "No candidates found"
    -> Run "Build Source Index" after adding new PDFs/sources

  Review panel won't open
    -> Make sure Zotero is running
    -> Try clicking "Open Review Panel" button in the launcher

  Page numbers show as "unverified"
    -> Tesseract may not be installed or not found
    -> You can always edit page numbers manually in the review panel

  install.bat fails
    -> Check internet connection
    -> Make sure Python is installed and "Add Python to PATH" was ticked

============================================================
  Six Rules This Pipeline Follows
============================================================
  1. Only (..) triggers are ever processed
  2. Original files are never modified — only copies are used
  3. No AI generates text — all results are deterministic
  4. No citation is inserted without your explicit approval
  5. Every decision is logged with timestamp and scores
  6. Only trigger markers are removed; document body is untouched
============================================================
