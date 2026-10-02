"""No merge-conflict markers are committed: a QA doc once went up with
"<<<<<<< HEAD" in it after a merge."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = re.compile(r"^(<{7} |>{7} |={7}$)", re.M)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "uploads", "backups"}
TEXT = {".py", ".js", ".html", ".css", ".md", ".txt", ".toml", ".yml", ".yaml", ".json", ".sql", ".ini", ".sh", ".cfg"}


def test_no_conflict_markers_in_the_tree():
    found = []
    for path in ROOT.rglob("*"):
        if path.suffix not in TEXT or not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found += [f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}" for m in MARKER.finditer(text)]
    assert found == [], found
