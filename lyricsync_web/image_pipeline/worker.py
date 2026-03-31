import asyncio
import logging
from typing import Dict, Tuple, Callable, Set
import torch

from .schemas import GenRequest, PrecisionT
from .loader import load_sdxl_pipeline, apply_loras
from .registry import get_model
from .utils import ensure_output_dir, save_png_with_meta

logger = logging.getLogger("image_pipeline")

def _safe_to_cpu(pipe):
    """Move pipeline to CPU while suppressing the diffusers float16 warning."""
    try:
        from diffusers.utils import logging as diffusers_logging
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old_level = diffusers_logging.get_verbosity()
            diffusers_logging.set_verbosity_error()
            if hasattr(pipe, "to"):
                pipe.to("cpu")
            diffusers_logging.set_verbosity(old_level)
    except Exception:
        pass

# In-memory job state
JOB_QUEUE: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
JOBS: Dict[str, dict] = {}
MAX_JOBS = 50  # Prevent infinite growth of job history

# Cache: (model_id, precision, vae_id, text_encoder_id) -> pipe
_PIPE_CACHE: Dict[Tuple[str, str, str | None, str | None], object] = {}
_PIPELINE_LOCKS: Dict[Tuple[str, str, str | None, str | None], asyncio.Lock] = {}
_KEEP_ALIVE_KEYS: Set[Tuple[str, str, str | None, str | None]] = set()
_ACTIVE_PIPELINES: Set[Tuple[str, str, str | None, str | None]] = set()

ProgressCallback = Callable[[Dict[str, object]], None]


async def start_worker():
    asyncio.create_task(_gpu_worker())


def _update_job(job_id: str, **fields):
    data = JOBS.get(job_id, {})
    data.update(fields)
    JOBS[job_id] = data
    
    # Prune old jobs if too many
    if len(JOBS) > MAX_JOBS:
        # Sort by 'started_at' or just pop oldest keys. 
        # Since it's a dict, we can pop the first few keys (insertion order in Python 3.7+)
        to_remove = len(JOBS) - MAX_JOBS
        keys = list(JOBS.keys())
        for i in range(to_remove):
            JOBS.pop(keys[i], None)


