
from __future__ import annotations
import re
import json
from pathlib import Path
from typing import List, Dict, Any

SRT_BLOCK_RE = re.compile(
    r"(?:^|\n)(\d+)\s*\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.+?)(?=\n{2,}|\Z)",
    re.DOTALL,
)
TIMECODE_LINE_RE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$")

def _to_seconds(h, m, s, ms) -> float:
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0

def parse_srt(path: Path) -> List[Dict[str, Any]]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    segments = []
    for m in SRT_BLOCK_RE.finditer(txt):
        idx = int(m.group(1))
        start = _to_seconds(m.group(2), m.group(3), m.group(4), m.group(5))
        end   = _to_seconds(m.group(6), m.group(7), m.group(8), m.group(9))
        raw_text = m.group(10).replace("\r", "")
        lines = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.isdigit():
                # sometimes indices get glued to the text when blank lines are missing
                continue
            if TIMECODE_LINE_RE.match(stripped):
                continue
            lines.append(stripped)
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        segments.append({"id": f"L{idx}", "text": text, "start": start, "end": end})
    return segments

def srt_timestamp(sec: float) -> str:
    if sec < 0: sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(segments: List[Dict[str, Any]], out_path: Path) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = srt_timestamp(seg["start"])
        end   = srt_timestamp(seg["end"])
        text  = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8", newline="\r\n")

def load_project(json_path: Path) -> Dict[str, Any]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return data

def save_project(json_path: Path, data: Dict[str, Any]) -> None:
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def export_srt(data: Dict[str, Any], out_path: Path) -> None:
    segs = data.get("segments", [])
    write_srt(segs, out_path)

def ensure_project_from_srt(srt_path: Path, json_path: Path, audio_path: Path) -> None:
    if srt_path.exists():
        segs = parse_srt(srt_path)
    else:
        segs = []
    data = {
        "version": 1,
        "audio_path": str(audio_path),
        "title": json_path.stem,
        "fps": 30,
        "level": "line",
        "segments": segs,
    }
    save_project(json_path, data)
