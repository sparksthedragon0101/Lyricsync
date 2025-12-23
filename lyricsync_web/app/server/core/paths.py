import os
from pathlib import Path

# app/ (./lyricsync_web/app)
APP_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = APP_DIR.parent

# Where we'll store user fonts
CUSTOM_FONT_DIR = APP_DIR / "static" / "fonts" / "custom"
CUSTOM_FONT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_FONT_EXTS = {".ttf", ".otf"}  # (expand if you truly support others)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB safety cap

ENV_FILE_PATH = ROOT_DIR / ".env"


def parse_env_file(path: Path) -> dict:
    env = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def _load_env_file(path: Path) -> None:
    env = parse_env_file(path)
    for k, v in env.items():
        os.environ.setdefault(k, v)


def write_env_file(updates: dict) -> Path:
    env = parse_env_file(ENV_FILE_PATH)
    env.update({k: v for k, v in updates.items() if v is not None})
    ENV_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_FILE_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    for k, v in updates.items():
        if v is not None:
            os.environ[k] = v
    return ENV_FILE_PATH


# Load .env (if present) before resolving roots from environment
_load_env_file(ENV_FILE_PATH)

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PROJECTS_ROOT = BASE_DIR.parent / "projects"  # previous default: lyricsync_web/projects
DEFAULT_FONTS_ROOT = BASE_DIR / "fonts"              # previous default: lyricsync_web/app/fonts


def _to_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


DATA_ROOT = _to_path(os.getenv("LYRICSYNC_DATA_ROOT"))
PROJECTS_ROOT = (
    _to_path(os.getenv("LYRICSYNC_PROJECTS_ROOT"))
    or (DATA_ROOT / "projects" if DATA_ROOT else DEFAULT_PROJECTS_ROOT)
)
GLOBAL_FONTS_DIR = (
    _to_path(os.getenv("LYRICSYNC_FONTS_ROOT"))
    or (DATA_ROOT / "fonts" if DATA_ROOT else DEFAULT_FONTS_ROOT)
)

PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
GLOBAL_FONTS_DIR.mkdir(parents=True, exist_ok=True)