def _run_generation_sync(job_id: str, req: GenRequest, pipe, progress_cb: ProgressCallback):
    if req.loras:
        progress_cb({"progress": f"applying {len(req.loras)} LoRA(s)"})
        pipe = apply_loras(pipe, [l.dict() for l in req.loras])
    else:
        progress_cb({"progress": "no LoRAs applied"})

    # Architecture detection
    from diffusers import FluxPipeline
    try:
        from diffusers import ZImagePipeline
    except ImportError:
        class ZImagePipeline: pass
    is_flux = isinstance(pipe, FluxPipeline)
    is_zimage = isinstance(pipe, ZImagePipeline)
    logger.info("Detected pipeline type: %s", "Flux" if is_flux else ("Z-Image" if is_zimage else "SDXL"))

    generator = torch.Generator(device="cpu" if is_flux else getattr(pipe, "_execution_device", "cuda"))
    if req.seed is None:
        import secrets
        req.seed = secrets.randbelow(2**32)
        logger.info("Generated new random seed: %d", req.seed)
    else:
        logger.info("Using provided seed: %d", req.seed)
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
    
    # Optional subtle boost for DreamShaper if the prompt is very short
    final_prompt = req.prompt
    final_negative = req.negative_prompt
    if "dreamshaper" in req.model_id.lower():
        # Only add a very subtle quality boost if no other quality tags are present
        quality_tags = ["masterpiece", "best quality", "detailed", "8k", "highres"]
        if not any(tag in final_prompt.lower() for tag in quality_tags):
            if final_prompt:
                # We add 'high quality' but avoid '8k' or 'ultra detailed' to respect artistic styles like 'hand-drawn'
                pass # Let's actually just trust the user's prompt more
        
        # Keep a basic negative prompt to reduce common SDXL artifacts/nudity
        base_negative = "nude, naked, blurry, deformed, disfigured, watermark, text, signature, low resolution, bad anatomy"
        if not final_negative:
            final_negative = base_negative
        elif base_negative not in final_negative:
            # Append if not present
            final_negative = f"{final_negative}, {base_negative}"

    logger.info("Compel init for prompt: %s", final_prompt)
    
    # --- Prompt Handling ---
    # Adjust dimensions to be multiples of 16 for DiT/VAE compatibility (pad)
    # 1080 -> 1088, etc.
    orig_w, orig_h = req.width, req.height
    adj_w = ((orig_w + 15) // 16) * 16
    adj_h = ((orig_h + 15) // 16) * 16
    if adj_w != orig_w or adj_h != orig_h:
        logger.info(f"Padding resolution for alignment: {orig_w}x{orig_h} -> {adj_w}x{adj_h}")

    kwargs = {
        "width": adj_w,
        "height": adj_h,
        "num_inference_steps": req.steps,
        "guidance_scale": req.guidance,
        "num_images_per_prompt": req.num_images,
        "generator": generator,
        "callback_on_step_end": _on_step_end,
        "callback_on_step_end_tensor_inputs": [],
    }

    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    with torch.inference_mode():
        if is_flux:
            logger.info("Using Flux prompt handling")
            # Flux handles prompts natively via tokenizer/tokenizer_2
            kwargs["prompt"] = req.prompt
            kwargs["prompt_2"] = req.prompt_2 if req.prompt_2 else req.prompt
            out = pipe(**kwargs)
        elif is_zimage:
            logger.info("Using Z-Image native prompt call (Simplified for stability)")
            # Disable progress callbacks temporarily to debug modular pipeline hang
            if "callback_on_step_end" in kwargs: del kwargs["callback_on_step_end"]
            if "callback_on_step_end_tensor_inputs" in kwargs: del kwargs["callback_on_step_end_tensor_inputs"]
            
            # Use native prompt passing for better integration with CPU offloading
            kwargs["prompt"] = req.prompt
            if req.guidance > 1.0:
                kwargs["negative_prompt"] = req.negative_prompt
            else:
                kwargs["negative_prompt"] = None
                
            out = pipe(**kwargs)
        else:
            logger.info("Using SDXL Compel prompt handling")
            # Use Compel for long prompt support in SDXL
            from compel import Compel, ReturnedEmbeddingsType
            
            compel = Compel(
                tokenizer=[pipe.tokenizer, pipe.tokenizer_2],
                text_encoder=[pipe.text_encoder, pipe.text_encoder_2],
                returned_embeddings_type=ReturnedEmbeddingsType.PENULTIMATE_HIDDEN_STATES_NON_NORMALIZED,
                requires_pooled=[False, True]
            )
            
            # Use negative prompt as empty string if None
            pos_prompt = req.prompt
            neg_prompt = req.negative_prompt if req.negative_prompt else ""
            
            # Generate embeddings (Compel returns (embeds, pooled) when requires_pooled is True)
            conditioning_all, pooled_all = compel([pos_prompt, neg_prompt])
            
            conditioning = conditioning_all[0:1]
            neg_conditioning = conditioning_all[1:2]
            pooled = pooled_all[0:1]
            neg_pooled = pooled_all[1:2]

            # Move tensors to correct device and dtype individually
            device = pipe.device
            dtype = pipe.dtype if hasattr(pipe, "dtype") else conditioning.dtype
            conditioning = conditioning.to(device=device, dtype=dtype)
            neg_conditioning = neg_conditioning.to(device=device, dtype=dtype)
            if pooled is not None: pooled = pooled.to(device=device, dtype=dtype)
            if neg_pooled is not None: neg_pooled = neg_pooled.to(device=device, dtype=dtype)
            
            # Ensure embeddings match pipe device and dtype
            device = pipe.device
            dtype = pipe.dtype if hasattr(pipe, "dtype") else conditioning.dtype
            
            conditioning = conditioning.to(device=device, dtype=dtype)
            if pooled is not None:
                 pooled = pooled.to(device=device, dtype=dtype)
            if neg_conditioning is not None:
                 neg_conditioning = neg_conditioning.to(device=device, dtype=dtype)
            if neg_pooled is not None:
                 neg_pooled = neg_pooled.to(device=device, dtype=dtype)
    
            logger.info("SDXL Conditioning shape: %s", conditioning.shape)
            
            kwargs.update({
                "prompt_embeds": conditioning,
                "pooled_prompt_embeds": pooled,
                "negative_prompt_embeds": neg_conditioning,
                "negative_pooled_prompt_embeds": neg_pooled,
            })
            out = pipe(**kwargs)
            
            # Explicit cleanup of large embedding tensors to free VRAM for potentially shared components
            del conditioning, neg_conditioning, pooled, neg_pooled, conditioning_all, pooled_all
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
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
                "negative_prompt": str(final_negative),
            },
        )
        rel_path = out_path.relative_to(project_dir).as_posix()
        saved.append(rel_path)

    logger.info("Generation finished. Refinement enabled: %s, count: %d", req.enable_refinement, len(images))

    if req.enable_refinement and len(images) > 0:
        progress_cb({"progress": "starting refinement pass"})
        logger.info("Starting refinement pass with strength %.2f", req.refinement_strength)
        from diffusers import AutoPipelineForImage2Image

        # Reuse components from the txt2img pipe to avoid reloading
        # We use the same pipe components but wrap them in the Img2Img pipeline
        refiner = AutoPipelineForImage2Image.from_pipe(pipe)
        
        # Ensure refiner is on the correct device (handle offloading)
        # Fix for "float16 on cpu" error from pipe components sharing
        try:
            if torch.cuda.is_available():
                # Check for offload hooks more robustly
                # Diffusers uses _offload_hook or _hf_hook depending on version/method
                has_offload = (
                    hasattr(pipe, "_offload_hook") and pipe._offload_hook is not None
                ) or getattr(pipe, "hf_device_map", None) or "_cpu_offload_hook" in str(pipe)
                
                # Clear cache before refinement pass to fit refined components in VRAM
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                if has_offload:
                    if is_zimage or is_flux:
                        logger.info("Main pipe has offload, enabling sequential offload for refiner")
                        refiner.enable_sequential_cpu_offload()
                    else:
                        logger.info("Main pipe has offload, enabling model cpu offload for refiner")
                        refiner.enable_model_cpu_offload()
                else:
                    logger.info("Moving refiner to CUDA (no offload detected on main pipe)")
                    refiner.to("cuda")
            else:
                 logger.warning("CUDA not available for refinement, functionality may be limited.")
        except Exception as e:
            logger.warning("Error setting refiner device: %s", e)
        
        refined_images = []
        for idx, init_image in enumerate(images):
            progress_cb({"progress": f"refining image {idx + 1}/{len(images)}"})
            
            # Run refinement
            # Strength 0.3 means "keep 70% of original, change 30%"
            # Steps usually can be lower for refinement, but we'll use same or default 20
            r_out = refiner(
                prompt=req.prompt,
                prompt_2=req.prompt, # SDXL often needs prompt_2
                negative_prompt=final_negative,
                negative_prompt_2=final_negative,
                image=init_image,
                strength=req.refinement_strength,
                num_inference_steps=max(20, int(req.steps * 0.8)), # slight reduction usually fine
                guidance_scale=req.guidance,
                generator=generator, # continue rng
            )
            refined_img = r_out.images[0]
            
            # Save refined image
            # We'll overwrite the original file to satisfy the user's "refinement" workflow
            out_path = out_dir / f"{job_id}_{idx}.png" # Same path
            save_png_with_meta(
                refined_img,
                out_path,
                metadata={
                    "model_id": req.model_id,
                    "steps": str(req.steps),
                    "guidance": str(req.guidance),
                    "seed": str(req.seed) if req.seed is not None else "",
                    "width": str(req.width),
                    "height": str(req.height),
                    "refined": "true",
                    "refinement_strength": str(req.refinement_strength)
                },
            )
            # Update the list content (though path is same)
            # saved[idx] is already the relative path
            
            refined_images.append(refined_img)
            
        images = refined_images # Update distinct images list if we were returning them objects

    meta = {
        "model_id": req.model_id,
        "seed": req.seed,
        "steps": req.steps,
        "guidance": req.guidance,
        "width": req.width,
        "height": req.height,
        "negative_prompt": final_negative,
    }
    return saved, meta


