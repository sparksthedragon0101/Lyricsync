import json
import os
import re
import tempfile, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .srt_json import ensure_project_from_srt, load_project
from .server.core.llm_client import OllamaProvider
from .server.core.paths import PROJECTS_ROOT

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
    aligned_srt: Path
    edited_srt: Path
    logs_dir: Path

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
        return {
            "slug": slug,
            "audio": audio_file.name if audio_file else None,
            "cover": p.cover.exists(),
            "official_txt": p.official_txt.exists(),
            "aligned_srt": p.aligned_srt.exists(),
            "edited_srt": p.edited_srt.exists(),
            "preview_mp4": (p.dir / "preview.mp4").exists(),
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
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _normalize_slots(data if isinstance(data, list) else [])

def _write_story_slots(p: Project, slots: List[Dict[str, Any]]) -> None:
    path = _story_slots_path(p)
    try:
        path.write_text(json.dumps(slots, indent=2, ensure_ascii=False), encoding="utf-8")
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

class StorySlotsRequest(BaseModel):
    slots: List[Dict[str, Any]]

def _list_project_images_sorted(p: Project, limit: int | None = None) -> List[Path]:
    images_dir = p.dir / "images"
    if not images_dir.exists():
        return []
    images = [f for f in images_dir.iterdir() if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    images.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return images[:limit] if limit is not None else images

def get_llm_story(slots: List[Dict[str, float]], model: str) -> List[Dict]:
    if not slots:
        return []
    def _clean_text(text: str) -> str:
        return (text or "").replace("\n", " ").replace("\r", " ").strip()
    verse_list = "\n".join(f"{idx+1}. {_clean_text(slot.get('text', ''))}" for idx, slot in enumerate(slots))
    system_instructions = (
        "You are a prompt writer. Given a numbered list of song verses, return a JSON array of short, "
        "evocative image prompts that correspond to each verse. The array must match the verse count."
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
        match = re.search(r"\[[\s\S]*?\]", text)
        if not match:
            return []
        prompts = json.loads(match.group(0))
        if not isinstance(prompts, list) or len(prompts) != len(slots):
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
    story_slots = get_llm_story(slots, req.model)
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
