import torch
from diffusers import StableDiffusionXLPipeline, AutoencoderKL
from typing import Optional
from pathlib import Path


def _dtype_for_precision(precision: str):
    return torch.float16 if precision == "fp16" else torch.float32

_MODULE_DIR = Path(__file__).resolve().parents[1]  # lyricsync_web/image_pipeline
_APP_ROOT = _MODULE_DIR.parent  # lyricsync_web
_REPO_ROOT = _APP_ROOT.parent  # project root


def _resolve_single_file(path: str | Path) -> Path:
    candidates = []
    p = Path(path).expanduser()
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([
            p,
            _REPO_ROOT / p,
            _APP_ROOT / p,
        ])

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except FileNotFoundError:
            continue
        if resolved.exists():
            return resolved

    raise FileNotFoundError(f"Model file not found. Tried: {', '.join(str(c) for c in candidates)}")

def load_sdxl_pipeline(
    base_path: str,
    vae_path: Optional[str],
    *,
    precision: str = "fp16",
    config_path: Optional[str] = None,
    device: Optional[str] = None,
) -> StableDiffusionXLPipeline:
    dtype = _dtype_for_precision(precision)

    model_path = _resolve_single_file(base_path)
    config_file = _resolve_single_file(config_path) if config_path else None
    local_only = config_file is not None

    pipe = StableDiffusionXLPipeline.from_single_file(
        str(model_path),
        torch_dtype=dtype,
        use_safetensors=True,
        local_files_only=local_only,
        original_config_file=str(config_file) if config_file else None,
    )

    if vae_path:
        vae_file = _resolve_single_file(vae_path)
        vae = AutoencoderKL.from_single_file(str(vae_file), torch_dtype=dtype, use_safetensors=True)
        pipe.vae = vae

    # Prefer keeping the model resident on GPU for max throughput
    target_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        pipe.to(target_device)
    except Exception:
        # fallback to CPU offload if direct move fails
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pass

    try:
        pipe.enable_vae_tiling()
        pipe.enable_attention_slicing()
    except Exception:
        pass

    return pipe

def apply_loras(pipe, lora_specs):
    """Apply LoRAs by loading and fusing at the requested weight.
    lora_specs: List[{"path": str, "weight": float}]
    Gracefully skips when the PEFT backend isn't available so jobs still run.
    """
    import logging
    from peft import PeftModel

    log = logging.getLogger("image_pipeline")
    for lora_spec in lora_specs:
        lora_path = lora_spec["path"]
        weight = lora_spec.get("weight", 1.0)
        try:
            log.info("Attempting to apply LoRA %s with diffusers", lora_path)
            # This is the diffusers method, which can handle single-file LoRAs
            pipe.load_lora_weights(lora_path, weight=weight)
            log.info("Successfully applied LoRA %s with diffusers", lora_path)
        except Exception as diffusers_exc:
            log.warning("Diffusers failed to load LoRA %s: %s", lora_path, diffusers_exc)
            try:
                log.info("Attempting to apply LoRA %s with peft", lora_path)
                # This is the peft method, which expects a directory with an adapter_config.json
                pipe.unet = PeftModel.from_pretrained(pipe.unet, lora_path)
                if hasattr(pipe, 'text_encoder_2'):
                    pipe.text_encoder = PeftModel.from_pretrained(pipe.text_encoder, lora_path)
                    pipe.text_encoder_2 = PeftModel.from_pretrained(pipe.text_encoder_2, lora_path)
                log.info("Successfully applied LoRA %s with peft", lora_path)
            except Exception as peft_exc:
                log.error("Both diffusers and peft failed to load LoRA %s", lora_path, exc_info=True)
    return pipe
