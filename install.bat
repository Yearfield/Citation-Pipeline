@echo off
title Citation Pipeline — First-Time Setup
color 0A
cd /d "%~dp0"

:: /quiet flag skips all pause commands (used when launched from the GUI)
set QUIET=0
if "%~1"=="/quiet" set QUIET=1

echo.
echo ============================================================
echo   Citation Pipeline — One-Time Installation
echo ============================================================
echo.
echo This will set up everything the pipeline needs.
echo Please keep this window open until it says DONE.
echo.

:: ── Check Python ────────────────────────────────────────────────────────────
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo ERROR: Python was not found on your system.
    echo.
    echo Please install Python 3.11 from: https://www.python.org/downloads/
    echo IMPORTANT: During installation, tick the box that says
    echo            "Add Python to PATH" before clicking Install.
    echo.
    echo After installing Python, run this file again.
    echo.
    if %QUIET%==0 pause
    exit /b 1
)
python --version
echo Python found. OK.
echo.

:: ── Check Tesseract (optional) ─────────────────────────────────────────────
echo [2/5] Checking Tesseract OCR...
tesseract --version >nul 2>&1
if not errorlevel 1 goto tess_ok
echo.
echo  WARNING: Tesseract OCR was not found.
echo  Tesseract is required for accurate PDF page number extraction.
echo.
echo  The pipeline will still work without Tesseract, but page numbers
echo  will be extracted less accurately. You can install it later from:
echo  https://github.com/UB-Mannheim/tesseract/wiki
echo.
echo  Continuing installation without Tesseract...
goto tess_done
:tess_ok
tesseract --version 2>&1 | findstr /i "tesseract"
echo Tesseract found. OK.
:tess_done
:: Clear stale errorlevel from tesseract check
cmd /c "exit /b 0"
echo.

:: ── Create virtual environment ───────────────────────────────────────────────
echo [3/5] Creating Python virtual environment...
if exist venv\ (
    echo Virtual environment already exists. Skipping creation.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Could not create virtual environment.
        if %QUIET%==0 pause
        exit /b 1
    )
    echo Virtual environment created. OK.
)
echo.

:: Verify venv python exists before proceeding
if not exist "%~dp0venv\Scripts\python.exe" (
    echo ERROR: Virtual environment is missing python.exe.
    echo Try deleting the venv folder and running this script again.
    if %QUIET%==0 pause
    exit /b 1
)

:: ── Install packages ─────────────────────────────────────────────────────────
echo [4/5] Installing Python packages from requirements.txt...
echo     This downloads ~200 MB of packages. Please wait.
echo.
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: pip upgrade failed — continuing with existing pip.
)

"%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo ERROR: Package installation failed.
    echo Check your internet connection and try again.
    if %QUIET%==0 pause
    exit /b 1
)
echo.
echo Packages installed. OK.
echo.

:: ── Download spaCy model + ML models ────────────────────────────────────────
echo [5/5] Downloading language and ML models...
echo     This downloads ~400 MB. Please wait.
echo.

"%~dp0venv\Scripts\python.exe" -m spacy download en_core_web_sm
if errorlevel 1 (
    echo WARNING: spaCy model download failed. The pipeline will attempt
    echo          to download the model on first use.
    echo.
)

"%~dp0venv\Scripts\python.exe" -c "from sentence_transformers import SentenceTransformer, CrossEncoder; print('Downloading bi-encoder...'); SentenceTransformer('all-MiniLM-L6-v2'); print('Downloading cross-encoder...'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('Models ready.')"
if errorlevel 1 (
    echo WARNING: ML model download may have had issues. The pipeline will
    echo          attempt to download models on first use if they are not cached.
)
echo.

:: ── Done ─────────────────────────────────────────────────────────────────────
color 0A
echo ============================================================
echo   Installation complete!
echo ============================================================
echo.
echo What to do next:
echo.
echo   1. Configure Zotero:
echo      - Install Better BibTeX plugin
echo      - Export your library: File ^> Export Library ^> Better BibTeX
echo        Tick "Keep Updated", save to: %~dp0sources\library.bib
echo      - Copy your PDFs to:  %~dp0sources\pdfs\
echo.
echo   2. Edit your citation style (optional):
echo      - Open config\style_profile.json to match your required style.
echo      - An APA 7 profile is also available: config\apa_profile.json
echo.
echo   3. Start the pipeline:
echo      - Double-click "Launch Citation Pipeline.bat"
echo.
echo ============================================================
echo.
if %QUIET%==0 pause
