import asyncio
import logging
from typing import Dict, Tuple, Callable, Set
import torch

from .schemas import GenRequest, PrecisionT
from .loader import load_sdxl_pipeline, apply_loras
from .registry import get_model
from .utils import ensure_output_dir, save_png_with_meta

logger = logging.getLogger("image_pipeline")

# In-memory job state
JOB_QUEUE: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
JOBS: Dict[str, dict] = {}

# Cache: (model_id, precision) -> pipe
_PIPE_CACHE: Dict[Tuple[str, str], object] = {}
_PIPELINE_LOCKS: Dict[Tuple[str, str], asyncio.Lock] = {}
_KEEP_ALIVE_KEYS: Set[Tuple[str, str]] = set()
_ACTIVE_PIPELINES: Set[Tuple[str, str]] = set()

ProgressCallback = Callable[[Dict[str, object]], None]


async def start_worker():
    asyncio.create_task(_gpu_worker())


def _update_job(job_id: str, **fields):
    data = JOBS.get(job_id, {})
    data.update(fields)
    JOBS[job_id] = data


def _run_generation_sync(job_id: str, req: GenRequest, pipe, progress_cb: ProgressCallback):
    if req.loras:
        progress_cb({"progress": f"applying {len(req.loras)} LoRA(s)"})
        pipe = apply_loras(pipe, [l.dict() for l in req.loras])
    else:
        progress_cb({"progress": "no LoRAs applied"})

    generator = torch.Generator(device=getattr(pipe, "_execution_device", "cuda"))
    if req.seed is not None:
        generator.manual_seed(int(req.seed))

    total_steps = max(req.steps, 1)
    progress_state = {"last_step": -1}

    def _on_step_end(_pipe, step: int, timestep: int, callback_kwargs=None):
        current_step = step + 1
        if current_step == progress_state["last_step"]:
            return {}
        progress_state["last_step"] = current_step
        pct = max(1, min(100, int(current_step / total_steps * 100)))
        progress_cb({"progress": f"samples {current_step}/{total_steps} steps (~{pct}%)"})
        return {}

    progress_cb({"progress": "starting diffusion steps"})
    
    # Use Compel for long prompt support
    from compel import Compel, ReturnedEmbeddingsType
    
    # Initialize Compel for SDXL (requires both tokenizers/encoders)
    compel = Compel(
        tokenizer=[pipe.tokenizer, pipe.tokenizer_2],
        text_encoder=[pipe.text_encoder, pipe.text_encoder_2],
        returned_embeddings_type=ReturnedEmbeddingsType.PENULTIMATE_HIDDEN_STATES_NON_NORMALIZED,
        requires_pooled=[False, True]
    )
    
    # Generate embeddings
    conditioning, pooled = compel(req.prompt)
    neg_conditioning, neg_pooled = compel(req.negative_prompt)
    
    out = pipe(
        prompt_embeds=conditioning,
        pooled_prompt_embeds=pooled,
        negative_prompt_embeds=neg_conditioning,
        negative_pooled_prompt_embeds=neg_pooled,
        width=req.width,
        height=req.height,
        num_inference_steps=req.steps,
        guidance_scale=req.guidance,
        num_images_per_prompt=req.num_images,
        generator=generator,
        callback_on_step_end=_on_step_end,
        callback_on_step_end_tensor_inputs=[],
    )
    images = out.images

    out_dir = ensure_output_dir(req.slug)
    project_dir = out_dir.parent
    saved = []
    for idx, im in enumerate(images):
        progress_cb({"progress": f"saving image {idx + 1}/{len(images)}"})
        out_path = out_dir / f"{job_id}_{idx}.png"
        save_png_with_meta(
            im,
            out_path,
            metadata={
                "model_id": req.model_id,
                "steps": str(req.steps),
                "guidance": str(req.guidance),
                "seed": str(req.seed) if req.seed is not None else "",
                "width": str(req.width),
                "height": str(req.height),
            },
        )
        rel_path = out_path.relative_to(project_dir).as_posix()
        saved.append(rel_path)

    meta = {
        "model_id": req.model_id,
        "seed": req.seed,
        "steps": req.steps,
        "guidance": req.guidance,
        "width": req.width,
        "height": req.height,
    }
    return saved, meta


def _cache_key(model_id: str, precision: str) -> Tuple[str, str]:
    return (model_id, precision)


async def _lock_for_key(cache_key: Tuple[str, str]) -> asyncio.Lock:
    lock = _PIPELINE_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _PIPELINE_LOCKS[cache_key] = lock
    return lock


