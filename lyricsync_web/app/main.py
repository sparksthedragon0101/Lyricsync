
import os
import re
import json
import socket
import logging
import shlex
import shutil
from datetime import datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Optional, List, Any, Dict

import httpx
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Response, Body
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .server.routers import llm_router
from .server.routers import effects_router
from .server.routers import fonts_router
from .server.routers import themes_router
from .server.core.paths import (
    GLOBAL_FONTS_DIR,
    PROJECTS_ROOT,
    DATA_ROOT,
    DEFAULT_PROJECTS_ROOT,
    DEFAULT_FONTS_ROOT,
    ENV_FILE_PATH,
    write_env_file,
)
from .server.core.llm_client import OllamaProvider
from api.api_images import router as image_router
from api.api_models import router as models_router
from image_pipeline import registry as model_registry
from .projects import (
    Projects,
    ProjectNotFound,
    slugify,
    router as projects_router,
    refresh_story_slot_timings,
    get_audio_metadata,
    set_audio_metadata,
)
from .jobs import JobManager
from .srt_json import ensure_project_from_srt, load_project, save_project, export_srt, parse_srt
from image_pipeline.worker import start_worker

# ---------------------------------------------------------------------------
# Windows / Python 3.13 asyncio regression workaround:
# SelectorSocketTransport may schedule _write_send with an empty buffer, which
# raises AssertionError and tears down uvicorn when the reload worker restarts.
# Guard the call so we simply skip the spurious callback.
# ---------------------------------------------------------------------------
try:
    from asyncio import selector_events

    _orig_write_send = selector_events._SelectorSocketTransport._write_send
    _orig_write_sendmsg = getattr(selector_events._SelectorSocketTransport, "_write_sendmsg", None)

    def _drain_when_empty(self):
        """Mirror the tail of _write_send to keep transports consistent."""
        maybe_resume = getattr(self, "_maybe_resume_protocol", None)
        if callable(maybe_resume):
            try:
                maybe_resume()
            except Exception:
                pass

        loop = getattr(self, "_loop", None)
        sock_fd = getattr(self, "_sock_fd", None)
        if loop and sock_fd is not None:
            try:
                loop._remove_writer(sock_fd)
            except Exception:
                pass

        waiter = getattr(self, "_empty_waiter", None)
        if waiter is not None:
            try:
                waiter.set_result(None)
            except Exception:
                pass

        if getattr(self, "_closing", False):
            try:
                self._call_connection_lost(None)
            except Exception:
                pass
        elif getattr(self, "_eof", False):
            sock = getattr(self, "_sock", None)
            if sock:
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

    def _safe_write_send(self):  # type: ignore[override]
        buffer = getattr(self, "_buffer", None)
        if buffer:
            try:
                return _orig_write_send(self)
            except AssertionError:
                _drain_when_empty(self)
                return
        _drain_when_empty(self)

    selector_events._SelectorSocketTransport._write_send = _safe_write_send

    if _orig_write_sendmsg:

        def _safe_write_sendmsg(self):  # type: ignore[override]
            buffer = getattr(self, "_buffer", None)
            if buffer:
                try:
                    return _orig_write_sendmsg(self)
                except AssertionError:
                    _drain_when_empty(self)
                    return
            _drain_when_empty(self)

        selector_events._SelectorSocketTransport._write_sendmsg = _safe_write_sendmsg
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
# Prefer lyricsync.py from the project root directory.
LYRICSYNC_PATH = (ROOT_DIR.parent / "lyricsync.py").resolve()
if not LYRICSYNC_PATH.exists():
    LYRICSYNC_PATH = (ROOT_DIR / "lyricsync.py").resolve()
ARCHIVES_DIR = ROOT_DIR / "archives"
SECTION_TAGS = re.compile(
    r'^\s*\[(?:verse|chorus|bridge|pre-chorus|post-chorus|intro|outro|hook|refrain|break|solo)(?:[^\]]*)\]\s*$',
    flags=re.IGNORECASE
)
TIMECODES = re.compile(r'\[?\b\d{1,2}:\d{2}(?:\.\d{1,3})?\b\]?')  # [01:23], 1:23.456, 00:59
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
_ollama_client = OllamaProvider(base_url=OLLAMA_BASE_URL)
STYLE_HINTS = {
    "photorealistic": "photorealistic, detailed photography with lifelike lighting",
    "stylized": "stylized concept art with painterly brush strokes",
    "anime": "anime illustration with cel shading and bold lighting",
    "animated": "3d animated film still with soft lighting and expressive characters",
    "landscape": "epic wide environmental landscape shot with sweeping vistas",
}

SUB_STYLE_HINTS = {
    "generic": "stylized concept art, digital painting, highly detailed, evocative composition",
    "pixel_art": "Strictly pixel art, 16-bit retro game style, blocky, low-res aesthetic, sharp edges. Do NOT use brush strokes, blurring, or painterly terms.",
    "3d_render": "3D CGI render, Octane render, digital art, ray-traced, sharp focus, Unreal Engine 5 style, hyper-realistic materials.",
    "surrealist": "Surrealist art, dreamlike, Salvador Dali style, impossible geometry, melting forms, strange, ethereal, psychological horror elements."
}
PROJECTS_LOGGER = logging.getLogger("lyricsync.api.projects")
def clean_lyrics(raw: str) -> str:
    # 1) Decode entities / normalize line endings
    txt = html_unescape(raw or "").replace("\r\n", "\n").replace("\r", "\n")

    # 2) Strip timecodes like 00:12, [00:12.500]
    txt = TIMECODES.sub("", txt)

    # 3) Drop common section headers like [Chorus], [Verse 2], etc.
    lines = []
    for line in txt.split("\n"):
        if SECTION_TAGS.match(line):
            continue
        # light de-garbling / whitespace trim
        line = line.strip()
        # remove obvious site cruft lines
        if line.lower().startswith(("you might also like", "embed", "see lyrics")):
            continue
        lines.append(line)

    txt = "\n".join(lines)

    # 4) Normalize punctuation and whitespace
    subs = {
        "\u2018": "'", "\u2019": "'",  # curly single quotes
        "\u201c": '"', "\u201d": '"',  # curly double quotes
        "\u2013": "-",  "\u2014": "-", # en/em dashes
        "\u00a0": " ",                 # nbsp
    }
    for k, v in subs.items():
        txt = txt.replace(k, v)

    # 5) Collapse 3+ blank lines -> 1, trim edges
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()

    return txt

