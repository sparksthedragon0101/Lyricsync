from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any


PrecisionT = Literal["fp16", "fp32"]


class LoRASpec(BaseModel):
    path: str
    weight: float = Field(0.8, ge=0.0, le=2.0)


class GenRequest(BaseModel):
    slug: str
    model_id: str
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 28
    guidance: float = 6.5
    seed: Optional[int] = None
    num_images: int = 1
    loras: List[LoRASpec] = []
    precision: PrecisionT = "fp16"
    style: Optional[str] = None
    model_config = {"protected_namespaces": ()}


class GenResponse(BaseModel):
    job_id: str


class JobResult(BaseModel):
    images: List[str]
    metadata: Dict[str, Any]


class JobStatus(BaseModel):
    status: Literal["queued", "running", "done", "error", "unknown"]
    progress: Optional[str] = None
    result: Optional[JobResult] = None
    error: Optional[str] = None


class RegisterModelRequest(BaseModel):
    id: str
    type: Literal["sdxl", "sd15"]
    path: str
    vae: Optional[str] = None
    config: Optional[str] = None
    tags: List[str] = []
    default_precision: PrecisionT = "fp16"
    model_config = {"protected_namespaces": ()}

