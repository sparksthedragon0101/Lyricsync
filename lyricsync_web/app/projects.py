import orjson
import json
import subprocess

import os
import re
import tempfile, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from .srt_json import ensure_project_from_srt, load_project
from .server.core.llm_client import OllamaProvider
from .server.core.paths import PROJECTS_ROOT
import aiofiles


try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3
except ImportError:
    mutagen = None
    EasyID3 = None
    ID3 = None

class ProjectNotFound(Exception):
    pass

def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "project"

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma"}
VIDEO_EXTS = {".mp4", ".mkv"}
WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)
VERSE_SPLIT_RE = re.compile(r"\n\s*\n")

def _pick_from_dir_audio(folder: Path) -> Path | None:
    if not folder or not folder.exists():
        return None
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
    if not files:
        return None
    mp3s = [f for f in files if f.suffix.lower() == ".mp3"]
    return mp3s[0] if mp3s else max(files, key=lambda f: f.stat().st_size)

@dataclass
class Project:
    slug: str
    dir: Path
    audio: Path
    cover: Path
    official_txt: Path
    edited_txt: Path
    aligned_srt: Path
    edited_srt: Path
    logs_dir: Path

    @property
    def config(self) -> Dict[str, Any]:
        path = self.dir / "project_config.json"
        if not path.exists():
            return {}
        try:
            return orjson.loads(path.read_bytes())
        except Exception:
            return {}

    def resolve_audio_file(self) -> Path | None:
        try:
            a = self.audio
            if a.exists():
                if a.is_file():
                    return a if a.stat().st_size > 0 else None
                if a.is_dir():
                    cand = _pick_from_dir_audio(a)
                    if cand:
                        return cand
        except Exception:
            pass
        for name in ("audio.mp3", "audio.wav", "audio.m4a", "audio.flac",
                     "audio.aac", "audio.ogg", "audio.wma"):
            cand = self.dir / name
            if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                return cand
        cand = _pick_from_dir_audio(self.dir / "audio")
        if cand:
            return cand
        legacy = self.dir / "audio"
        try:
            if legacy.exists() and legacy.is_file() and legacy.stat().st_size > 0:
                return legacy
        except Exception:
            pass
        return None

class Projects:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, slug: str):
        slug = slugify(slug)
        d = self.root / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "logs").mkdir(exist_ok=True)
        return self.get(slug)

    def get(self, slug: str) -> Project:
        d = self.root / slug
        if not d.exists():
            raise ProjectNotFound(slug)
        return Project(
            slug=slug,
            dir=d,
            audio=d / "audio",
            cover=d / "cover.png",
            official_txt=d / "official_lyrics.txt",
            edited_txt=d / "edited.txt",
            aligned_srt=d / "aligned.srt",
            edited_srt=d / "edited.srt",
            logs_dir=d / "logs",
        )

    def list_projects(self) -> List[Dict]:
        rows = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            rows.append(self.meta(d.name))
        return rows

    def meta(self, slug: str) -> Dict:
        p = self.get(slug)
        audio_file = p.resolve_audio_file()
        
        is_story = False
        config_path = p.dir / "project_config.json"
        if config_path.exists():
            try:
                data = orjson.loads(config_path.read_bytes())
                is_story = data.get("is_story", False)
            except:
                pass

        return {
            "slug": slug,
            "audio": audio_file.name if audio_file else None,
            "cover": p.cover.exists(),
            "official_txt": p.official_txt.exists(),
            "edited_txt": p.edited_txt.exists(),
            "aligned_srt": p.aligned_srt.exists(),
            "edited_srt": p.edited_srt.exists(),
            "preview_mp4": (p.dir / "preview.mp4").exists(),
            "is_story": is_story,
        }

    @staticmethod
    def _atomic_write(src_fp, dst_path: Path, chunk_size: int = 1024 * 1024) -> None:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(dst_path.parent), delete=False) as tmp:
            for chunk in iter(lambda: src_fp.read(chunk_size), b""):
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.replace(tmp_name, dst_path)

    def save_upload(self, p: Project, up, name_hint: str) -> Path | None:
        if not up:
            return None
        ext = ""
        if hasattr(up, "filename") and up.filename:
            _, ext = os.path.splitext(up.filename)
            ext = ext.lower()
        if name_hint == "audio":
            audio_dir = p.dir / "audio"
            if audio_dir.exists() and not audio_dir.is_dir():
                try:
                    audio_dir.unlink()
                except Exception as exc:
                    raise RuntimeError(f"Cannot prepare audio directory at {audio_dir}") from exc
            raw_stem = Path(up.filename).stem if hasattr(up, "filename") and up.filename else ""
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (raw_stem or "").strip()).strip("-._") or "audio"
            if ext in AUDIO_EXTS:
                filename = f"{safe_stem}{ext}"
            else:
                mime = (getattr(up, "content_type", "") or "").lower()
                guessed = ".mp3" if "mpeg" in mime else ".wav" if "wav" in mime else ""
                filename = f"{safe_stem}{guessed}" if guessed else safe_stem
            path = audio_dir / filename
        elif name_hint == "cover":
            path = p.cover
        elif name_hint.endswith(".txt"):
            path = p.dir / "official_lyrics.txt"
        elif name_hint.endswith(".srt"):
            if "edited" in name_hint.lower():
                path = p.dir / "edited.srt"
            else:
                path = p.dir / "aligned.srt"
        else:
            path = p.dir / (name_hint + ext)
        try:
            up.file.seek(0)
        except Exception:
            pass
        Projects._atomic_write(up.file, path)
        return path