class StoragePathRequest(BaseModel):
    kind: str
    path: str

app = FastAPI(title="LyricSync Server")
app.mount("/static", StaticFiles(directory=str((BASE_DIR / "static").resolve())), name="static")
templates = Jinja2Templates(directory=str((BASE_DIR / "templates").resolve()))

projects = Projects(PROJECTS_ROOT)
jobs = JobManager(base_logs=PROJECTS_ROOT)
app.include_router(llm_router.router)
app.include_router(effects_router.router)
app.include_router(fonts_router.router, prefix="/api/fonts", tags=["fonts"])
app.include_router(themes_router.router, prefix="/api/themes", tags=["themes"])
app.include_router(image_router)
app.include_router(models_router)
app.include_router(projects_router)


@app.on_event("startup")
async def _startup():
    await start_worker()

def _should_redirect_to_project(request: Request) -> bool:
    """Determine whether a create request wants an HTML redirect."""
    redirect_param = request.query_params.get("redirect")
    if redirect_param is not None:
        return redirect_param.strip().lower() in {"1", "true", "yes", "on"}
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept


async def _create_project_response(
    request: Request,
    name: str,
    audio: UploadFile,
    cover: Optional[UploadFile],
    lyrics_txt: Optional[UploadFile],
    lyrics_srt: Optional[UploadFile],
):
    slug = slugify(name)
    PROJECTS_LOGGER.info(
        "Creating project '%s' via %s (accept=%s)",
        slug,
        request.url.path,
        request.headers.get("accept"),
    )
    p = projects.create(slug)
    audio_path = projects.save_upload(p, audio, "audio")
    cover_path = projects.save_upload(p, cover, "cover") if cover else None
    txt_path = projects.save_upload(p, lyrics_txt, "official_lyrics.txt") if lyrics_txt else None
    srt_path = projects.save_upload(p, lyrics_srt, "aligned.srt") if lyrics_srt else None

    if _should_redirect_to_project(request):
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    return JSONResponse(
        {
            "ok": True,
            "slug": slug,
            "paths": {
                "audio": str(audio_path) if audio_path else None,
                "cover": str(cover_path) if cover_path else None,
                "lyrics_txt": str(txt_path) if txt_path else None,
                "lyrics_srt": str(srt_path) if srt_path else None,
            },
        }
    )


@app.post("/api/projects/create")
async def api_create_project_legacy(request: Request):
    """Legacy compatibility endpoint that forwards to /api/projects."""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(
            status_code=415,
            detail="Project creation uploads must use multipart/form-data. POST to /api/projects.",
        )

    form = await request.form()
    name = form.get("name")
    audio = form.get("audio")
    if name is None or not isinstance(name, str):
        raise HTTPException(status_code=422, detail="Field 'name' is required.")
    if not isinstance(audio, UploadFile):
        raise HTTPException(status_code=422, detail="Field 'audio' file is required.")

    cover = form.get("cover")
    lyrics_txt = form.get("lyrics_txt")
    lyrics_srt = form.get("lyrics_srt")

    PROJECTS_LOGGER.warning("Legacy /api/projects/create was called; forwarding to /api/projects.")

    return await _create_project_response(
        request=request,
        name=name,
        audio=audio,
        cover=cover if isinstance(cover, UploadFile) else None,
        lyrics_txt=lyrics_txt if isinstance(lyrics_txt, UploadFile) else None,
        lyrics_srt=lyrics_srt if isinstance(lyrics_srt, UploadFile) else None,
    )

@app.post("/api/projects/{slug}/paste_lyrics")
async def api_paste_lyrics(slug: str, request: Request):
    """
    Accept raw lyrics from the UI, auto-clean them, and save as edited.txt.
    Optional JSON:
      { "text": "...", "also_official": true }  -> also writes official_lyrics.txt
    """
    p = projects.get(slug)  # same pattern as other endpoints
    data = await request.json()
    raw_text = (data or {}).get("text", "")
    also_official = bool((data or {}).get("also_official", False))

    cleaned = clean_lyrics(raw_text)
    out_txt = p.dir / "edited.txt"
    out_txt.write_text(cleaned, encoding="utf-8")

    # optional: also update official_lyrics.txt if user asks
    if also_official:
        (p.dir / "official_lyrics.txt").write_text(cleaned, encoding="utf-8")

    return {
        "ok": True,
        "saved": str(out_txt.name),
        "bytes": len(cleaned.encode("utf-8")),
        "also_official": also_official
    }

@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    storage_paths = {
        "projects_root": str(PROJECTS_ROOT),
        "fonts_root": str(GLOBAL_FONTS_DIR),
        "data_root": str(DATA_ROOT) if DATA_ROOT else None,
        "defaults": {
            "projects": str(DEFAULT_PROJECTS_ROOT),
            "fonts": str(DEFAULT_FONTS_ROOT),
        },
        "env": {
            "LYRICSYNC_DATA_ROOT": os.getenv("LYRICSYNC_DATA_ROOT"),
            "LYRICSYNC_PROJECTS_ROOT": os.getenv("LYRICSYNC_PROJECTS_ROOT"),
            "LYRICSYNC_FONTS_ROOT": os.getenv("LYRICSYNC_FONTS_ROOT"),
        },
        "env_file": str(ENV_FILE_PATH),
    }
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "projects": projects.list_projects(), "storage_paths": storage_paths},
    )
    
@app.get("/api/fonts")
def api_list_global_fonts(debug: bool = False):
    exts = {".ttf", ".otf"}  # add ".ttc", ".woff", ".woff2" if you truly want them listed
    names = set()

    exists = GLOBAL_FONTS_DIR.exists()
    scanned_path = str(GLOBAL_FONTS_DIR)

    if exists:
        for f in GLOBAL_FONTS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in exts:
                names.add(f.name)

    fonts = sorted(names)  # stable ordering

    if debug:
        return {
            "fonts": fonts,
            "path": scanned_path,
            "exists": exists,
            "count": len(fonts)
        }
    return {"fonts": fonts}


