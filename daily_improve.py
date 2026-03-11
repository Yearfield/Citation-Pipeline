#!/usr/bin/env python3
"""
Daily Improvement Loop — Citation Pipeline
==========================================
A Karpathy-style tight feedback loop: read code, spot issues, apply fixes, commit.

Usage:
    python daily_improve.py              # run and commit improvements
    python daily_improve.py --dry-run    # show proposed changes only

Schedule via cron:
    0 3 * * * cd /home/user/Citation-Pipeline && python daily_improve.py

Or via GitHub Actions — see .github/workflows/daily_improve.yml
"""

import os
import sys
import json
import textwrap
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import anthropic

DRY_RUN = "--dry-run" in sys.argv

# Files to review in each daily pass (order matters — start with most critical)
FILES = [
    "pipeline/auditor.py",
    "pipeline/exporter.py",
    "pipeline/formatter.py",
    "pipeline/hardcopy.py",
    "pipeline/incremental.py",
    "pipeline/indexer.py",
    "pipeline/processor.py",
    "pipeline/reranker.py",
    "pipeline/retrieval.py",
    "pipeline/style_parser.py",
    "pipeline/tab_importer.py",
    "pipeline/validator.py",
    "review/app.py",
    "run.py",
]

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a careful, conservative Python code reviewer doing a daily improvement
    pass on an academic citation pipeline. Your mandate is SMALL, SAFE, MECHANICAL
    fixes only — the kind a senior engineer would apply in a 30-minute tidy-up.

    WHAT TO FIX:
    - Obvious bugs: wrong math, wrong index, off-by-one errors
    - Replace hand-rolled implementations with stdlib equivalents
      (e.g. pow(2.718, x) → math.exp(x), manual temp-file pattern → os.replace)
    - Race conditions in file I/O (non-atomic writes)
    - Mutable default arguments or mutable function-attribute state (anti-patterns)
    - Missing imports for symbols already used in the file
    - Dead imports (imported but never referenced)
    - Misleading or wrong comments/docstrings
    - Bare `except:` swallowing all exceptions silently

    DO NOT:
    - Redesign architecture or change function signatures
    - Add new features or new dependencies
    - Refactor for style alone (naming, formatting)
    - Add logging, type annotations, or docstrings to untouched code
    - Make changes that require new packages

    RESPOND with a JSON array. Each element is one change:
    {
      "description": "one-line human-readable explanation",
      "old": "exact verbatim string to find in the file (must be unique)",
      "new": "exact replacement string"
    }

    Be conservative. If you are not highly confident a change is correct AND an
    improvement, omit it. Return [] if the file needs no changes.
    Do NOT wrap your response in markdown fences.
""")


def review_file(client: anthropic.Anthropic, filepath: str, content: str) -> list[dict]:
    """Ask Claude to identify concrete improvements. Returns list of {description, old, new}."""
    prompt = f"File: {filepath}\n\n```python\n{content}\n```\n\nList improvements as a JSON array:"

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    # Extract text content (adaptive thinking may include a thinking block first)
    text = ""
    for block in response.content:
        if block.type == "text":
            text = block.text.strip()
            break

    # Strip any accidental markdown fences Claude might add despite instructions
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        changes = json.loads(text)
        return [c for c in changes if isinstance(c, dict) and "old" in c and "new" in c]
    except json.JSONDecodeError:
        # If response is not valid JSON, skip this file
        print(f"  [WARN] Could not parse response for {filepath}")
        return []


def apply_changes(content: str, changes: list[dict]) -> tuple[str, list[str]]:
    """Apply find-replace changes. Returns (updated_content, list_of_applied_descriptions)."""
    applied = []
    for change in changes:
        old = change.get("old", "")
        new = change.get("new", "")
        desc = change.get("description", "(no description)")
        if not old:
            continue
        if old not in content:
            print(f"    SKIP (string not found): {desc}")
            continue
        if content.count(old) > 1:
            print(f"    SKIP (ambiguous match, found >1): {desc}")
            continue
        content = content.replace(old, new, 1)
        applied.append(desc)
    return content, applied


def git_commit(changed_files: list[str], descriptions: list[tuple[str, str]]) -> None:
    """Stage changed files and create a dated commit."""
    subprocess.run(["git", "add"] + changed_files, check=True)

    lines = [f"Daily improvement pass ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"]
    lines.append("")
    for filepath, desc in descriptions:
        lines.append(f"- {filepath}: {desc}")

    commit_msg = "\n".join(lines)
    subprocess.run(["git", "commit", "-m", commit_msg], check=True)


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(f"=== Citation Pipeline Daily Improvement Loop ===")
    print(f"Date : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Mode : {'DRY RUN (no files written)' if DRY_RUN else 'LIVE'}")
    print(f"Model: claude-opus-4-6 (adaptive thinking)")
    print()

    all_descriptions: list[tuple[str, str]] = []  # (filepath, description)
    changed_files: list[str] = []

    for filepath in FILES:
        path = Path(filepath)
        if not path.exists():
            print(f"SKIP {filepath} (file not found)")
            continue

        content = path.read_text(encoding="utf-8")
        print(f"Reviewing {filepath} ...", end=" ", flush=True)

        changes = review_file(client, filepath, content)

        if not changes:
            print("clean.")
            continue

        new_content, applied = apply_changes(content, changes)

        if not applied:
            print(f"0 changes applied (all skipped).")
            continue

        print(f"{len(applied)} improvement(s):")
        for desc in applied:
            print(f"    + {desc}")

        if not DRY_RUN:
            path.write_text(new_content, encoding="utf-8")
            changed_files.append(filepath)
            all_descriptions.extend((filepath, d) for d in applied)

    print()
    if DRY_RUN:
        print("Dry run complete — no files modified.")
        return

    if not changed_files:
        print("No improvements needed today. Repository is clean.")
        return

    print(f"Applied {len(all_descriptions)} improvement(s) across {len(changed_files)} file(s).")
    print("Creating git commit...")
    try:
        git_commit(changed_files, all_descriptions)
        print("Committed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Git commit failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
