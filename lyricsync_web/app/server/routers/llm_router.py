# server/routers/llm_router.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any
from ..core.llm_client import LLMClient
from ..core.prompts import PROMPTS

router = APIRouter(prefix="/api/llm", tags=["llm"])
_llm = LLMClient()

class GenerateRequest(BaseModel):
    task: Literal[
        "lyrics_polish:v1",
        "lyrics_to_cover_prompt:v1",
        "lyrics_metadata:v1"
    ] = Field(default="lyrics_polish:v1")
    payload: Dict[str, Any]
    model_override: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 800
    model_config = {"protected_namespaces": ()}

class GenerateResponse(BaseModel):
    ok: bool
    text: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = None

@router.post("/generate", response_model=GenerateResponse)
def api_llm_generate(req: GenerateRequest):
    if req.task not in PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown task: {req.task}")
    prompt = PROMPTS[req.task]
    system = prompt["system"]
    user_tmpl = prompt["user_template"]

    # Map payload -> template inputs
    if "lyrics" in user_tmpl and "lyrics" not in req.payload:
        raise HTTPException(status_code=400, detail="Missing required field: payload.lyrics")

    user = user_tmpl.format(**req.payload)
    resp = _llm.chat(system=system, user=user, model=req.model_override, temperature=req.temperature, max_tokens=req.max_tokens)
    # If task returns JSON, try to parse; otherwise return text
    data = None
    text_out = resp.text
    if req.task == "lyrics_metadata:v1":
        try:
            data = __import__("json").loads(resp.text)
            text_out = None
        except Exception:
            # leave as raw text if not strict JSON
            pass

    return GenerateResponse(
        ok=True,
        text=text_out,
        data=data,
        provider=resp.provider,
        model=resp.model,
        latency_ms=resp.latency_ms,
    )