@app.post("/api/storage_paths")
def api_set_storage_paths(req: StoragePathRequest):
    kind = (req.kind or "").strip().lower()
    path_value = (req.path or "").strip()
    key_map = {
        "projects": "LYRICSYNC_PROJECTS_ROOT",
        "fonts": "LYRICSYNC_FONTS_ROOT",
    }
    env_key = key_map.get(kind)
    if not env_key:
        raise HTTPException(400, detail="kind must be 'projects' or 'fonts'")
    if not path_value:
        raise HTTPException(422, detail="Path is required.")

    target = Path(path_value).expanduser()
    # After home expansion, path must be absolute to avoid writing to unexpected places.
    if not target.is_absolute():
        raise HTTPException(400, detail=f"Path must be absolute. Got: '{path_value}'")

    # The path must already exist and be a directory.
    if not target.is_dir():
        raise HTTPException(400, detail=f"Path is not an existing directory: '{target}'")
    
    env_path = write_env_file({env_key: str(target)})
    return {
        "ok": True,
        "env_file": str(env_path),
        "applied": {kind: str(target)},
        "restart_required": True,
    }

    
@app.get("/projects/{slug}", response_class=HTMLResponse)
def project_page(request: Request, slug: str):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    meta = projects.meta(slug)
    return templates.TemplateResponse("project.html", {"request": request, "p": p, "meta": meta, "has_lyricsync": LYRICSYNC_PATH.exists()})

@app.get("/projects/{slug}/edit", response_class=HTMLResponse)
def editor_page(request: Request, slug: str):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    return templates.TemplateResponse("editor.html", {"request": request, "p": p})

@app.get("/api/projects")
def api_list_projects():
    return {"projects": projects.list_projects()}

@app.post("/api/projects")
async def api_create_project(
    request: Request,
    name: str = Form(...),
    audio: UploadFile = File(...),
    cover: Optional[UploadFile] = File(None),
    lyrics_txt: Optional[UploadFile] = File(None),
    lyrics_srt: Optional[UploadFile] = File(None),
):
    return await _create_project_response(
        request=request,
        name=name,
        audio=audio,
        cover=cover,
        lyrics_txt=lyrics_txt,
        lyrics_srt=lyrics_srt,
    )

@app.get("/api/projects/{slug}")
def api_project(slug: str):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    return projects.meta(slug)

@app.get("/api/projects/{slug}/audio")
def api_audio(slug: str):
    p = projects.get(slug)
    audio_path = p.audio  # may be a dir or a file depending on Projects implementation

    def pick_from_dir(d: Path):
        files = [f for f in d.iterdir() if f.is_file()]
        if not files:
            return None
        mp3s = [f for f in files if f.suffix.lower() == ".mp3"]
        return mp3s[0] if mp3s else max(files, key=lambda f: f.stat().st_size)

    chosen = None
    if audio_path.exists():
        if audio_path.is_dir():
            chosen = pick_from_dir(audio_path)
        elif audio_path.is_file():
            # still allow an override: if there's an audio/ folder, prefer a file inside it
            audio_dir = p.dir / "audio"
            if audio_dir.exists() and audio_dir.is_dir():
                chosen = pick_from_dir(audio_dir) or audio_path
            else:
                chosen = audio_path

    if not chosen or not chosen.exists() or chosen.stat().st_size == 0:
        raise HTTPException(404, "No usable audio file found. Put a media file in the project's audio folder.")

    media_type = "audio/mpeg" if chosen.suffix.lower() == ".mp3" else "application/octet-stream"
    return FileResponse(str(chosen), media_type=media_type)


