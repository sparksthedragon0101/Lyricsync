import logging
import os
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from image_pipeline.schemas import RegisterModelRequest
from image_pipeline.registry import (
    list_models,
    register_model,
    list_loras,
    list_vaes,
    list_text_encoders,
    get_models_dir,
    set_models_dir,
    get_lora_dir,
    set_lora_dir,
    get_vae_dir,
    set_vae_dir,
    get_text_encoder_dir,
    set_text_encoder_dir,
)

logger = logging.getLogger("image_pipeline")
router = APIRouter(prefix="/api/models", tags=["models"])

@router.get("/list")
async def list_all():
    return {
        "models": list_models(),
        "loras": list_loras(),
        "vaes": list_vaes(),
        "text_encoders": list_text_encoders(),
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


@router.get("/vae_directory")
async def get_vae_directory():
    path = get_vae_dir()
    return {"path": str(path) if path else None}


@router.post("/vae_directory")
async def set_vae_directory(req: DirectoryRequest):
    set_vae_dir(req.path)
    return {"ok": True, "path": req.path}


@router.get("/text_encoder_directory")
async def get_text_encoder_directory():
    path = get_text_encoder_dir()
    return {"path": str(path) if path else None}


@router.post("/text_encoder_directory")
async def set_text_encoder_directory(req: DirectoryRequest):
    set_text_encoder_dir(req.path)
    return {"ok": True, "path": req.path}


@router.post("/lora/upload")
async def upload_lora(file: UploadFile = File(...)):
    lora_dir = get_lora_dir() or Path("app/models/loras")
    if not lora_dir.exists():
        lora_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename
    if not filename.endswith(".safetensors") and not filename.endswith(".gguf"):
        filename += ".safetensors"

    target_path = lora_dir / filename

    try:
        with open(target_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)
    except Exception as e:
        if target_path.exists():
            target_path.unlink()
        logger.error("LoRA upload failed: %s", str(e), exc_info=True)
        raise HTTPException(500, f"Upload failed: {str(e)}")

    logger.info("Successfully uploaded LoRA: %s (%d bytes)", target_path, target_path.stat().st_size)
    return {"ok": True, "path": str(target_path), "filename": filename}
