# lyricsync_web/app/server/routers/fonts_router.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from starlette import status
from pathlib import Path
import zipfile, io, re, secrets, shutil, os

from ..core.paths import GLOBAL_FONTS_DIR, CUSTOM_FONT_DIR, ALLOWED_FONT_EXTS, MAX_UPLOAD_BYTES

router = APIRouter()
ALLOWED_FONT_EXTS = {".ttf", ".otf"}
_slugify = re.compile(r"[^A-Za-z0-9._-]+")

def _sanitize(name: str) -> str:
    return _slugify.sub("-", name.strip().replace(" ", "_"))

def _unique(dest_dir: Path, filename: str) -> Path:
    base = Path(filename).stem
    ext  = Path(filename).suffix.lower()
    candidate = dest_dir / f"{base}{ext}"
    while candidate.exists():
        candidate = dest_dir / f"{base}-{secrets.token_hex(2)}{ext}"
    return candidate

def _is_font_file(p: Path) -> bool:
    return p.suffix.lower() in ALLOWED_FONT_EXTS

def _ensure_within(base: Path, target: Path) -> None:
    # Prevent zip-slip: ensure target is within base
    try:
        target.resolve().relative_to(base.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path in archive.")

@router.post("/upload")
async def upload_font(file: UploadFile = File(...)):
    filename = _sanitize(file.filename or "")
    if not filename:
        raise HTTPException(400, "Missing filename")

    # --- ZIP bundle
    if filename.lower().endswith(".zip"):
        ok, skip = 0, 0
        data = await file.read()  # read once
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for m in zf.infolist():
                if m.is_dir():
                    continue
                inner = _sanitize(Path(m.filename).name)
                ext = Path(inner).suffix.lower()
                if ext not in ALLOWED_FONT_EXTS:
                    skip += 1
                    continue
                dest = _unique(GLOBAL_FONTS_DIR, inner)
                # (optional) zip-slip guard
                if not str(dest.resolve()).startswith(str(GLOBAL_FONTS_DIR)):
                    raise HTTPException(400, "Invalid path in archive.")
                with zf.open(m) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                ok += 1
        if ok == 0:
            raise HTTPException(400, "No .ttf/.otf files in zip")
        return {"status": "ok", "added": ok, "skipped": skip, "saved_to": str(GLOBAL_FONTS_DIR)}

    # --- Single file
    if Path(filename).suffix.lower() not in ALLOWED_FONT_EXTS:
        raise HTTPException(400, "Only .ttf/.otf or .zip")
    dest = _unique(GLOBAL_FONTS_DIR, filename)
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return {"status": "ok", "file": dest.name, "saved_to": str(GLOBAL_FONTS_DIR)}


@router.get("/download/{font_name}")
def download_font(font_name: str):
    if not font_name:
        raise HTTPException(404, "Font not found")
    safe_name = Path(font_name).name
    target = (GLOBAL_FONTS_DIR / safe_name).resolve()
    try:
        target.relative_to(GLOBAL_FONTS_DIR.resolve())
    except Exception:
        raise HTTPException(404, "Font not found")
    if not target.exists() or not _is_font_file(target):
        raise HTTPException(404, "Font not found")
    media = "font/otf" if target.suffix.lower() == ".otf" else "font/ttf"
    return FileResponse(target, media_type=media, filename=target.name)
