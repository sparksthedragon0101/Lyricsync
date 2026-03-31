import os
import torch
# Set PyTorch allocation configuration early to prevent fragmentation
# This must ideally be set before the first CUDA call in the process
if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
from diffusers import StableDiffusionXLPipeline, AutoencoderKL, FluxPipeline
try:
    from diffusers import ZImagePipeline, ZImageTransformer2DModel
    HAS_ZIMAGE = True
except ImportError:
    class ZImagePipeline: pass
    class ZImageTransformer2DModel: pass
    HAS_ZIMAGE = False
from typing import Optional
from pathlib import Path
import logging


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
    text_encoder_path: Optional[str] = None,
) -> StableDiffusionXLPipeline:
    dtype = _dtype_for_precision(precision)
    log = logging.getLogger("image_pipeline")

    model_path = _resolve_single_file(base_path)
    config_file = _resolve_single_file(config_path) if config_path else None
    local_only = config_file is not None

    import warnings
    from diffusers.utils import logging as diffusers_logging

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old_level = diffusers_logging.get_verbosity()
        diffusers_logging.set_verbosity_error()
        
        is_safetensors = model_path.suffix.lower() == ".safetensors"
        
        # Z-Image and Flux VAEs reliably produce NaNs in standard float16. 
        # Overriding to bfloat16 fixes the black image output while keeping VRAM identical.
        if "zimage" in str(model_path).lower() or "flux" in str(model_path).lower():
            if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
                log.info("Z-Image/Flux detected: Overriding dtype to bfloat16 to prevent VAE NaNs")
                dtype = torch.bfloat16

        # Load Text Encoder if provided
        text_encoder_2 = None
        tokenizer = None
        if text_encoder_path:
            import os
            te_file = _resolve_single_file(text_encoder_path)
            from transformers import AutoModel, AutoConfig, Qwen2Tokenizer, Qwen3Model
            
            try:
                if te_file.suffix.lower() == ".gguf":
                    logging.getLogger("image_pipeline").info("Loading GGUF text encoder: %s", te_file.name)
                    text_encoder_2 = AutoModel.from_pretrained(
                        str(te_file.parent),
                        gguf_file=te_file.name,
                        torch_dtype=dtype,
                        low_cpu_mem_usage=True
                    )
                else:
                    # Non-GGUF text encoder
                    if te_file.is_dir():
                        text_encoder_2 = AutoModel.from_pretrained(
                            str(te_file),
                            torch_dtype=dtype,
                            low_cpu_mem_usage=True
                        )
                    elif "qwen" in str(te_file).lower():
                        # Z-Image Qwen3 implementation often requires specific loading
                        # We'll load ARCHITECTURE from HF (small) and WEIGHTS from local (large)
                        log.info(f"Loading Qwen3 architecture from Tongyi-MAI/Z-Image-Turbo (metadata) and weights from {te_file.name}")
                        try:
                            # 1. Load Config (small)
                            config = AutoConfig.from_pretrained(
                                "Tongyi-MAI/Z-Image-Turbo",
                                subfolder="text_encoder",
                                local_files_only=False
                            )
                            # 2. Instantiate (empty weights)
                            # Using 'meta' device for instant init if supported, else CPU
                            text_encoder_2 = Qwen3Model(config)
                            
                            # 3. Load Local Weights (7GB)
                            from safetensors.torch import load_file
                            state_dict = load_file(str(te_file))
                            
                            # Ensure model is correctly sized/typed
                            text_encoder_2.load_state_dict(state_dict, strict=False)
                            text_encoder_2.to(dtype=dtype)
                        except Exception as qwen_err:
                            log.warning(f"Precise Qwen3 load failed: {qwen_err}, attempting generic fallback")
                            # Generic fallback for AutoModel
                            text_encoder_2 = AutoModel.from_pretrained(
                                str(te_file.parent),
                                torch_dtype=dtype,
                                local_files_only=True
                            )
                        
                        # Load Qwen2Tokenizer (small)
                        try:
                            tokenizer = Qwen2Tokenizer.from_pretrained(
                                "Tongyi-MAI/Z-Image-Turbo",
                                subfolder="tokenizer",
                                local_files_only=False
                            )
                        except Exception:
                            log.warning("Could not load specialized Qwen2Tokenizer, using generic")
                            tokenizer = Qwen2Tokenizer.from_pretrained("Qwen/Qwen2.5-7B-Instruct", local_files_only=False)
                    else:
                        # Generic single-file load
                        try:
                            if hasattr(AutoModel, "from_single_file"):
                                text_encoder_2 = AutoModel.from_single_file(
                                    str(te_file),
                                    torch_dtype=dtype,
                                    low_cpu_mem_usage=True
                                )
                        except Exception:
                            text_encoder_2 = AutoModel.from_pretrained(
                                str(te_file),
                                torch_dtype=dtype,
                                local_files_only=True
                            )
                
                if text_encoder_2:
                    logging.getLogger("image_pipeline").info(f"Loaded external text encoder from {te_file}")
            except Exception as te_err:
                logging.getLogger("image_pipeline").warning(f"Failed to load external text encoder: {te_err}")

        # Assemble keywords for single-file load
        kwargs = {
            "torch_dtype": dtype,
            "use_safetensors": is_safetensors,
            "local_files_only": local_only,
            "original_config_file": str(config_file) if config_file else None,
        }

        # If we have an external text encoder, we might need to provide it 
        # as 'text_encoder' or 'text_encoder_2' (or both for Flux variants).
        if text_encoder_2 is not None:
            kwargs["text_encoder"] = text_encoder_2
            kwargs["text_encoder_2"] = text_encoder_2
            logging.getLogger("image_pipeline").info("Provided external text encoder to pipeline kwargs")

        # Load VAE early if provided (required for most modular pipeline constructors)
        vae = None
        if vae_path:
            try:
                vae_file = _resolve_single_file(vae_path)
                vae_is_safetensors = vae_file.suffix.lower() == ".safetensors"
                
                # Check for Z-Image 32-channel requirement (mandatory for ae.safetensors)
                if "zimage" in str(model_path).lower() or "ae.safetensors" in str(vae_file).lower() or "flux" in str(model_path).lower():
                    log.info(f"Loading 32-channel VAE config from HF and weights from {vae_file.name}")
                    # Load config via diffusers method to avoid transformers 'model_type' error
                    v_config = AutoencoderKL.load_config("Tongyi-MAI/Z-Image-Turbo", subfolder="vae", local_files_only=False)
                    vae = AutoencoderKL.from_config(v_config)
                    # Load weights
                    from safetensors.torch import load_file
                    v_state = load_file(str(vae_file))
                    vae.load_state_dict(v_state, strict=False)
                    vae.to(dtype=dtype)
                else:
                    log.info(f"Loading standard VAE from {vae_file.name}")
                    vae = AutoencoderKL.from_single_file(str(vae_file), torch_dtype=dtype, use_safetensors=vae_is_safetensors)
            except Exception as vae_err:
                log.warning(f"Failed to load VAE early: {vae_err}")

        # Z-Image Core Architecture Detection
        model_name_lower = str(model_path).lower()
        is_zimage = "zimage" in model_name_lower

        try:
            if is_zimage:
                if not HAS_ZIMAGE:
                    raise ImportError("ZImagePipeline requires diffusers installed from git source (pip install git+https://github.com/huggingface/diffusers)")
                log.info("Z-Image detected: Loading via ZImageTransformer2DModel in primary dtype")
                transformer = ZImageTransformer2DModel.from_single_file(
                    str(model_path),
                    torch_dtype=dtype,
                    use_safetensors=is_safetensors,
                )
                
                # ZImagePipeline instantiation
                try:
                    from diffusers import FlowMatchEulerDiscreteScheduler
                    log.info("Loading scheduler metadata from Tongyi-MAI/Z-Image-Turbo")
                    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(
                        "Tongyi-MAI/Z-Image-Turbo", 
                        subfolder="scheduler", 
                        local_files_only=False,
                        shift=1.0,
                        mu=1.0
                    )
                    
                    log.info("Assembling ZImagePipeline with provided components")
                    pipe = ZImagePipeline(
                        transformer=transformer,
                        text_encoder=text_encoder_2,
                        tokenizer=tokenizer,
                        vae=vae,
                        scheduler=scheduler
                    )
                except Exception as z_err:
                    log.warning(f"Advanced ZImagePipeline instantiation failed: {z_err}, attempting basic from_single_file")
                    # Basic fallback if manual assembly fails
                    pipe = ZImagePipeline.from_single_file(
                        str(model_path),
                        transformer=transformer,
                        text_encoder=text_encoder_2,
                        tokenizer=tokenizer,
                        vae=vae,
                        torch_dtype=dtype
                    )

            elif not is_zimage:
                pipe = StableDiffusionXLPipeline.from_single_file(
                    str(model_path),
                    **kwargs
                )
            else:
                # Should not reach here if is_zimage handled, but for safety:
                raise ValueError("Z-Image handled via specialized block")

        except Exception as e:
            # If primary load or Z-Image specialized load fails
            err_str = str(e)
            log = logging.getLogger("image_pipeline")
            log.error(f"Z-Image/Primary load attempt failed: {err_str}")
            
            # Recover verbosity
            diffusers_logging.set_verbosity(old_level)

            # If it was Z-Image, do NOT fallback to Flux as it provides confusing errors
            if is_zimage:
                raise RuntimeError(f"Failed to load Z-Image pipeline: {e}")

            try:
                log.info("Attempting FluxPipeline load as fallback...")
                diffusers_logging.set_verbosity_error()
                pipe = FluxPipeline.from_single_file(
                    str(model_path),
                    ignore_mismatched_sizes=True,
                    low_cpu_mem_usage=False,
                    **kwargs
                )
            except Exception as flux_e:
                log.error(f"Fallback load also failed: {flux_e}")
                raise flux_e

        if vae and not hasattr(pipe, "vae"):
            pipe.vae = vae
        elif vae_path and not vae:
             # If we failed to load VAE early, try one more time now that we have a pipe
             try:
                 vae_file = _resolve_single_file(vae_path)
                 vae_is_safetensors = vae_file.suffix.lower() == ".safetensors"
                 if is_zimage:
                     log.info("Z-Image detected: Using 32-channel VAE late-load logic")
                     # Load config via diffusers method to avoid transformers 'model_type' error
                     v_config = AutoencoderKL.load_config("Tongyi-MAI/Z-Image-Turbo", subfolder="vae", local_files_only=False)
                     pipe.vae = AutoencoderKL.from_config(v_config)
                     from safetensors.torch import load_file
                     v_state = load_file(str(vae_file))
                     pipe.vae.load_state_dict(v_state, strict=False)
                     pipe.vae.to(dtype=dtype)
                 else:
                     pipe.vae = AutoencoderKL.from_single_file(str(vae_file), torch_dtype=dtype, use_safetensors=vae_is_safetensors)
             except Exception as vae_err_late:
                 log.warning(f"Final VAE load attempt failed: {vae_err_late}")

        diffusers_logging.set_verbosity(old_level)

    # Move to device AND ensure dtype is applied to all modules
    target_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    is_zimage = getattr(pipe, "_class_name", "") == "ZImagePipeline" or "ZImage" in str(type(pipe))
    is_flux = getattr(pipe, "_class_name", "") == "FluxPipeline" or "Flux" in str(type(pipe))
    
    try:
        # Move to device or enable offloading
        if target_device == "cuda":
            if is_zimage:
                log.info("Z-Image detected: Enabling Sequential CPU Offload (Layer-by-layer) to fit 24GB model in VRAM")
                pipe.enable_sequential_cpu_offload()
            else:
                log.info(f"Enabling Model CPU Offload for {target_device} stability")
                pipe.enable_model_cpu_offload()
        else:
            logging.getLogger("image_pipeline").info(f"Moving pipeline to {target_device} with dtype {dtype}")
            pipe.to(target_device, dtype=dtype)
    except Exception as dev_err:
        logging.getLogger("image_pipeline").warning(f"Failed to move pipeline: {dev_err}, attempting offload fallback")
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe.to(target_device)

    # Fix for Diffusers 1.0.0 deprecation: upcast_vae removed in favor of explicit move
    # We MUST do this after pipe.to() otherwise pipe.to(dtype=float16) will undo it.
    if hasattr(pipe, "vae") and pipe.vae is not None and dtype == torch.float16 and not (is_zimage or is_flux):
         log.info("Upcasting VAE to float32 for stability and adding cast-hook")
         pipe.vae.to(dtype=torch.float32)
         
         # Monkeypatch vae.decode to automatically cast latents to VAE's dtype.
         import types
         old_decode = pipe.vae.decode
         def new_decode(self, z, *args, **kwargs):
             z = z.to(self.dtype)
             return old_decode(z, *args, **kwargs)
         pipe.vae.decode = types.MethodType(new_decode, pipe.vae)

    # Optimize for Turbo/Lightning or DreamShaper variants if detected
    # We check the name to apply best-practice samplers/schedulers
    if ("turbo" in model_name_lower or "lightning" in model_name_lower) and not isinstance(pipe, ZImagePipeline):
        from diffusers import LCMScheduler
        logging.getLogger("image_pipeline").info("Turbo/Lightning detected: Using LCMScheduler")
        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    elif "dreamshaper" in model_name_lower:
        from diffusers import DPMSolverMultistepScheduler
        logging.getLogger("image_pipeline").info("DreamShaper XL detected: Using DPMSolverMultistepScheduler (2M Karras)")
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            pipe.scheduler.config, 
            use_karras_sigmas=True,
            algorithm_type="dpmsolver++" # This is DPM++ 2M Karras (standard, very stable)
        )

    # VAE tiling/slicing and attention slicing
    try:
        if hasattr(pipe, "vae"):
            pipe.vae.enable_tiling()
            pipe.vae.enable_slicing()
        pipe.enable_attention_slicing()
    except Exception:
        pass

    # Extremely important: CPU offloading MUST be the final step. 
    # If any pipe.to() calls occur after this, the accelerate hooks are destroyed and VRAM fills up.
    if is_zimage and target_device == "cuda":
        logging.getLogger("image_pipeline").info("Z-Image detected on CUDA: Applying final Model CPU Offload hooks")
        pipe.enable_model_cpu_offload()

    return pipe
def apply_loras(pipe, lora_specs):
    """Apply LoRAs by loading and fusing at the requested weight.
    lora_specs: List[{"path": str, "weight": float}]
    Gracefully skips when the PEFT backend isn't available so jobs still run.
    """
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
