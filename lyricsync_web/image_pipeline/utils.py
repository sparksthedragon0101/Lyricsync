import logging
from pathlib import Path
from typing import Dict, Optional

from app.projects import Projects, ProjectNotFound
from PIL import PngImagePlugin

logger = logging.getLogger("image_pipeline")

PROJECTS_ROOT = (Path(__file__).resolve().parents[1] / "projects").resolve()
_PROJECTS = Projects(PROJECTS_ROOT)


def ensure_output_dir(slug: str, base_dir: Optional[Path | str] = None) -> Path:
    """
    Locate (and create, if needed) the images directory inside an existing project.
    """
    root = Path(base_dir).resolve() if base_dir else PROJECTS_ROOT
    manager = _PROJECTS if root == PROJECTS_ROOT else Projects(root)
    try:
        project = manager.get(slug)
    except ProjectNotFound as exc:
        raise RuntimeError(f"Project '{slug}' not found; create it before requesting images.") from exc

    out_dir = project.dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def save_png_with_meta(img, out_path: Path, metadata: Dict[str, str]):
    info = PngImagePlugin.PngInfo()
    for k, v in metadata.items():
        try:
            info.add_text(k, str(v))
        except Exception:
            pass
    img.save(out_path, pnginfo=info)
    logger.info("Saved image: %s", out_path)