def _cache_key(model_id: str, precision: str, vae_id: str | None = None, text_encoder_id: str | None = None) -> Tuple[str, str, str | None, str | None]:
    return (model_id, precision, vae_id, text_encoder_id)


async def _lock_for_key(cache_key: Tuple[str, str, str | None, str | None]) -> asyncio.Lock:
    lock = _PIPELINE_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _PIPELINE_LOCKS[cache_key] = lock
    return lock


async def _get_or_load_pipeline(
    model_id: str,
    precision: PrecisionT,
    vae_id: str | None = None,
    text_encoder_id: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> Tuple[object, Tuple[str, str, str | None, str | None]]:
    cache_key = _cache_key(model_id, precision, vae_id, text_encoder_id)
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
                vae_id or model.get("vae"),
                precision=precision,
                config_path=model.get("config"),
                device="cuda",
                text_encoder_path=text_encoder_id or model.get("text_encoder"),
            )

        pipe = await asyncio.to_thread(_load)
        _PIPE_CACHE[cache_key] = pipe
        if progress_cb:
            progress_cb({"progress": "pipeline components loaded"})
            
        # Optional: ensure it's move to device if needed (loader usually does this)
        if progress_cb:
            progress_cb({"progress": "model ready"})
        return pipe, cache_key


async def ensure_pipeline_loaded(
    model_id: str, 
    precision: PrecisionT = "fp16",
    vae_id: str | None = None,
    text_encoder_id: str | None = None,
) -> Tuple[str, str, str | None, str | None]:
    pipe, cache_key = await _get_or_load_pipeline(model_id, precision, vae_id, text_encoder_id)
    _KEEP_ALIVE_KEYS.add(cache_key)
    return cache_key