async def _get_or_load_pipeline(
    model_id: str,
    precision: PrecisionT,
    progress_cb: ProgressCallback | None = None,
) -> Tuple[object, Tuple[str, str]]:
    cache_key = _cache_key(model_id, precision)
    lock = await _lock_for_key(cache_key)
    async with lock:
        pipe = _PIPE_CACHE.get(cache_key)
        model = get_model(model_id)
        if not model:
            raise RuntimeError(f"Unknown model_id={model_id}")
        if pipe is not None:
            if progress_cb:
                progress_cb({"progress": "reusing cached model"})
            return pipe, cache_key
        if progress_cb:
            progress_cb({"progress": "loading model into GPU"})
        logger.info("Loading pipeline for %s (%s)", model["id"], precision)

        def _load():
            return load_sdxl_pipeline(
                model["path"],
                model.get("vae"),
                precision=precision,
                config_path=model.get("config"),
                device="cuda",
            )

        pipe = await asyncio.to_thread(_load)
        _PIPE_CACHE[cache_key] = pipe
        if progress_cb:
            progress_cb({"progress": "model ready"})
        return pipe, cache_key


async def ensure_pipeline_loaded(model_id: str, precision: PrecisionT = "fp16") -> Tuple[str, str]:
    pipe, cache_key = await _get_or_load_pipeline(model_id, precision)
    _KEEP_ALIVE_KEYS.add(cache_key)
    return cache_key


async def release_pipeline(model_id: str, precision: PrecisionT = "fp16") -> bool:
    cache_key = _cache_key(model_id, precision)
    if cache_key in _ACTIVE_PIPELINES:
        raise RuntimeError("Cannot unload pipeline while it is in use")
    lock = await _lock_for_key(cache_key)
    async with lock:
        _KEEP_ALIVE_KEYS.discard(cache_key)
        cached = _PIPE_CACHE.pop(cache_key, None)
    if cached:
        await asyncio.to_thread(lambda: cached.to("cpu"))
        if torch.cuda.is_available():
            await asyncio.to_thread(torch.cuda.empty_cache)
        return True
    return False


def is_pipeline_loaded(model_id: str, precision: PrecisionT = "fp16") -> bool:
    return _cache_key(model_id, precision) in _PIPE_CACHE


async def _gpu_worker():
    while True:
        job_id, payload = await JOB_QUEUE.get()
        req: GenRequest | None = None
        pipe = None
        cache_key = None
        try:
            _update_job(job_id, status="running", progress="initializing")
            req = GenRequest(**payload)
            loop = asyncio.get_running_loop()

            def progress_cb(fields: Dict[str, object]):
                loop.call_soon_threadsafe(lambda: _update_job(job_id, **fields))

            pipe, cache_key = await _get_or_load_pipeline(req.model_id, req.precision, progress_cb)
            _ACTIVE_PIPELINES.add(cache_key)

            logger.info("Generating: slug=%s prompt=%.64s...", req.slug, req.prompt)
            saved, metadata = await asyncio.to_thread(_run_generation_sync, job_id, req, pipe, progress_cb)

            _update_job(
                job_id,
                status="done",
                progress="completed",
                result={
                    "images": saved,
                    "metadata": metadata,
                },
            )
        except Exception as e:
            logger.exception("Job %s failed", job_id)
            _update_job(job_id, status="error", error=str(e))
        finally:
            JOB_QUEUE.task_done()
            if cache_key:
                _ACTIVE_PIPELINES.discard(cache_key)
                if cache_key not in _KEEP_ALIVE_KEYS:
                    cached = _PIPE_CACHE.pop(cache_key, None)
                    if cached:
                        try:
                            cached.to("cpu")
                        except Exception:
                            pass
                    if torch.cuda.is_available():
                        try:
                            torch.cuda.empty_cache()
                        except Exception:
                            pass


async def force_reset_worker() -> dict:
    """Force clears the job queue and unloads all models to free VRAM."""
    import gc
    
    # 1. Clear queue
    cleared_jobs = 0
    while not JOB_QUEUE.empty():
        try:
            JOB_QUEUE.get_nowait()
            JOB_QUEUE.task_done()
            cleared_jobs += 1
        except asyncio.QueueEmpty:
            break
            
    # 2. Unload all pipelines
    unloaded_models = []
    keys_to_remove = list(_PIPE_CACHE.keys())
    
    for key in keys_to_remove:
        # Force release even if locked/active (reset scenario)
        _ACTIVE_PIPELINES.discard(key)
        _KEEP_ALIVE_KEYS.discard(key)
        
        cached = _PIPE_CACHE.pop(key, None)
        if cached:
            try:
                # Move to CPU to free GPU ram immediately
                if hasattr(cached, "to"):
                    cached.to("cpu")
                # Explicitly delete
                del cached
            except Exception:
                pass
            unloaded_models.append(f"{key[0]} ({key[1]})")

    # 3. Garbage collection and CUDA cache clear
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception:
            pass
            
    logger.warning("Worker forced reset. Cleared %d jobs, unloaded: %s", cleared_jobs, unloaded_models)
    
    return {
        "cleared_jobs": cleared_jobs,
        "unloaded_models": unloaded_models,
        "vram_cleared": True
    }