router = APIRouter(prefix="/api/projects", tags=["projects"])
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
_story_ollama_client = OllamaProvider(base_url=OLLAMA_BASE_URL)

def _story_slots_path(p: Project) -> Path:
    return p.dir / "image_story_prompts.json"

def _read_story_slots(p: Project) -> List[Dict[str, Any]]:
    path = _story_slots_path(p)
    if not path.exists():
        return []
    try:
        data = orjson.loads(path.read_bytes())
    except Exception:
        return []
    return _normalize_slots(data if isinstance(data, list) else [])

def _write_story_slots(p: Project, slots: List[Dict[str, Any]]) -> None:
    path = _story_slots_path(p)
    try:
        path.write_bytes(orjson.dumps(slots, option=orjson.OPT_INDENT_2))
    except Exception:
        pass

def _split_verses(raw: str) -> List[str]:
    return [v.strip() for v in VERSE_SPLIT_RE.split(raw or "") if v.strip()]

def _timing_segments(project: Project) -> List[Dict]:
    json_path = project.dir / "timing.json"
    if not json_path.exists() or json_path.stat().st_size == 0:
        candidate = project.edited_srt if project.edited_srt.exists() else project.aligned_srt
        audio_file = project.resolve_audio_file() or project.audio
        if candidate and audio_file:
            ensure_project_from_srt(candidate, json_path, audio_file)
    if not json_path.exists():
        return []
    data = load_project(json_path)
    if not isinstance(data, dict):
        return []
    return data.get("segments", [])

def _verse_intervals(lyrics: str, segments: List[Dict]) -> List[Dict]:
    verses = _split_verses(lyrics)
    if not verses:
        return []
    if not segments:
        return [{"start": None, "end": None} for _ in verses]
    intervals = []
    seg_idx = 0
    last_segment_end = next((s.get("end") for s in reversed(segments) if s.get("end") is not None), None)
    for v_idx, verse in enumerate(verses):
        words = WORD_RE.findall(verse.lower())
        needed = len(words)
        start = None
        end = None
        collected = 0
        while seg_idx < len(segments) and (needed == 0 or collected < needed):
            seg = segments[seg_idx]
            seg_words = WORD_RE.findall((seg.get("text") or "").lower())
            if not seg_words:
                seg_idx += 1
                continue
            if start is None:
                start = seg.get("start")
            end = seg.get("end")
            collected += len(seg_words)
            seg_idx += 1
            if needed == 0:
                break
        if v_idx == len(verses) - 1:
            # Let the final verse stretch over any remaining lyric segments
            if last_segment_end is not None:
                end = last_segment_end
            if start is None and segments:
                start = segments[0].get("start")
        if start is None and seg_idx < len(segments):
            start = segments[seg_idx].get("start")
        if end is None:
            end = start
        intervals.append({"start": start, "end": end})
    return intervals