@app.get("/api/projects/{slug}/download/{path:path}")
def api_download(slug: str, path: str):
    p = projects.get(slug)
    root = p.dir.resolve()
    target = (p.dir / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not target.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(str(target))
    
@app.head("/api/projects/{slug}/download/{path:path}")
def head_download(slug: str, path: str):
    p = projects.get(slug)
    root = p.dir.resolve()
    target = (p.dir / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")

    try:
        size = target.stat().st_size
    except Exception:
        size = 0

    # Minimal, safe HEAD response. Provide an explicit empty body.
    return Response(
        content=b"",                     # ✅ must be bytes/str, not None
        status_code=200,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(size),                 # safe even when 0
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            # (skip Accept-Ranges here; HEAD doesn’t need it)
        },
    )


def _list_project_images(p, limit: int | None = None) -> List[Path]:
    images_dir = p.dir / "images"
    if not images_dir.exists():
        return []
    files = [
        f for f in images_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS
    ]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if limit is not None:
        files = files[:limit]
    return files


def _selected_images_path(p) -> Path:
    return p.dir / "images" / ".selection.json"


def _get_selected_images(p) -> List[Path]:
    sel_file = _selected_images_path(p)
    if not sel_file.exists():
        return []
    try:
        data = json.loads(sel_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: List[Path] = []
    for rel in data if isinstance(data, list) else []:
        rel = str(rel)
        if not rel:
            continue
        cand = (p.dir / rel).resolve()
        try:
            cand.relative_to(p.dir)
        except ValueError:
            continue
        if cand.exists() and cand.is_file():
            out.append(cand)
    return out


def _set_selected_images(p, rel_paths: List[str]) -> List[str]:
    sel_file = _selected_images_path(p)
    sel_file.parent.mkdir(parents=True, exist_ok=True)
    valid: List[str] = []
    for rel in rel_paths:
        rel = str(rel).strip()
        if not rel:
            continue
        cand = (p.dir / rel).resolve()
        try:
            cand.relative_to(p.dir)
        except ValueError:
            continue
        if not cand.exists() or cand.suffix.lower() not in IMAGE_EXTS:
            continue
        valid.append(rel)
    sel_file.write_text(json.dumps(valid, indent=2), encoding="utf-8")
    return valid


class ImagePromptRequest(BaseModel):
    model: str
    temperature: float = 0.35
    max_tokens: int = 512
    style: Optional[str] = None
    sub_style: Optional[str] = None
    no_humans: bool = False


class ModelDirRequest(BaseModel):
    path: str


def _read_lyrics_for_prompt(p) -> str:
    if not p.official_txt.exists():
        raise HTTPException(400, "official_lyrics.txt is missing for this project.")
    try:
        txt = p.official_txt.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        txt = p.official_txt.read_text(encoding="latin-1", errors="ignore")
    lyrics = txt.strip()
    if not lyrics:
        raise HTTPException(400, "official_lyrics.txt is empty.")
    if len(lyrics) > 5000:
        lyrics = lyrics[:5000] + "\n..."
    return lyrics


def _parse_prompt_response(payload: str) -> tuple[str, str]:
    positive = ""
    negative = ""
    try:
        data = json.loads(payload)
        positive = str(data.get("positive", "")).strip()
        negative = str(data.get("negative", "")).strip()
    except Exception:
        pass

    if not positive:
        # fallback: look for "Positive:" style labels
        lower = payload.lower()
        if "positive" in lower:
            idx = lower.find("positive")
            segment = payload[idx:]
            parts = segment.split("\n", 1)
            if parts:
                positive = parts[-1].strip()
        if not positive:
            positive = payload.strip()

    if not negative:
        for marker in ("negative:", "neg:", "negative prompt:"):
            loc = payload.lower().find(marker)
            if loc != -1:
                neg = payload[loc + len(marker):].split("\n", 1)[0]
                negative = neg.strip()
                break
    return positive.strip(), negative.strip()

def _normalize_story_slots(raw: Any) -> List[Dict[str, Any]]:
    enumerated: List[tuple[int, Dict[str, Any]]] = []
    if not isinstance(raw, list):
        return []
    def _to_float(value):
        try:
            return float(value)
        except Exception:
            return None
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        prompt = str(entry.get("prompt", "") or "").strip()
        if not prompt:
            continue
        def _to_float(value):
            try:
                return float(value)
            except Exception:
                return None
        start = _to_float(entry.get("start"))
        end = _to_float(entry.get("end"))
        image_path = entry.get("image_path")
        image_path = str(image_path).strip() if isinstance(image_path, str) else None
        if image_path == "":
            image_path = None
        enumerated.append((idx, {
            "prompt": prompt,
            "start": start,
            "end": end,
            "image_path": image_path
        }))

    def _sort_key(item: tuple[int, Dict[str, Any]]):
        order, slot = item
        start = slot.get("start")
        sortable = start if isinstance(start, (int, float)) else float("inf")
        return (sortable, order)

    enumerated.sort(key=_sort_key)
    return [slot for _, slot in enumerated]


@app.get("/api/ollama/models")
def api_ollama_models():
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = []
        for item in data.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(name)
    except Exception as exc:
        logging.warning("Failed to query Ollama models: %s", exc)
        models = []
    return {"models": models}


@app.get("/api/models/directory")
def api_models_directory():
    path = model_registry.get_models_dir()
    return {"path": str(path) if path else None}


@app.post("/api/models/directory")
def api_set_models_directory(req: ModelDirRequest):
    target = Path(req.path).expanduser()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "Directory does not exist")
    model_registry.set_models_dir(str(target))
    return {"ok": True, "path": str(target)}


@app.post("/api/projects/{slug}/image_prompt")
def api_image_prompt(slug: str, req: ImagePromptRequest):
    p = projects.get(slug)
    lyrics = _read_lyrics_for_prompt(p)
    style_key = (req.style or "photorealistic").strip().lower()
    style_instruction = STYLE_HINTS.get(style_key, style_key or "photorealistic")
    
    # Override with specific sub-style if provided
    if req.sub_style:
        sub_key = req.sub_style.strip().lower()
        if sub_key in SUB_STYLE_HINTS:
            style_instruction = SUB_STYLE_HINTS[sub_key]
    
    enforce_no_humans = bool(req.no_humans and style_key == "photorealistic")
    constraints = []
    if enforce_no_humans:
        constraints.append(
            "Absolutely no humans, people, faces, hands, or body parts in frame. "
            "Lean into objects, landscapes, lighting, stage design, or vibes only."
        )
    messages = [
        {
            "role": "system",
            "content": (
                "You analyze song lyrics and craft Stable Diffusion prompts. "
                "Respond strictly as compact JSON: {\"positive\": \"...\", \"negative\": \"...\"}. "
                "Positive prompt should be cinematic and vivid. Negative prompt should list things to avoid."
            ),
        },
    ]
    if enforce_no_humans:
        messages.append(
            {
                "role": "system",
                "content": (
                    "A recent request insisted on absolutely no humans in the generated art. "
                    "Do not describe, mention, or include people, faces, bodies, or limbs in the scene."
                ),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": (
                "Lyrics:\n"
                f"{lyrics}\n\n"
                f"Desired style: {style_instruction}.\n"
                "Create positive and negative prompts tailored for cover art generation."
                + ("\nConstraints: " + " ".join(constraints) if constraints else "")
            ),
        },
    )
    
    try:
        resp = _ollama_client.chat(messages=messages, model=req.model, temperature=req.temperature, timeout=180)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc

    positive, negative = _parse_prompt_response(resp.text or "")
    if enforce_no_humans:
        human_block = "no people, no humans, no faces, no body parts, no hands"
        neg_lower = negative.lower()
        if not any(term in neg_lower for term in ("no humans", "no people", "no person", "faces", "body parts", "hands")):
            negative = (negative + ", " if negative else "") + human_block

    return {
        "ok": True,
        "positive": positive,
        "negative": negative,
        "raw": resp.text,
        "model": resp.model,
        "latency_ms": resp.latency_ms,
    }


class ImagePathRequest(BaseModel):
    path: str


@app.get("/api/projects/{slug}/images")
def api_project_images(slug: str):
    p = projects.get(slug)
    files = _list_project_images(p)
    rels = [f.relative_to(p.dir).as_posix() for f in files]
    selected = [f.relative_to(p.dir).as_posix() for f in _get_selected_images(p)]
    return {"images": rels, "selected": selected}


class ImageSelectionRequest(BaseModel):
    paths: List[str]


@app.post("/api/projects/{slug}/images/selection")
def api_set_image_selection(slug: str, req: ImageSelectionRequest):
    p = projects.get(slug)
    saved = _set_selected_images(p, req.paths or [])
    return {"ok": True, "selected": saved}


class MetadataRequest(BaseModel):
    title: str


@app.get("/api/projects/{slug}/metadata")
def api_get_metadata(slug: str):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    meta = get_audio_metadata(p)
    return {"ok": True, "metadata": meta}


@app.post("/api/projects/{slug}/metadata")
def api_set_metadata(slug: str, req: MetadataRequest):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    
    try:
        set_audio_metadata(p, req.title)
    except Exception as e:
        raise HTTPException(500, detail=str(e))

    return {"ok": True}


@app.post("/api/projects/{slug}/cover/from_image")
def api_cover_from_image(slug: str, req: ImagePathRequest):
    p = projects.get(slug)
    rel = Path(req.path)
    target = (p.dir / rel).resolve()
    try:
        target.relative_to(p.dir)
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    if not target.exists():
        raise HTTPException(404, "Image not found")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(400, "File is not an image")
    p.cover.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target, p.cover)
    return {"ok": True, "cover": str(p.cover)}


