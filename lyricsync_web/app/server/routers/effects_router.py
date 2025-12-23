# server/routers/effects_router.py
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))
from fastapi import APIRouter
from effects import choices as effect_choices

router = APIRouter(prefix="/api/effects", tags=["Effects"])

@router.get("/")
async def list_effects():
    return {"effects": effect_choices()}