def _verse_slots(lyrics: str, segments: List[Dict]) -> List[Dict]:
    verses = _split_verses(lyrics)
    intervals = _verse_intervals(lyrics, segments)
    slots = []
    for idx, verse in enumerate(verses):
        slot = intervals[idx] if idx < len(intervals) else {"start": None, "end": None}
        slots.append({"text": verse, "start": slot.get("start"), "end": slot.get("end")})
    return slots

def _normalize_slots(raw_slots: List[Dict]) -> List[Dict]:
    cleaned = []
    for entry in raw_slots:
        if not isinstance(entry, dict):
            continue
        prompt = str(entry.get("prompt") or "").strip()
        if not prompt:
            continue
        def _to_float(value):
            try:
                return float(value)
            except Exception:
                return None
        def _to_image_path(val):
            if isinstance(val, str):
                val = val.strip()
                return val or None
            return None
        cleaned.append({
            "prompt": prompt,
            "start": _to_float(entry.get("start")),
            "end": _to_float(entry.get("end")),
            "image_path": _to_image_path(entry.get("image_path"))
        })
    return cleaned

def refresh_story_slot_timings(project: Project, segments: List[Dict[str, Any]] | None = None) -> List[Dict]:
    """
    Recompute the timing bounds for existing story slots so they stay aligned
    with the latest lyric segments.
    """
    slots = _read_story_slots(project)
    if not slots:
        return []
    if not project.official_txt.exists():
        return slots
    try:
        lyrics = project.official_txt.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        lyrics = project.official_txt.read_text(encoding="latin-1", errors="ignore")
    segments = segments if segments is not None else _timing_segments(project)
    if not segments:
        return slots
    verse_slots = _verse_slots(lyrics, segments)
    if not verse_slots:
        return slots
    for idx, slot in enumerate(slots):
        if idx >= len(verse_slots):
            break
        mapped = verse_slots[idx]
        slot["start"] = mapped.get("start")
        slot["end"] = mapped.get("end")
    _write_story_slots(project, slots)
    return slots

class ImageStoryRequest(BaseModel):
    model: str
    style: str | None = None

class StorySlotsRequest(BaseModel):
    slots: List[Dict[str, Any]]

