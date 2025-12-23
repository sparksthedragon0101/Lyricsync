from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .paths import APP_DIR

THEMES_PATH = (APP_DIR / "configs" / "themes.json").resolve()
THEMES_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_THEME: Dict[str, Any] = {
    "name": "Default",
    "slug": "default",
    "font": "Arial",
    "font_file_name": None,
    "font_size": 20,
    "outline": 2,
    "font_color": "#FFFFFF",
    "outline_color": "#000000",
    "thanks_color": "#FFFFFF",
    "thanks_border_color": "#000000",
}

_slug_re = re.compile(r"[^a-z0-9]+")


def _normalize_hex(value: str, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()):
        v = value.strip()
        if not v.startswith("#"):
            v = "#" + v
        return v.upper()
    return fallback


def _slugify(name: str) -> str:
    base = _slug_re.sub("-", (name or "").lower()).strip("-")
    return base or "theme"


def _ensure_store() -> None:
    if not THEMES_PATH.exists():
        THEMES_PATH.write_text(json.dumps([DEFAULT_THEME], indent=2), encoding="utf-8")


def _sanitize(theme: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(theme, dict):
        theme = {}
    result = dict(DEFAULT_THEME)
    result.update({
        "name": str(theme.get("name") or DEFAULT_THEME["name"]).strip() or DEFAULT_THEME["name"],
        "font": (theme.get("font") or DEFAULT_THEME["font"]).strip(),
        "font_file_name": (theme.get("font_file_name") or None) or None,
        "font_size": int(theme.get("font_size") or DEFAULT_THEME["font_size"]),
        "outline": int(theme.get("outline") or DEFAULT_THEME["outline"]),
        "font_color": _normalize_hex(theme.get("font_color") or DEFAULT_THEME["font_color"], DEFAULT_THEME["font_color"]),
        "outline_color": _normalize_hex(theme.get("outline_color") or DEFAULT_THEME["outline_color"], DEFAULT_THEME["outline_color"]),
        "thanks_color": _normalize_hex(theme.get("thanks_color") or DEFAULT_THEME["thanks_color"], DEFAULT_THEME["thanks_color"]),
        "thanks_border_color": _normalize_hex(theme.get("thanks_border_color") or DEFAULT_THEME["thanks_border_color"], DEFAULT_THEME["thanks_border_color"]),
    })
    result["font_size"] = max(6, min(160, result["font_size"]))
    result["outline"] = max(0, min(20, result["outline"]))
    return result


def load_themes() -> List[Dict[str, Any]]:
    _ensure_store()
    try:
        data = json.loads(THEMES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Theme config should be a list")
    except Exception:
        data = [DEFAULT_THEME]
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for entry in data:
        sanitized = _sanitize(entry)
        slug = _slugify(entry.get("slug") or sanitized["name"])
        if not slug:
            slug = "theme"
        if slug in seen:
            slug = f"{slug}-{len(seen)}"
        seen.add(slug)
        sanitized["slug"] = slug
        cleaned.append(sanitized)
    if not any(t["slug"] == DEFAULT_THEME["slug"] for t in cleaned):
        cleaned.insert(0, dict(DEFAULT_THEME))
    save_themes(cleaned)
    return cleaned


def save_themes(themes: List[Dict[str, Any]]) -> None:
    payload = [ _sanitize(t) for t in themes ]
    THEMES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def upsert_theme(theme: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = _sanitize(theme)
    sanitized["slug"] = _slugify(theme.get("slug") or sanitized["name"])
    themes = load_themes()
    replaced = False
    for idx, existing in enumerate(themes):
        if existing.get("slug") == sanitized["slug"]:
            themes[idx] = sanitized
            replaced = True
            break
    if not replaced:
        themes.append(sanitized)
    save_themes(themes)
    return sanitized


def delete_theme(slug: str) -> None:
    normalized = _slugify(slug)
    if normalized == DEFAULT_THEME["slug"]:
        raise ValueError("Default theme cannot be removed.")
    themes = load_themes()
    new = [t for t in themes if t.get("slug") != normalized]
    if len(new) == len(themes):
        raise FileNotFoundError("Theme not found.")
    save_themes(new)