async def release_pipeline(
    model_id: str, 
    precision: PrecisionT = "fp16",
    vae_id: str | None = None,
    text_encoder_id: str | None = None,
) -> bool:
    cache_key = _cache_key(model_id, precision, vae_id, text_encoder_id)
    if cache_key in _ACTIVE_PIPELINES:
        raise RuntimeError("Cannot unload pipeline while it is in use")
    lock = await _lock_for_key(cache_key)
    async with lock:
        _KEEP_ALIVE_KEYS.discard(cache_key)
        cached = _PIPE_CACHE.pop(cache_key, None)
    if cached:
        await asyncio.to_thread(_safe_to_cpu, cached)
        if torch.cuda.is_available():
            await asyncio.to_thread(torch.cuda.empty_cache)
        return True
    return False


def is_pipeline_loaded(
    model_id: str, 
    precision: PrecisionT = "fp16",
    vae_id: str | None = None,
    text_encoder_id: str | None = None,
) -> bool:
    return _cache_key(model_id, precision, vae_id, text_encoder_id) in _PIPE_CACHE


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

            pipe, cache_key = await _get_or_load_pipeline(req.model_id, req.precision, getattr(req, "vae_id", None), getattr(req, "text_encoder_id", None), progress_cb)
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
                        # Move to CPU first (suppresses some warnings/errors) then delete
                        _safe_to_cpu(cached)
                        del cached
                        import gc
                        gc.collect()
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
            _safe_to_cpu(cached)
            # Explicitly delete
            del cached
            unloaded_models.append(f"{key[0]} ({key[1]})")

    # 3. Clear job history
    JOBS.clear()

    # 4. Garbage collection and CUDA cache clear
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
        "jobs_history_cleared": True,
        "vram_cleared": True
    }
