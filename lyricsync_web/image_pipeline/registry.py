import json
from pathlib import Path
from typing import Dict, Any, List
import os

BASE_CONFIG_DIR = Path(__file__).resolve().parent.parent / "app" / "configs"
REGISTRY_PATH = BASE_CONFIG_DIR / "models.json"
MODELS_DIR_PATH = BASE_CONFIG_DIR / "models_dir.txt"
LORA_DIR_PATH = BASE_CONFIG_DIR / "loras_dir.txt"
VAES_DIR_PATH = BASE_CONFIG_DIR / "vaes_dir.txt"
TEXT_ENCODERS_DIR_PATH = BASE_CONFIG_DIR / "text_encoders_dir.txt"


def _load_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: List[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_models_dir() -> Path | None:
    if not MODELS_DIR_PATH.exists():
        return None
    raw = MODELS_DIR_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def set_models_dir(path: str):
    MODELS_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODELS_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def get_lora_dir() -> Path | None:
    if not LORA_DIR_PATH.exists():
        return None
    raw = LORA_DIR_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def set_lora_dir(path: str):
    LORA_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    LORA_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def get_vae_dir() -> Path | None:
    if not VAES_DIR_PATH.exists():
        return None
    raw = VAES_DIR_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def set_vae_dir(path: str):
    VAES_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    VAES_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def get_text_encoder_dir() -> Path | None:
    if not TEXT_ENCODERS_DIR_PATH.exists():
        return None
    raw = TEXT_ENCODERS_DIR_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def set_text_encoder_dir(path: str):
    TEXT_ENCODERS_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEXT_ENCODERS_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def list_models() -> List[Dict[str, Any]]:
    data = _load_json(REGISTRY_PATH)
    if isinstance(data, dict) and "models" in data:
        data = data["models"]
    models = {m["id"]: m for m in data if isinstance(m, dict) and m.get("id")}
    base_dir = get_models_dir()
    if base_dir and base_dir.exists():
        model_files = list(base_dir.glob("**/*.safetensors")) + list(base_dir.glob("**/*.gguf"))
        for path in model_files:
            key = path.name
            if key not in models:
                models[key] = {
                    "id": key,
                    "type": "sdxl",
                    "path": str(path),
                    "tags": [path.parent.name],
                }
            else:
                # Ensure path is added to models pre-loaded from models.json
                models[key]["path"] = str(path)
                if "type" not in models[key]:
                    models[key]["type"] = "sdxl"
                new_tags = [path.parent.name]
                old_tags = models[key].get("tags", [])
                models[key]["tags"] = list(set(old_tags + new_tags))
    # Only return models that actually exist on disk (have a path)
    return [m for m in models.values() if "path" in m]


def get_model(model_id: str) -> Dict[str, Any] | None:
    for m in list_models():
        if m.get("id") == model_id:
            return m
    return None


def register_model(entry: Dict[str, Any]):
    data = list_models()
    # replace if id exists
    data = [d for d in data if d.get("id") != entry.get("id")]
    data.append(entry)
    _save_json(REGISTRY_PATH, data)


def list_loras(root: str | None = None) -> List[str]:
    if root:
        p = Path(root)
    else:
        p = get_lora_dir() or Path("app/models/loras")
    if not p.exists():
        return []
    return [str(x) for x in p.glob("**/*.safetensors")] + [str(x) for x in p.glob("**/*.gguf")]


def list_vaes(root: str | None = None) -> List[str]:
    if root:
        p = Path(root)
    else:
        p = get_vae_dir() or Path("app/models/vaes")
    if not p.exists():
        return []
    return [str(x) for x in p.glob("**/*.safetensors")] + [str(x) for x in p.glob("**/*.gguf")]


def list_text_encoders(root: str | None = None) -> List[str]:
    if root:
        p = Path(root)
    else:
        p = get_text_encoder_dir() or Path("app/models/text_encoders")
    if not p.exists():
        return []
    return [str(x) for x in p.glob("**/*.safetensors")] + [str(x) for x in p.glob("**/*.gguf")]
