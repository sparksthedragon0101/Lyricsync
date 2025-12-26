import uuid
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError
from image_pipeline.schemas import GenRequest, GenResponse, JobStatus, PrecisionT
from image_pipeline.worker import (
    JOBS,
    JOB_QUEUE,
    ensure_pipeline_loaded,
    release_pipeline,
    is_pipeline_loaded,
)


logger = logging.getLogger("image_pipeline")
router = APIRouter(prefix="/api/image", tags=["image"])


class PipelineControlRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: str
    precision: PrecisionT = "fp16"


@router.post("/generate", response_model=GenResponse)
async def generate(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        req = GenRequest(**payload)
    except ValidationError as exc:
        logger.warning("Invalid image payload: %s", exc)
        raise HTTPException(status_code=400, detail=exc.errors())

    if not req.slug or not req.model_id:
        raise HTTPException(status_code=400, detail="slug and model_id are required")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "progress": "waiting for GPU worker"}
    await JOB_QUEUE.put((job_id, req.dict()))
    logger.info("Queued job %s for slug=%s", job_id, req.slug)
    return GenResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=JobStatus)
async def status(job_id: str):
    data = JOBS.get(job_id)
    if not data:
        return JobStatus(status="unknown")
    return JobStatus(**data)


@router.get("/ping")
async def ping():
    return {"ok": True}


@router.post("/pipeline/preload")
async def pipeline_preload(req: PipelineControlRequest):
    try:
        await ensure_pipeline_loaded(req.model_id, req.precision)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "model_id": req.model_id, "precision": req.precision}


@router.post("/pipeline/release")
async def pipeline_release(req: PipelineControlRequest):
    try:
        released = await release_pipeline(req.model_id, req.precision)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "model_id": req.model_id,
        "precision": req.precision,
        "released": released,
    }


@router.get("/pipeline/status")
async def pipeline_status(model_id: str, precision: PrecisionT = "fp16"):
    return {"loaded": is_pipeline_loaded(model_id, precision)}
