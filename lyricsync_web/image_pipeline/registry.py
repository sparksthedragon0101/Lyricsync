import json
from pathlib import Path
from typing import Dict, Any, List
import os

REGISTRY_PATH = Path("app/configs/models.json")
MODELS_DIR_PATH = Path("app/configs/models_dir.txt")
LORA_DIR_PATH = Path("app/configs/loras_dir.txt")


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
    p = Path(raw).expanduser()
    return p if p.exists() else None


def set_models_dir(path: str):
    MODELS_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODELS_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def get_lora_dir() -> Path | None:
    if not LORA_DIR_PATH.exists():
        return None
    raw = LORA_DIR_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


def set_lora_dir(path: str):
    LORA_DIR_PATH.parent.mkdir(parents=True, exist_ok=True)
    LORA_DIR_PATH.write_text(path.strip(), encoding="utf-8")


def list_models() -> List[Dict[str, Any]]:
    models = {m["id"]: m for m in _load_json(REGISTRY_PATH) if isinstance(m, dict) and m.get("id")}
    base_dir = get_models_dir()
    if base_dir:
        safetensors = list(base_dir.glob("**/*.safetensors"))
        for path in safetensors:
            rel_id = path.stem.replace(" ", "_")
            key = f"{rel_id}"
            if key not in models:
                models[key] = {
                    "id": key,
                    "type": "sdxl",
                    "path": str(path),
                    "tags": [path.parent.name],
                }
    return list(models.values())


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
    return [str(x) for x in p.glob("**/*.safetensors")]
