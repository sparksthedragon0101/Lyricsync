# server/routers/effects_router.py
import sys, os
# Get the root of the lyricsync project (above lyricsync_web)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import APIRouter
try:
    from effects import choices as effect_choices
except ImportError:
    # Fallback/Debug if it still fails
    import logging
    logging.getLogger("uvicorn.error").error(f"Failed to import 'effects' from {project_root}. sys.path: {sys.path}")
    def effect_choices(): return []

router = APIRouter(prefix="/api/effects", tags=["Effects"])

@router.get("/")
async def list_effects():
    return {"effects": effect_choices()}
