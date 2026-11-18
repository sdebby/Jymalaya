"""
Athlete-written custom coach instructions ("bricks only on Saturday", "swims on
Tuesday"...). Stored as a plain text file; sent to Claude with every plan
generation and in the coach chat context when non-empty. Saving new text makes
the cached plan stale (sync_service checks this file's mtime), so the next sync
regenerates the plan even without new activities.
"""

from __future__ import annotations

from app.config import ROOT

INSTRUCTIONS_FILE = ROOT / "data" / "coach_instructions.txt"


def get_instructions() -> str:
    """The saved instructions, stripped; empty string when none exist."""
    try:
        return INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def save_instructions(text: str) -> None:
    """Persist the instructions; an empty/whitespace text clears them."""
    INSTRUCTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTRUCTIONS_FILE.write_text((text or "").strip(), encoding="utf-8")