@app.delete("/api/projects/{slug}/images")
def api_delete_project_image(slug: str, req: ImagePathRequest):
    p = projects.get(slug)
    rel = Path(req.path)
    target = (p.dir / rel).resolve()
    try:
        target.relative_to(p.dir)
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    if not target.exists():
        raise HTTPException(404, "Image not found")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(400, "File is not an image")
    try:
        target.unlink()
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete image: {exc}") from exc
    return {"ok": True}


@app.delete("/api/projects/{slug}/images/all")
def api_delete_all_project_images(slug: str):
    p = projects.get(slug)
    images = _list_project_images(p)
    deleted = 0
    for img_path in images:
        try:
            img_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
        except TypeError:
            if img_path.exists():
                img_path.unlink()
        except Exception as exc:
            raise HTTPException(500, f"Failed to delete {img_path.name}: {exc}") from exc
        else:
            deleted += 1
    sel_path = _selected_images_path(p)
    try:
        sel_path.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "deleted": deleted}


@app.post("/api/projects/{slug}/images/upload")
async def api_upload_images(slug: str, files: List[UploadFile] = File(...)):
    """
    Allow remote users to batch-upload multiple images to a project.
    Saves into projects/<slug>/images and returns relative paths of saved files.
    """
    p = projects.get(slug)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    images_dir = p.dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    saved: List[str] = []
    errors: List[str] = []

    for up in files:
        filename = (up.filename or "").strip()
        if not filename:
            errors.append("Unnamed file skipped")
            continue
        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            errors.append(f"{filename}: unsupported type")
            continue
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-._") or "image"
        candidate = images_dir / f"{stem}{ext}"
        counter = 1
        while candidate.exists():
            candidate = images_dir / f"{stem}-{counter}{ext}"
            counter += 1
        try:
            data = await up.read()
            candidate.write_bytes(data)
            saved.append(candidate.relative_to(p.dir).as_posix())
        except Exception as exc:
            errors.append(f"{filename}: {exc}")

    if not saved and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    return {"ok": True, "saved": saved, "errors": errors}


@app.post("/api/projects/{slug}/archive")
def api_archive_project(slug: str):
    """Zip a project and remove it from the active projects directory."""
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")

    if not p.dir.exists():
        raise HTTPException(404, "Project folder missing")

    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    archive_base = ARCHIVES_DIR / slug
    archive_path = shutil.make_archive(
        base_name=str(archive_base),
        format="zip",
        root_dir=p.dir.parent,
        base_dir=p.dir.name,
    )
    try:
        shutil.rmtree(p.dir)
    except Exception as exc:
        raise HTTPException(500, f"Archived but failed to remove project: {exc}") from exc

    archive_name = Path(archive_path).name
    return {
        "ok": True,
        "archive": archive_name,
        "archive_url": f"/api/archives/{archive_name}",
    }