def _list_project_images_sorted(p: Project, limit: int | None = None) -> List[Path]:
    images_dir = p.dir / "images"
    if not images_dir.exists():
        return []
    images = [f for f in images_dir.iterdir() if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    images.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return images[:limit] if limit is not None else images

def get_llm_story(slots: List[Dict[str, float]], model: str, style: str | None = None, is_story: bool = False) -> List[Dict]:
    if not slots:
        return []
    def _clean_text(text: str) -> str:
        return (text or "").replace("\n", " ").replace("\r", " ").strip()
    verse_list = "\n".join(f"{idx+1}. {_clean_text(slot.get('text', ''))}" for idx, slot in enumerate(slots))
    
    style_desc = STYLE_HINTS.get(style or "", "General/Unspecified") if "STYLE_HINTS" in globals() else str(style or "General")

    if is_story:
        system_instructions = (
            "You are a narrative director and prompt artist. Analyze the following story chunks and identify "
            "distinct 'visual beats' or 'scenes'. A scene should cover one or more chunks where the visual "
            "setting or primary action remains consistent.\n"
            f"Art Style Directive: Ensure the prompts reflect this visual style: {style_desc}\n\n"
            "Return a JSON object with a 'scenes' array. Each scene must have:\n"
            " - 'start_chunk': The 1-based index (integer) of the first chunk in this scene.\n"
            " - 'prompt': A detailed, evocative Stable Diffusion prompt for this scene.\n"
            "Keep the number of scenes reasonable (e.g., 1 scene per 3-8 chunks)."
        )
        user_content = (
            f"Here is the story text divided into numbered chunks:\n{verse_list}\n\n"
            "Identify the visual beats and return the JSON object."
        )
    else:
        system_instructions = (
            "You are a prompt writer. Given a numbered list of song verses, return a JSON array of short, "
            "evocative image prompts that correspond to each verse. The array must match the verse count.\n"
            f"Art Style Directive: Ensure the prompts reflect this visual style: {style_desc}"
        )
        user_content = (
            f"Here are the verses:\n{verse_list}\n\n"
            f"Generate exactly {len(slots)} prompts describing scenes that follow the narrative.\n"
            "Respond only with a JSON array of strings (double quotes)."
        )

    messages = [
        {"role": "system", "content": system_instructions},
        {"role": "user", "content": user_content},
    ]
    try:
        response = _story_ollama_client.chat(messages=messages, model=model, temperature=0.5, timeout=120)
        text = response.text or ""
        
        if is_story:
            scenes = []
            # Extract JSON block
            match = re.search(r"(\{[\s\S]*\})", text)
            if match:
                try:
                    data = orjson.loads(match.group(1))
                    scenes = data.get("scenes", [])
                except Exception:
                    pass

            if not scenes:
                return []
            
            if not isinstance(scenes, list):
                return []
            
            story_slots = []
            for i, scene in enumerate(scenes):
                try:
                    idx = int(scene.get("start_chunk", 1)) - 1
                except (ValueError, TypeError):
                    continue
                if idx < 0 or idx >= len(slots):
                    continue
                
                start_t = slots[idx].get("start")
                # End time is the start of the next scene, or the end of the final chunk
                if i + 1 < len(scenes):
                    try:
                        next_idx = int(scenes[i+1].get("start_chunk", idx + 2)) - 1
                    except (ValueError, TypeError):
                        next_idx = idx + 1
                    next_idx = max(idx + 1, min(next_idx, len(slots) - 1))
                    end_t = slots[next_idx].get("start")
                else:
                    end_t = slots[-1].get("end")
                
                story_slots.append({
                    "prompt": str(scene.get("prompt") or "").strip(),
                    "start": start_t,
                    "end": end_t
                })
            return story_slots
        else:
            prompts = []
            # Extract JSON array
            match = re.search(r"(\[[\s\S]*\])", text)
            if match:
                try:
                    prompts = orjson.loads(match.group(1))
                except Exception:
                    pass
            
            if not prompts or not isinstance(prompts, list) or len(prompts) != len(slots):
                return []
            return [
                {"prompt": str(prompt).strip(), "start": slot.get("start"), "end": slot.get("end")}
                for prompt, slot in zip(prompts, slots)
            ]
    except Exception as exc:
        print(f"Error calling Ollama for story prompts: {exc}")
        return []

@router.post("/{slug}/image_story")
async def image_story(slug: str, req: ImageStoryRequest):
    projects = Projects(PROJECTS_ROOT)
    try:
        project = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.official_txt.exists():
        raise HTTPException(status_code=404, detail="Lyrics missing for this project.")
    lyrics = project.official_txt.read_text(encoding="utf-8")
    segments = _timing_segments(project)
    slots = _verse_slots(lyrics, segments)
    if not slots:
        raise HTTPException(status_code=404, detail="Could not derive verse timings for this project.")
    is_story = project.config.get("is_story", False)
    story_slots = get_llm_story(slots, req.model, req.style, is_story=is_story)
    available_images = _list_project_images_sorted(project, limit=len(story_slots))
    for idx, slot in enumerate(story_slots):
        if idx < len(available_images):
            slot["image_path"] = available_images[idx].relative_to(project.dir).as_posix()
        else:
            slot["image_path"] = None
    if not story_slots:
        raise HTTPException(status_code=500, detail="Failed to generate story prompts from LLM.")
    _write_story_slots(project, story_slots)
    return {"ok": True, "prompts": story_slots}

@router.get("/{slug}/image_story_slots")
def get_story_slots(slug: str):
    projects = Projects(PROJECTS_ROOT)
    try:
        project = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="Project not found")
    slots = _read_story_slots(project)
    return {"ok": True, "slots": slots}

@router.post("/{slug}/image_story_slots")
def save_story_slots(slug: str, req: StorySlotsRequest):
    projects = Projects(PROJECTS_ROOT)
    try:
        project = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="Project not found")
    slots = _normalize_slots(req.slots or [])
    _write_story_slots(project, slots)
    return {"ok": True, "slots": slots}


