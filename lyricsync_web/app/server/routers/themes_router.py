from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import themes as theme_store

router = APIRouter()


class ThemePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    font: str | None = Field(default=None, max_length=128)
    font_file_name: str | None = Field(default=None, max_length=256)
    font_size: int = Field(default=20, ge=6, le=160)
    outline: int = Field(default=2, ge=0, le=20)
    font_color: str = Field(default="#FFFFFF", pattern=r"^#?[0-9a-fA-F]{6}$")
    outline_color: str = Field(default="#000000", pattern=r"^#?[0-9a-fA-F]{6}$")
    thanks_color: str = Field(default="#FFFFFF", pattern=r"^#?[0-9a-fA-F]{6}$")
    thanks_border_color: str = Field(default="#000000", pattern=r"^#?[0-9a-fA-F]{6}$")


@router.get("")
def list_themes():
    return {"themes": theme_store.load_themes()}


@router.post("")
def save_theme(payload: ThemePayload):
    theme = theme_store.upsert_theme(payload.dict())
    return {"ok": True, "theme": theme}


@router.delete("/{slug}")
def remove_theme(slug: str):
    try:
        theme_store.delete_theme(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Theme not found.")
    return {"ok": True}
