# server/core/prompts.py
from __future__ import annotations
from typing import Dict

"""
Tiny prompt registry.
We can add versions (v1, v2) to avoid silent prompt drift.
"""

PROMPTS: Dict[str, Dict[str, str]] = {
    # For polishing pasted lyrics into tighter, punchier lines (non-destructive style preservation optional).
    "lyrics_polish:v1": {
        "system": (
            "You are a concise lyric editor for metal/symphonic/power styles. "
            "Tighten rhythm, imagery, and punch without overwriting the author's voice. "
            "Keep line count and section labels unless asked otherwise."
        ),
        "user_template": (
            "Polish the lyrics below. Keep section headers.\n\n"
            "LYRICS:\n{lyrics}\n"
        ),
    },
    # Turn lyrics into an image prompt for cover art (hook for future image/video tools).
    "lyrics_to_cover_prompt:v1": {
        "system": (
            "You generate cinematic, single-shot cover prompts for image models. "
            "Write one paragraph; include subject, setting, mood, palette, composition, and key props. "
            "Avoid camera jargon unless crucial; avoid artist names."
        ),
        "user_template": (
            "Create a cover prompt based on these lyrics, suitable for a square album cover. "
            "Return ONLY the prompt text.\n\nLYRICS:\n{lyrics}\n"
        ),
    },
    # Summarize tone/themes for metadata.
    "lyrics_metadata:v1": {
        "system": "You analyze lyrics and extract concise metadata.",
        "user_template": (
            "Return a compact JSON with keys: mood, genres[], themes[], tempo_hint ('slow'|'mid'|'fast'). "
            "Stay under 250 characters total for mood.\n\nLYRICS:\n{lyrics}\n"
        ),
    },
}
