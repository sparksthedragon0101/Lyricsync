import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict
from image_pipeline.schemas import RegisterModelRequest
from image_pipeline.registry import (
    list_models,
    register_model,
    list_loras,
    get_models_dir,
    set_models_dir,
    get_lora_dir,
    set_lora_dir,
)

logger = logging.getLogger("image_pipeline")
router = APIRouter(prefix="/api/models", tags=["models"])

@router.get("/list")
async def list_all():
    return {
        "models": list_models(),
        "loras": list_loras(),
    }

@router.post("/register")
async def register(req: RegisterModelRequest) -> Dict[str, str]:
    register_model(req.dict())
    logger.info("Registered/updated model: %s", req.id)
    return {"status": "ok", "id": req.id}


class DirectoryRequest(BaseModel):
    path: str


@router.get("/directory")
async def get_model_directory():
    path = get_models_dir()
    return {"path": str(path) if path else None}


@router.post("/directory")
async def set_model_directory(req: DirectoryRequest):
    set_models_dir(req.path)
    return {"ok": True, "path": req.path}


@router.get("/lora_directory")
async def get_lora_directory():
    path = get_lora_dir()
    return {"path": str(path) if path else None}


@router.post("/lora_directory")
async def set_lora_directory(req: DirectoryRequest):
    set_lora_dir(req.path)
    return {"ok": True, "path": req.path}