def _get_ffprobe_metadata(path: Path) -> Dict[str, str]:
    """Fallback to ffprobe if mutagen fails or returns empty tags."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        fmt = data.get("format", {})
        tags = fmt.get("tags", {})
        # Normalize keys as ffprobe tags can be case-variant
        normalized = {}
        for k, v in tags.items():
            normalized[k.lower()] = v
        
        return {
            "title": normalized.get("title", ""),
            "artist": normalized.get("artist", ""),
            "album": normalized.get("album", ""),
        }
    except Exception:
        return {}


def get_audio_metadata(p: Project) -> Dict[str, Any]:
    if not mutagen:
        return {}
    audio_file = p.resolve_audio_file() or p.audio
    if not audio_file or not audio_file.exists():
        return {}
    
    meta = {}
    try:
        # EasyID3 for MP3s is simplest for Title/Artist
        if audio_file.suffix.lower() == ".mp3":
            try:
                tags = EasyID3(audio_file)
            except mutagen.id3.ID3NoHeaderError:
                tags = mutagen.id3.ID3()
            except Exception:
                # Sometimes EasyID3 strictly requires ID3-ish files
                tags = {}

            # get returns list of strings
            meta["title"] = tags.get("title", [""])[0] if tags else ""
            meta["artist"] = tags.get("artist", [""])[0] if tags else ""
            meta["album"] = tags.get("album", [""])[0] if tags else ""
        else:
            # Fallback for other formats if mutagen supports them generally
            f = mutagen.File(audio_file)
            if f:
                meta["title"] = f.get("title", [""])[0] if "title" in f else ""
            
        # Get duration regardless of format if possible
        try:
            f_info = mutagen.File(audio_file)
            if f_info and f_info.info and hasattr(f_info.info, "length"):
                meta["duration"] = round(f_info.info.length)
        except Exception:
            pass
    except Exception as e:
        print(f"Error reading metadata: {e}")
    
    # Fallback to ffprobe if title/artist are still missing
    if not meta.get("title") or not meta.get("artist"):
        ff_meta = _get_ffprobe_metadata(audio_file)
        if ff_meta:
            if not meta.get("title"):
                meta["title"] = ff_meta.get("title", "")
            if not meta.get("artist"):
                meta["artist"] = ff_meta.get("artist", "")
            if not meta.get("album") and "album" in ff_meta:
                meta["album"] = ff_meta.get("album", "")

    return meta


def set_audio_metadata(p: Project, title: str) -> None:
    if not mutagen:
        raise RuntimeError("mutagen not installed")
    audio_file = p.resolve_audio_file() or p.audio
    if not audio_file or not audio_file.exists():
        raise FileNotFoundError("Audio file not found")
    
    if audio_file.suffix.lower() == ".mp3":
        try:
            tags = EasyID3(audio_file)
        except mutagen.id3.ID3NoHeaderError:
            try:
                tags = mutagen.file.File(audio_file, easy=True)
                tags.add_tags()
            except Exception:
                tags = mutagen.id3.ID3()
        except Exception:
             # Fallback if something else goes wrong opening
             tags = mutagen.file.File(audio_file, easy=True)

        if tags is None:
             tags = mutagen.file.File(audio_file, easy=True)
             if tags is None:
                 # Last ditch: try plain ID3
                 try:
                     tags = mutagen.id3.ID3(audio_file)
                 except mutagen.id3.ID3NoHeaderError:
                     tags = mutagen.id3.ID3()

        if tags is not None:
             tags["title"] = title
             tags.save(audio_file)
    else:
        # Generic handling
        f = mutagen.File(audio_file)
        if f:
            f["title"] = title
            f.save()

# --- Image Management Endpoints ---

class ImagePathRequest(BaseModel):
    path: str

class ImageSelectionRequest(BaseModel):
    paths: List[str]

@router.get("/{slug}/images")
def get_project_images(slug: str):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    
    # Use the existing helper
    images = _list_project_images_sorted(p)
    # Return relative paths for the UI (e.g. "images/foo.png" or "cover.png")
    rel_paths = []
    for img in images:
        try:
            rel_paths.append(img.relative_to(p.dir).as_posix())
        except ValueError:
            pass
    return {"images": rel_paths}

@router.delete("/{slug}/images")
def delete_project_image(slug: str, req: ImagePathRequest):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
    
    # Prevent directory traversal
    target = (p.dir / req.path).resolve()
    try:
        target.relative_to(p.dir.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
        
    if target.exists():
        try:
            target.unlink()
        except Exception as e:
            raise HTTPException(500, f"Could not delete: {e}")
            
    return {"ok": True}

@router.delete("/{slug}/images/all")
def delete_all_project_images(slug: str):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
        
    images_dir = p.dir / "images"
    if images_dir.exists():
        for f in images_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except: pass
    return {"ok": True}

@router.get("/{slug}/images/selection")
def get_image_selection(slug: str):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")

    path = p.dir / "image_selection.json"
    if not path.exists():
        return {"selection": []}

    try:
        data = orjson.loads(path.read_bytes())
        if isinstance(data, dict):
            return {"selection": data.get("selection", [])}
        elif isinstance(data, list):
            return {"selection": data}
        return {"selection": []}
    except Exception:
        return {"selection": []}

@router.post("/{slug}/images/selection")
def save_image_selection(slug: str, req: ImageSelectionRequest):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
        
    # Save to a JSON file
    msg = {"selection": req.paths}
    # Using atomic write helper if possible, or just direct write
    # Projects._atomic_write implies we can access it, but staticmethod on class 
    # Just write directly for simplicity
    (p.dir / "image_selection.json").write_text(orjson.dumps(msg).decode("utf-8"), encoding="utf-8")
    return {"ok": True}

@router.post("/{slug}/images/upload")
async def upload_project_images(slug: str, files: List[UploadFile] = File(...)):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")
        
    img_dir = p.dir / "images"
    img_dir.mkdir(exist_ok=True)
    
    saved_paths = []
    for up in files:
        if not up.filename: continue
        # sanitize
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", up.filename)
        dest = img_dir / safe
        
        # Unique name if exists
        idx = 1
        stem, ext = os.path.splitext(safe)
        while dest.exists():
            dest = img_dir / f"{stem}_{idx}{ext}"
            idx += 1
            
        try:
            with dest.open("wb") as f:
                shutil.copyfileobj(up.file, f)
            saved_paths.append(dest.relative_to(p.dir).as_posix())
        except Exception:
            pass
            
    return {"ok": True, "saved": saved_paths}

# --- LLM Image Prompt Logic ---

STYLE_HINTS = {
    "photorealistic": "photorealistic, detailed photography with lifelike lighting",
    "stylized": "stylized concept art with painterly brush strokes",
    "anime": "anime illustration with cel shading and bold lighting",
    "animated": "animated work, 2d hand-drawn aesthetic, vibrant colors, expressive",
    "landscape": "epic wide environmental landscape shot with sweeping vistas",
    "illustration": "high-quality professional 2D illustration, artistic composition, flat colors, no photorealism, non-photorealistic, expressive drawing style, hand-drawn feel",
}

SUB_STYLE_HINTS = {
    "generic": "stylized concept art, digital painting, highly detailed, evocative composition",
    "pixel_art": "Strictly pixel art, 16-bit retro game style, blocky, low-res aesthetic, sharp edges. Do NOT use brush strokes, blurring, or painterly terms.",
    "3d_render": "3D CGI render, Octane render, digital art, ray-traced, sharp focus, Unreal Engine 5 style, hyper-realistic materials.",
    "surrealist": "Surrealist art, dreamlike, Salvador Dali style, impossible geometry, melting forms, strange, ethereal, psychological horror elements.",
    "anime_80s": "1980s cel-shaded anime style, retro aesthetic, grain, hand-drawn animation, detailed mechanical designs, vintage color palette",
    "anime_90s": "1990s anime aesthetic, golden age style, detailed cel shading, atmospheric lighting, hand-drawn nostalgic feel",
    "anime_00s": "early 2000s digital anime style, sharp lines, experimental coloring, transition era aesthetic",
    "anime_ghibli": "Studio Ghibli style, Hayao Miyazaki, hand-painted backgrounds, lush nature, soft wind, whimsical, detailed clouds, watercolor aesthetic",
    "anime_mappa": "MAPPA studio style, high contrast, dynamic camera angles, detailed particle effects, modern clean lines, cinematic lighting",
    "anime_trigger": "Studio Trigger style, hyper-dynamic, neon accents, sharp geometric shapes, exaggerated perspective, vibrant and poppy colors",
    "anime_kyoani": "Kyoto Animation style, KyoAni, moist eyes, incredibly detailed hair, soft lighting, emotional atmosphere, high production value",
    "anime_ufotable": "Ufotable style, digital compositing, intense particle effects, deep colors, cinematic depth of field, high-budget visual fidelity",
    "anime_madhouse": "Madhouse studio style, dark and gritty, detailed linework, mature aesthetic, dramatic shadows, high frame-rate feel",
    "anime_sunrise": "Sunrise/Bandai style, mecha aesthetic, detailed mechanical shading, epic scale, space opera vibes, dramatic posing",
    "anime_shaft": "Studio Shaft style, Shinbo Akiyuki, head tilts, abstract backgrounds, avant-garde composition, text overlays, minimal but striking colors",
    "anim_looney": "classic looney tunes style, hand-painted backgrounds, slapstick energy, exaggerated features, golden age animation",
    "anim_disney": "classic disney animation style, 1950s hand-drawn, rich technicolor, fluid movement, fairytale aesthetic",
    "anim_simpsons": "simpsons style, matt groening, flat colors, yellow skin, simple distinct outlines, satirical cartoon aesthetic",
    "anim_southpark": "south park style, construction paper cutout aesthetic, geometric simplicity, textured paper look, stop-motion feel",
    "anim_nickelodeon": "classic 90s nickelodeon cartoon style, rugrats/ren&stimpy aesthetic, squiggly lines, wacky color palettes, offbeat character designs",
    "anim_cel": "traditional 2d cel animation, ink and paint, hand-drawn, authentic animation cells, vintage aesthetic",
    "anim_digital": "modern digital 2d animation, clean vector lines, flash animation style, crisp colors, smooth tweening",
    "anim_clay": "claymation style, ardman/laika aesthetic, plasticine texture, visible fingerprints, studio lighting, miniature set",
    "anim_cutout": "cutout stop-motion, terry gilliam style, collage aesthetic, rough edges, disjointed movement feel",
    "anim_rotoscope": "rotoscope animation, a scanner darkly style, traced realism, dreamlike movement, shifting lines, uncanny valley aesthetic",
    "anim_modern": "modern minimalist 2D animation style, Adventure Time and Steven Universe aesthetic, flat colors, thick clean outlines, expressive noodle limbs, simple geometric shapes, vibrant pastel color palette, whimsical and imaginative backgrounds.",
    "illu_gothic": "professional black and white pen and ink illustration, intricate Gothic Victorian macabre aesthetic, fine pen and ink hatching and cross-hatching, high-contrast monochrome, hand-drawn linework, stark blacks and whites, no color, no gradients, dramatic chiaroscuro lighting, vintage engraving style, style of Edward Gorey and Charles Addams.",
    "illu_storybook": "Classic 19th-century storybook illustration, soft watercolor and ink, whimsical and charming, delicate textures, hand-drawn nostalgic feel, Beatrix Potter style.",
    "illu_cybercore": "Modern digital illustration, cybercore aesthetic, vibrant neon cyan and magenta accents, sharp clean vector lines, futuristic high-tech interface elements, glitch art tendencies.",
    "illu_botanical": "Vintage botanical field guide illustration, detailed scientific study, ink and wash, aged parchment background, intricate natural floral details, labeled specimen style.",
    "illu_synthwave": "Synthwave digital illustration, 1980s retro-futurism, bold pink and purple sunset gradients, wireframe grid landscape, glowing neon highlights, cinematic retro vibe.",
    "illu_medieval": "Modern medieval illumination style, techno-medieval aesthetic, intricate Celtic knotwork decorative borders, mythical creatures in border corners, flat colors with parchment texture, monk-like figures in blue robes, absurdist and humorous situations, 2D illustration, no photorealism, anachronistic elements like mechanical winged transport, satirical archaic English captions in boxes, clean modern line art with medieval woodcut influence, whimsical and historical atmosphere."
}

class ImagePromptRequest(BaseModel):
    model: str
    temperature: float = 0.35
    max_tokens: int = 512
    style: str | None = None
    sub_style: str | None = None
    no_humans: bool = False
    prompt: str = "" # user initial text

async def _read_lyrics_for_prompt(p: Project) -> str:
    if not p.official_txt.exists():
        # Fallback to edited.txt if official missing? Or audio tags?
        # For now, require official lyrics or return empty
        return "" 
    try:
        async with aiofiles.open(p.official_txt, "r", encoding="utf-8") as f:
            txt = await f.read()
    except UnicodeDecodeError:
        async with aiofiles.open(p.official_txt, "r", encoding="latin-1", errors="ignore") as f:
            txt = await f.read()
    lyrics = txt.strip()
    if len(lyrics) > 4000:
        lyrics = lyrics[:4000] + "\n...(truncated)"
    return lyrics

def _parse_prompt_response(payload: str) -> tuple[str, str]:
    positive = ""
    negative = ""
    clean = payload.strip()
    
    # Try robust JSON extraction first
    match = re.search(r"(\{[\s\S]*\})", clean)
    if match:
        try:
            blob = match.group(1)
            data = orjson.loads(blob)
            positive = str(data.get("positive", "")).strip()
            negative = str(data.get("negative", "")).strip()
        except Exception:
            pass

    # If JSON failed, try manual regex fallback for fields
    if not positive:
        pos_match = re.search(r'"positive":\s*"([^"]+)"', clean)
        if pos_match:
            positive = pos_match.group(1).strip()
    
    if not negative:
        neg_match = re.search(r'"negative":\s*"([^"]+)"', clean)
        if neg_match:
            negative = neg_match.group(1).strip()

    if not positive and not negative:
        # Final fallback: strip markdown blocks if they survived
        # Remove ```json ... ``` or ``` ... ```
        clean_final = re.sub(r"```[a-z]*\n?", "", clean).replace("```", "").strip()
        # If it looks like a JSON block, it probably failed parsing, so try to just strip the braces
        if clean_final.startswith("{") and clean_final.endswith("}"):
             # Just use it as-is or return raw
             positive = clean_final 
        else:
             positive = clean_final

    return positive, negative

@router.post("/{slug}/image_prompt")
async def generate_image_prompt(slug: str, req: ImagePromptRequest):
    projects = Projects(PROJECTS_ROOT)
    try:
        p = projects.get(slug)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found")

    lyrics = await _read_lyrics_for_prompt(p)
    
    style_desc = STYLE_HINTS.get(req.style or "", "")
    sub_desc = SUB_STYLE_HINTS.get(req.sub_style or "", "")
    
    # Construct System Prompt
    system = (
        "You are an expert Stable Diffusion prompt engineer. "
        "Your task is to create a detailed, high-quality image generation prompt based on the provided song lyrics and style description.\n"
        "Return the result as a JSON object with keys: 'positive' (the main prompt) and 'negative' (what to avoid).\n"
        "Focus on visual imagery, lighting, mood, and composition."
    )
    if req.no_humans:
        system += "\nMake sure the scene contains NO humans, people, or characters. Focus on scenery, objects, or abstract concepts."

    user_msg = f"Song Lyrics Context:\n{lyrics}\n\n"
    if req.prompt:
        user_msg += f"User Concept: {req.prompt}\n"
    
    user_msg += f"Style: {req.style or 'General'}\n"
    if style_desc:
        user_msg += f"Style Definition: {style_desc}\n"
    if sub_desc:
        user_msg += f"Specific Detail: {sub_desc}\n"
        
    user_msg += "\nGenerate the JSON prompt now."

    resp = _story_ollama_client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg}
        ],
        model=req.model,
        temperature=req.temperature,
        max_tokens=req.max_tokens
    )
    
    pos, neg = _parse_prompt_response(resp.text or "")
    
    return {"ok": True, "positive": pos, "negative": neg, "raw": resp.text}