@app.get("/api/archives/{filename}")
def api_get_archive(filename: str):
    """Serve a previously archived project zip."""
    safe_name = Path(filename).name  # prevent path traversal
    target = (ARCHIVES_DIR / safe_name).resolve()
    try:
        target.relative_to(ARCHIVES_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid archive name")
    if not target.exists():
        raise HTTPException(404, "Archive not found")
    return FileResponse(str(target), media_type="application/zip", filename=target.name)


@app.get("/api/archives")
def api_list_archives():
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    archives = []
    for entry in sorted(ARCHIVES_DIR.glob("*.zip")):
        if entry.is_file():
            archives.append(entry.name)
    return {"archives": archives}


@app.post("/api/archives/{filename}/restore")
def api_restore_archive(filename: str):
    """Restore an archived project back into the active projects directory."""
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    archive_path = (ARCHIVES_DIR / safe_name).resolve()
    try:
        archive_path.relative_to(ARCHIVES_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid archive name")
    if not archive_path.exists():
        raise HTTPException(404, "Archive not found")

    slug = archive_path.stem
    target_dir = PROJECTS_ROOT / slug
    if target_dir.exists():
        raise HTTPException(409, "A project with this slug already exists. Delete or rename it before restoring.")

    try:
        shutil.unpack_archive(str(archive_path), extract_dir=str(PROJECTS_ROOT))
    except Exception as exc:
        raise HTTPException(500, f"Failed to restore archive: {exc}") from exc

    return {"ok": True, "slug": slug, "path": str(target_dir)}


@app.delete("/api/archives/{filename}")
def api_delete_archive(filename: str):
    """Delete an archived zip."""
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = (ARCHIVES_DIR / safe_name).resolve()
    try:
        target.relative_to(ARCHIVES_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid archive name")
    if not target.exists():
        raise HTTPException(404, "Archive not found")
    try:
        target.unlink()
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete archive: {exc}") from exc
    return {"ok": True, "deleted": safe_name}

@app.post("/api/projects/{slug}/cover")
async def api_upload_cover(slug: str, cover: UploadFile = File(...)):
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")

    if not cover or not cover.filename:
        raise HTTPException(400, "No file uploaded")

    ext = os.path.splitext(cover.filename)[1].lower()
    if ext and ext not in IMAGE_EXTS:
        raise HTTPException(400, "Unsupported image type. Use PNG, JPG, WEBP, or BMP.")

    saved = projects.save_upload(p, cover, "cover")
    return {"ok": True, "path": str(saved)}

@app.get("/api/projects/{slug}/timing")
def api_get_timing(slug: str):
    p = projects.get(slug)
    json_path = p.dir / "timing.json"
    if json_path.exists():
        data = load_project(json_path)
        segments = data.get("segments") if isinstance(data, dict) else []

        # If a newer aligned.srt exists with more segments than timing.json, refresh from it.
        aligned = p.aligned_srt
        try:
            aligned_mtime = aligned.stat().st_mtime if aligned.exists() else None
            timing_mtime = json_path.stat().st_mtime
        except Exception:
            aligned_mtime = None
            timing_mtime = None
        if aligned.exists() and aligned_mtime and timing_mtime and aligned_mtime > timing_mtime:
            try:
                aligned_count = len(parse_srt(aligned))
            except Exception:
                aligned_count = 0
            if aligned_count and len(segments) < aligned_count:
                ensure_project_from_srt(aligned, json_path, p.audio)
                data = load_project(json_path)
        return data
    if p.aligned_srt.exists():
        ensure_project_from_srt(p.aligned_srt, json_path, p.audio)
        return load_project(json_path)
    data = {"version": 1, "audio_path": str(p.audio), "title": p.dir.name, "fps": 30, "level": "line", "segments": []}
    save_project(json_path, data)
    return data

@app.post("/api/projects/{slug}/timing")
async def api_save_timing(slug: str, request: Request):
    p = projects.get(slug)
    data = await request.json()
    json_path = p.dir / "timing.json"
    save_project(json_path, data)
    try:
        export_srt(data, p.aligned_srt)
        export_srt(data, p.dir / "edited.srt")
    except Exception:
        pass
    try:
        segments = data.get("segments") if isinstance(data, dict) else None
        refresh_story_slot_timings(p, segments)
    except Exception:
        pass
    return {"ok": True}

@app.post("/api/projects/{slug}/export_srt")
def api_export_srt(slug: str):
    p = projects.get(slug)
    json_path = p.dir / "timing.json"
    if not json_path.exists():
        raise HTTPException(400, "No timing.json")
    out_srt = p.aligned_srt
    data = load_project(json_path)
    export_srt(data, out_srt)
    return {"ok": True, "path": str(out_srt)}

@app.post("/api/projects/{slug}/import_srt")
def api_import_srt(slug: str):
    p = projects.get(slug)
    srt = p.aligned_srt if p.aligned_srt.exists() else (p.dir / "edited.srt")
    if not srt.exists():
        raise HTTPException(404, "No SRT to import")
    json_path = p.dir / "timing.json"
    ensure_project_from_srt(srt, json_path, p.audio)
    try:
        data = load_project(json_path)
        refresh_story_slot_timings(p, data.get("segments") if isinstance(data, dict) else None)
    except Exception:
        pass
    return {"ok": True}

# --- Align endpoint (robust, Windows-friendly, single-flight) ---
from fastapi import HTTPException
import sys, os, shlex, subprocess, threading
from datetime import datetime

@app.post("/api/projects/{slug}/align")
async def api_align(
    slug: str,
    request: Request,
    model_size: str = "large-v2",
    device: str = "auto",
    language: str = "auto",
    vad: str = "auto",
    align_mode: str = "words",
    compute_type: str = "float16",
    separate: str = "none",
    prep_audio: str = "auto",
):
    p = projects.get(slug)
    if not LYRICSYNC_PATH.exists():
        raise HTTPException(status_code=500, detail=f"lyricsync.py not found at {LYRICSYNC_PATH}")

    body = {}
    try:
        if request.headers.get("content-type", "").lower().startswith("application/json"):
            incoming = await request.json()
            if isinstance(incoming, dict):
                body = incoming
    except Exception:
        body = {}

    def _cfg(key: str, default):
        if key not in body:
            return default
        val = body[key]
        if isinstance(default, bool):
            return bool(val)
        if isinstance(default, (int, float)):
            try:
                return type(default)(val)
            except Exception:
                return default
        return str(val).strip() or default

    model_size = _cfg("model_size", model_size)
    device = _cfg("device", device)
    language = _cfg("language", language)
    compute_type = _cfg("compute_type", compute_type)
    vad = _cfg("vad", vad)
    prep_audio = _cfg("prep_audio", prep_audio)
    enable_word_highlight = bool(body.get("enable_word_highlight") or body.get("word_highlight"))

    # ---------- pick an actual audio file ----------
    audio_path = p.audio
    chosen_audio = None

    def _pick_from_dir(d):
        files = [f for f in d.iterdir() if f.is_file()]
        if not files:
            return None
        mp3s = [f for f in files if f.suffix.lower() == ".mp3"]
        return mp3s[0] if mp3s else max(files, key=lambda f: f.stat().st_size)

    if audio_path.exists():
        if audio_path.is_dir():
            chosen_audio = _pick_from_dir(audio_path)
        elif audio_path.is_file():
            chosen_audio = audio_path
            maybe_dir = p.dir / "audio"
            if maybe_dir.exists() and maybe_dir.is_dir():
                cand = _pick_from_dir(maybe_dir)
                if cand:
                    chosen_audio = cand

    if not chosen_audio or not chosen_audio.exists() or chosen_audio.stat().st_size == 0:
        raise HTTPException(400, "No usable audio file found. Put a media file in the project's audio folder.")

    # ---------- prep paths & logs ----------
    p.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = p.logs_dir / "align.log"

    # ---------- lyrics: prefer edited.txt → official_lyrics.txt ----------
    lyrics_path = p.dir / "edited.txt"
    if not lyrics_path.exists() or lyrics_path.stat().st_size < 10:
        # Some Project classes expose p.official_txt; fall back to that, else raw file in dir
        if hasattr(p, "official_txt") and p.official_txt and p.official_txt.exists():
            lyrics_path = p.official_txt
        else:
            lyrics_path = p.dir / "official_lyrics.txt"
    if not lyrics_path.exists() or lyrics_path.stat().st_size < 10:
        raise HTTPException(status_code=400, detail="Lyrics missing or empty (need edited.txt or official_lyrics.txt)")

    # ---------- outputs ----------
    out_srt         = p.dir / "aligned.srt"
    out_srt_shifted = p.dir / "edited.srt"

    # ---------- args for lyricsync.py (mirror BAT behavior) ----------
    args = [
        "--audio", str(chosen_audio),
        "--lyrics", str(lyrics_path),
        "--model-size", model_size,
        "--device", device,
        "--language", language,
        "--vad", vad,
        "--compute-type", compute_type,
        "--separate", "vocals",
        "--demucs-model", "htdemucs",
        "--align-mode", "words",
        "--out-srt", str(out_srt),
        "--out-srt-shifted", str(out_srt_shifted),
        "--shift-seconds", "3",
        "--prep-audio", prep_audio,
        "--keep-prep",
    ]
    if enable_word_highlight:
        args.append("--enable-word-highlight")
    full_cmd = [sys.executable, str(LYRICSYNC_PATH), *args]

    # ---------- log header ----------
    with open(log_file, "a", encoding="utf-8") as lf:
        lf.write("\n=== Align launch at {} ===\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        lf.write(f"[LyricSync] Project: {slug}\n")
        lf.write(f"[LyricSync] Audio:   {chosen_audio}\n")
        lf.write(f"[LyricSync] Lyrics:  {lyrics_path}\n")
        lf.write(f"[LyricSync] Out SRT: {out_srt}\n")
        lf.write(f"[LyricSync] Out SRT+: {out_srt_shifted} (+3s)\n")
        if enable_word_highlight:
            lf.write("[LyricSync] Word highlight flag: enabled (not yet wired in renderer)\n")
        lf.write("[LyricSync] Command:\n  " + " ".join(shlex.quote(str(x)) for x in full_cmd) + "\n")
        lf.flush()

    # ---------- background runner + fallback if empty SRT ----------
    def _run_and_maybe_fallback(cmd):
        with open(log_file, "ab", buffering=0) as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(p.dir),
                text=True,
            )
            for line in proc.stdout:
                lf.write(line.encode("utf-8", errors="ignore"))
            ret = proc.wait()

        # if success but SRT is empty → fallback once with segments
        try:
            size = out_srt.stat().st_size if out_srt.exists() else 0
        except Exception:
            size = 0

        if (ret == 0) and (size < 10):
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write("[LyricSync] Word alignment coverage too low; falling back to segment alignment.\n")
                lf.flush()

            cmd_fallback = list(cmd)
            for i, v in enumerate(cmd_fallback):
                if v == "--align-mode" and i + 1 < len(cmd_fallback):
                    cmd_fallback[i + 1] = "segments"
                    break

            with open(log_file, "ab", buffering=0) as lf:
                proc2 = subprocess.Popen(
                    cmd_fallback,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(p.dir),
                    text=True,
                )
                for line in proc2.stdout:
                    lf.write(line.encode("utf-8", errors="ignore"))
                proc2.wait()

        with open(log_file, "a", encoding="utf-8") as lf:
            final = out_srt.stat().st_size if out_srt.exists() else 0
            lf.write(f"[Complete] SRT: {out_srt} ({final} bytes)\n")

    threading.Thread(target=_run_and_maybe_fallback, args=(full_cmd,), daemon=True).start()
    return {"ok": True, "pid": os.getpid()}

@app.post("/api/projects/{slug}/render")
async def api_render(
    request: Request,
    slug: str,
    style: str = "burn-srt",
    text_theme: str = "default",
    font: str = "Arial",
    font_size: int = 20,
    outline: int = 2,
    ass_align: int = 2,
    margin_v: int = 20,
    force_res: str = "1920:1080",
    srt_name: str = "edited.srt",
    no_burn: bool = False,
    title_from_mp3: bool = False,
    show_title: bool = False,
    use_mp3_title: bool = False,
    show_end_card: bool = False,
    font_file_name: str | None = None,
    end_card_text: Optional[str] = None,
    end_card_seconds: float = 5.0,
    effect: str = "none",
    effect_strength: float = 0.08,
    effect_cycle: float = 12.0,
    effect_zoom: float | None = None,
    effect_pan: float | None = None,
    fps: int = 30,

    ):
    font_color            = "#FFFFFF"
    outline_color         = "#000000"
    endcard_color         = "#FFFFFF"
    endcard_border_color  = "#000000"
    image_clip_seconds: float | None = None
    image_fade_seconds: float | None = None
    image_playback = "story"
    story_slots: List[Dict[str, Any]] = []
    # ---- Merge JSON body (so the UI checkboxes actually take effect) ----
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            data = await request.json()
            if isinstance(data, dict):
                use_mp3_title  = bool(data.get("use_mp3_title", use_mp3_title))
                title_from_mp3 = bool(data.get("title_from_mp3", title_from_mp3))
                # alias legacy -> canonical
                if "title_from_mp3" not in data and "use_mp3_title" in data:
                   title_from_mp3 = use_mp3_title
                show_end_card  = bool(data.get("show_end_card", show_end_card))
                style          = data.get("style", style)
                text_theme     = data.get("text_theme", text_theme)
                font           = data.get("font", font)
                font_size      = int(data.get("font_size", font_size))
                outline        = int(data.get("outline", outline))
                ass_align      = int(data.get("ass_align", ass_align))
                margin_v       = int(data.get("margin_v", margin_v))
                force_res      = data.get("force_res", force_res)
                srt_name       = data.get("srt_name", srt_name)
                no_burn        = bool(data.get("no_burn", no_burn))
                font_file_name = data.get("font_file_name", font_file_name)
                end_card_text  = data.get("end_card_text", end_card_text)
                end_card_seconds = float(data.get("end_card_seconds", end_card_seconds))
                font_color           = data.get("font_color", "#FFFFFF")
                outline_color        = data.get("outline_color", "#000000")
                endcard_color        = data.get("endcard_color", "#FFFFFF")
                endcard_border_color = data.get("endcard_border_color", "#000000")
                eff = data.get("effects") or {}
                effect          = eff.get("effect", effect)
                effect_strength = float(eff.get("strength", effect_strength))
                effect_cycle    = float(eff.get("cycle", effect_cycle))
                effect_zoom     = eff.get("zoom", effect_zoom)
                effect_pan      = eff.get("pan", effect_pan)
                fps             = int(eff.get("fps", fps))
                image_opts = data.get("image") or {}
                try:
                    clip_val = image_opts.get("clip_seconds")
                    if clip_val not in (None, ""):
                        clip_val = float(clip_val)
                        image_clip_seconds = clip_val if clip_val > 0 else None
                except Exception:
                    pass
                try:
                    fade_val = image_opts.get("fade_seconds")
                    if fade_val not in (None, ""):
                        fade_val = float(fade_val)
                        image_fade_seconds = fade_val if fade_val > 0 else None
                except Exception:
                    pass
                playback_val = image_opts.get("playback")
                if playback_val:
                    image_playback = str(playback_val).strip().lower() or image_playback
                story_slots = _normalize_story_slots(image_opts.get("story_slots"))
                if image_playback == "story":
                    image_clip_seconds = None
                
    except Exception:
        pass
    # --------------------------------------------------------------------
    print("[API] effect:", effect, effect_strength, effect_cycle, effect_zoom, effect_pan, fps)

    # 1) Get project + sanity checks
    p = projects.get(slug)
    if not LYRICSYNC_PATH.exists():
        raise HTTPException(500, f"lyricsync.py not found at {LYRICSYNC_PATH}")
    if not p.audio.exists():
        raise HTTPException(400, "Audio missing")

    if srt_name == "edited.srt" and p.aligned_srt.exists():
        srt_name = "aligned.srt"

    srt_path = p.dir / srt_name
    if not srt_path.exists():
        raise HTTPException(400, f"SRT missing: {srt_path.name}")

    preview_out = p.dir / "preview.mp4"

    # 2) Resolve a concrete audio FILE (prefer MP3; allow extensionless -> largest)
    audio_path = p.audio
    if audio_path.is_dir():
        files = [f for f in audio_path.iterdir() if f.is_file()]
        if not files:
            raise HTTPException(400, "No audio files found in the project's audio folder.")
        mp3s = [f for f in files if f.suffix.lower() == ".mp3"]
        audio_path = mp3s[0] if mp3s else max(files, key=lambda f: f.stat().st_size)

    # 3) Effective style (don’t auto-force credits; let UI style do that)
    eff_style = style

    # 4) Build base command
    title_enabled = bool(show_title or title_from_mp3 or use_mp3_title)
    title_seconds = 3.0 if title_enabled else 0.0

    cmd = [
        str(LYRICSYNC_PATH), "--audio", str(audio_path),
        "--lyrics", str(srt_path),
        "--srt-only",
        "--out-srt", str(srt_path),
        "--burn-subs", str(srt_path),
        "--preview-out", str(preview_out),
        "--style", eff_style,
        "--text-theme", text_theme,
        "--font", font,
        "--font-size", str(font_size),
        "--outline", str(outline),
        "--align", str(ass_align),
        "--margin-v", str(margin_v),
        "--force-res", force_res,
        
    ]
    
    cmd += [
        "--font-color", str(font_color),
        "--outline-color", str(outline_color),
        "--thanks-color", str(endcard_color),
        "--thanks-border-color", str(endcard_border_color),
        "--effect", str(effect),
        "--effect-strength", str(effect_strength),
        "--effect-cycle", str(effect_cycle),
        "--fps", str(fps),
    ]
    if effect_zoom is not None:
        cmd += ["--effect-zoom", str(effect_zoom)]
    if effect_pan is not None:
        cmd += ["--effect-pan", str(effect_pan)]
    if image_clip_seconds:
        cmd += ["--image-clip-seconds", str(image_clip_seconds)]
    if image_fade_seconds:
        cmd += ["--image-fade-seconds", str(image_fade_seconds)]
    if image_playback:
        cmd += ["--image-playback", image_playback]
    if story_slots:
        cmd += ["--image-slots", json.dumps(story_slots, ensure_ascii=False)]

    preview_sources: List[Path] = []
    selection = _get_selected_images(p)
    if selection:
        preview_sources.extend(selection)
    elif p.cover.exists():
        preview_sources.append(p.cover)
    else:
        preview_sources.extend(_list_project_images(p, limit=6))
    for img_path in preview_sources:
        cmd += ["--preview-image", str(img_path)]
    if no_burn:
        cmd += ["--no-burn"]

    cmd += ["--title-seconds", str(title_seconds)]

    # 5) Font file resolution (project fonts/ then global app/fonts)
    font_file_path = None
    if font_file_name:
        candidates = [
            p.dir / "fonts" / font_file_name,   # per-project
            GLOBAL_FONTS_DIR / font_file_name,  # global
        ]
        for cand in candidates:
            try:
                if cand.exists():
                    font_file_path = cand
                    break
            except Exception:
                pass
        if not font_file_path:
            raise HTTPException(
                400,
                f"Font '{font_file_name}' not found. Looked in: "
                f"{(p.dir / 'fonts')}, {GLOBAL_FONTS_DIR}"
            )
        cmd += ["--font-file", str(font_file_path)]  # <-- append ONCE, after cmd exists

    # 6) Title card flags
    if title_from_mp3 or (show_title and use_mp3_title):
        cmd += ["--title-from-mp3"]
    
    if show_end_card:
        if end_card_text is None or not str(end_card_text).strip():
            end_card_text = "Thank You for Watching"
        cmd += ["--thanks-text", str(end_card_text)]
        cmd += ["--thanks-seconds", str(end_card_seconds)]
    # 7) Start job
    job_id = jobs.start(slug, "render", cmd, cwd=p.dir)

    # 8) Log command + resolved audio
    try:
        p.logs_dir.mkdir(parents=True, exist_ok=True)
        quoted = " ".join(shlex.quote(str(x)) for x in cmd)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p.logs_dir / "render.log", "a", encoding="utf-8") as lf:
            lf.write("\n=== Render launch at {} ===\n".format(stamp))
            lf.write("[LyricSync] Working dir: {}\n".format(str(p.dir)))
            lf.write(f"[LyricSync] Resolved audio file: {audio_path}\n")
            lf.write("[LyricSync] Command:\n")
            lf.write(quoted + "\n")
        with open(p.logs_dir / "render_cmd.txt", "w", encoding="utf-8") as cf:
            cf.write(quoted + "\n")
    except Exception:
        pass
    
    applied = {
    # effects
    "effect": effect,
    "effect_strength": effect_strength,
    "effect_cycle": effect_cycle,
    "effect_zoom": effect_zoom,
    "effect_pan": effect_pan,
    "fps": fps,
    # text/style
    "style": style,
    "text_theme": text_theme,
    "font": font,
    "font_size": font_size,
    "outline": outline,
    "ass_align": ass_align,
    "margin_v": margin_v,
    "force_res": force_res,
    # colors
    "font_color": font_color,
    "outline_color": outline_color,
    "thanks_color": endcard_color,
    "thanks_border_color": endcard_border_color,
    # title/end card
    "show_title": show_title,
    "use_mp3_title": use_mp3_title or title_from_mp3,
    "title_seconds": title_seconds,
    "show_end_card": show_end_card,
    "end_card_text": end_card_text or "Thank You for Watching",
    "end_card_seconds": end_card_seconds,
    # io
    "srt_name": srt_name,
    "no_burn": no_burn,
    }

    return {"ok": True, "job_id": job_id}

@app.get("/api/projects/{slug}/logs/{job}")
def api_logs(slug: str, job: str, offset: int = 0):
    p = projects.get(slug)
    log_path = p.logs_dir / f"{job}.log"
    if not log_path.exists():
        return {"offset": 0, "chunk": ""}
    with open(log_path, "rb") as f:
        f.seek(offset)
        data = f.read()
    return {"offset": offset + len(data), "chunk": data.decode("utf-8", errors="ignore")}

@app.get("/healthz")
def healthz():
    return {"ok": True}
