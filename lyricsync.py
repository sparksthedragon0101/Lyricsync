#!/usr/bin/env python3
"""
lyricsync.py — auto VAD retry + segment fallback
-----------------------------------------------
- WhisperX transcription (CPU/CUDA)
- **Auto VAD logic**: start with VAD on; if quality looks poor vs. official lyrics, re-run without VAD automatically
- Greedy word alignment → automatic **segment fallback** when it looks bad
- SRT export
- Optional preview MP4 (image+audio) with ffmpeg progress, scaling, optional burned-in subs

Usage examples:
  # Simple CPU run (auto VAD + auto segment fallback)
  python lyricsync.py --audio song.mp3 --lyrics official_lyrics.txt --device cpu

  # Force VAD ON / OFF
  python lyricsync.py --audio song.mp3 --lyrics official_lyrics.txt --vad on
  python lyricsync.py --audio song.mp3 --lyrics official_lyrics.txt --vad off

  # Force specific alignment
  python lyricsync.py --audio song.mp3 --lyrics official_lyrics.txt --align-mode segments
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess, tempfile
import sys
import glob
import math
import tempfile
import uuid
from pathlib import Path
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable, Set, Dict
from fontTools.ttLib import TTFont
from effects import build_effect_filter, choices as effect_choices

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="ctranslate2")

# ---------- Progress bar (tqdm) ----------
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

try:
    from mutagen.id3 import ID3          
    from mutagen.mp3 import MP3          
    from mutagen.easyid3 import EasyID3
    from mutagen import File as MutagenFile
except Exception:
    ID3 = None
    MP3 = None
    EasyID3 = None
    MutagenFile = None

def _fontsdir_opt_for_windows(font_file: str | None) -> str:
    """
    Ensure libass can actually find a font. If a specific font file was given,
    point to its folder; otherwise default to C:\\Windows\\Fonts.
    """
    if os.name != "nt":
        return ""
    # Prefer the directory of an explicit font file
    if font_file:
        fdir = os.path.dirname(font_file)
    else:
        fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if not os.path.isdir(fdir):
        return ""
    # escape for filtergraph
    fdir_esc = _esc_filter_dir_win(fdir)
    return f":fontsdir='{fdir_esc}'"

def _probe_audio_channels(audio_path: str) -> int:
    """
    Return number of channels in the first audio stream, or 2 if unknown.
    """
    if not shutil.which("ffprobe"):
        return 2
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "a:0",
             "-show_entries", "stream=channels",
             "-of", "default=nokey=1:nw=1",
             audio_path],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        v = proc.stdout.strip()
        ch = int(v) if v.isdigit() else 2
        return max(1, ch)
    except Exception:
        return 2
        
def _win_long_prefix(path: str) -> str:
    r"""Add \\?\ prefix so ffmpeg/libass can open very long Windows paths."""
    if os.name == "nt":
        path = os.path.abspath(path)
        if not path.startswith("\\\\?\\"):
            path = "\\\\?\\" + path
    return path

def _win_long_path(p: str) -> str:
    """
    Converts a Windows path to its long form (disables 8.3 DOS-style alias).
    """
    if not os.path.exists(p):
        return p
    buf = ctypes.create_unicode_buffer(260)
    get_long_path_name = ctypes.windll.kernel32.GetLongPathNameW
    get_long_path_name(p, buf, 260)
    return buf.value or p
    
def _esc_filter_path_win(p: str) -> str:
    r"""
    Make a Windows path safe inside ffmpeg filter args:
    - use forward slashes
    - escape ONLY the drive colon (C\:/...)
    """
    import os, re
    s = os.path.abspath(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", s):
        s = s[0] + r"\:" + s[2:]
    return s

def _detect_sub_charenc(path: str) -> str:
    """Return ffmpeg/libass charenc for common BOMs; default UTF-8."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\xff\xfe"):
        return "UTF-16LE"
    if head.startswith(b"\xfe\xff"):
        return "UTF-16BE"
    if head.startswith(b"\xef\xbb\xbf"):
        return "UTF-8"
    return "UTF-8"

def _esc_filter_dir_win(d: str) -> str:
    import os, re
    s = os.path.abspath(d).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", s):
        s = s[0] + r"\:" + s[2:]
    s = s.replace("'", r"\'")
    return s

def safe_temp_copy_for_filters(src_path: str, suffix: str = ".srt") -> str:
    """
    Copy a subtitle file to a short, safe path in the current project folder,
    normalize to UTF-8 (no BOM), and force a full disk flush so ffmpeg/libass
    can open it immediately on Windows.
    """
    import os, io

    src_path = os.path.abspath(src_path)
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Subtitle file not found: {src_path}")

    root = os.path.join(os.getcwd(), "ls_tmp_short")
    os.makedirs(root, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staged = os.path.join(root, f"sub_{token}{suffix}")

    # Read source and normalize to UTF-8 (handle UTF-16 and BOM)
    with open(src_path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xff\xfe"):
        text = data.decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16-be")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig")
    else:
        text = data.decode("utf-8", errors="ignore")

    # Write, flush, fsync to guarantee visibility to ffmpeg
    with open(staged, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())

    # Verify
    if not os.path.exists(staged):
        raise RuntimeError(f"Subtitle staging failed: {staged} missing")
    print(f"[INFO] Verified staged subtitle exists: {staged} ({os.path.getsize(staged)} bytes)")

    return staged


def _font_fullname_or_family(ttf_path):
    """Return internal Full or Family name from a TTF/OTF (requires fonttools)."""
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(ttf_path, lazy=True)
        full = fam = None
        for rec in f["name"].names:
            try:
                val = rec.toUnicode()
            except Exception:
                val = rec.string.decode(rec.getEncoding() or "utf-16-be", "ignore")
            if rec.nameID == 4 and not full: full = val     # Full name
            if rec.nameID == 1 and not fam:  fam  = val     # Family
        return full or fam
    except Exception:
        return None


_THEME_CONFIG_PATH = (Path(__file__).resolve().parent / "lyricsync_web" / "app" / "configs" / "themes.json").resolve()
_THEME_KEY_RE = re.compile(r"[^a-z0-9]+")


def _normalize_theme_key(value: str | None) -> str:
    if not value:
        return ""
    return _THEME_KEY_RE.sub("-", str(value).lower()).strip("-")

def _sort_story_slots(slots):
    if not isinstance(slots, list):
        return []
    ordered = []
    for idx, entry in enumerate(slots):
        if not isinstance(entry, dict):
            continue
        slot = dict(entry)
        def _to_float(val):
            try:
                return float(val)
            except Exception:
                return None
        if "start" in slot:
            slot["start"] = _to_float(slot.get("start"))
        if "end" in slot:
            slot["end"] = _to_float(slot.get("end"))
        ordered.append((idx, slot))
    ordered.sort(key=lambda item: (
        item[1].get("start") if isinstance(item[1].get("start"), (int, float)) else float("inf"),
        item[0]
    ))
    return [slot for _, slot in ordered]


def _load_text_themes() -> list[dict]:
    try:
        data = json.loads(_THEME_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
    except Exception:
        pass
    return []


def _resolve_text_theme(name: str | None) -> Optional[dict]:
    if not name:
        return None
    key = _normalize_theme_key(name)
    if not key:
        return None
    for theme in _load_text_themes():
        slug = _normalize_theme_key(theme.get("slug") or theme.get("name"))
        if slug == key:
            return theme
    return None


def _apply_text_theme_to_args(args):
    theme = _resolve_text_theme(getattr(args, "text_theme", None))
    if not theme:
        return None

    def _apply_int(field: str, key: str):
        try:
            val = theme.get(key)
            if val is None:
                return
            setattr(args, field, int(val))
        except Exception:
            pass

    def _apply_str(field: str, key: str):
        val = theme.get(key)
        if val is None or str(val).strip() == "":
            return
        setattr(args, field, str(val))

    _apply_str("font", "font")
    _apply_int("font_size", "font_size")
    _apply_int("outline", "outline")
    _apply_str("font_color", "font_color")
    _apply_str("outline_color", "outline_color")
    _apply_str("thanks_color", "thanks_color")
    _apply_str("thanks_border_color", "thanks_border_color")

    font_file_name = theme.get("font_file_name")
    if font_file_name:
        candidate = (Path(__file__).resolve().parent / "fonts" / font_file_name).resolve()
        if candidate.exists():
            args.font_file = str(candidate)
        else:
            print(f"[WARN] Theme font file '{font_file_name}' missing in ./fonts; falling back to system fonts.")
            args.font_file = None

    return theme

def _separate_vocals_demucs(src_path: str, model: str, device: str) -> str:
    """
    Run Demucs to produce a vocals stem and return its path.
    Requires 'demucs' in PATH (pip install demucs).
    """
    dev = "cuda" if (device == "auto") else device
    if device == "auto":
        try:
            import torch  # type: ignore
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            dev = "cpu"

    # Demucs writes to ./separated/{model}/{basename}/vocals.wav
    # We send output to a temp dir so we can locate it deterministically.
    outdir = tempfile.mkdtemp(prefix="demucs_")
    cmd = [
        "demucs",
        "-n", model,
        "-d", dev,
        "--two-stems", "vocals",
        "-o", outdir,
        src_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        raise SystemExit("demucs is not installed or not in PATH. Install with: pip install demucs")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Demucs failed (code {e.returncode}). Try a different model or device.")

    # Find vocals.wav
    # Demucs layout: {outdir}/{model}/{track_basename}/vocals.wav
    pattern = os.path.join(outdir, model, "*", "vocals.wav")
    matches = glob.glob(pattern)
    if not matches:
        raise SystemExit(f"Demucs finished but no vocals.wav found at {pattern}")
    return matches[0]
# ---------- Text utils ----------

def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9'\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def load_lyrics(lyrics_path: str) -> List[str]:
    with open(lyrics_path, "r", encoding="utf-8") as f:
        raw = f.read()
    # keep empty lines as intentional pauses
    return [ln.rstrip() for ln in raw.splitlines()]

# ---------- Data ----------

@dataclass
class Word:
    text: str
    start: float
    end: float

@dataclass
class Seg:
    text: str
    start: float
    end: float

@dataclass
class MatchSpan:
    line_index: int
    start_word: int
    end_word: int
    score: float

# ---------- SRT helpers ----------

def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def write_srt(spans: List[Tuple[int, int, float, float, str]], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (_, _, start, end, text) in enumerate(spans, start=1):
            f.write(f"{i}\n")
            f.write(f"{srt_timestamp(start)} --> {srt_timestamp(end)}\n")
            f.write(((text or "").strip()) + "\n\n")
            
def write_srt_atomic(dst_path: str, srt_text: str):
    folder = os.path.dirname(dst_path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="align_", suffix=".srt", dir=folder)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(srt_text)
    shutil.move(tmp, dst_path)  # atomic on same filesystem
    
def ass_inject_fade(
    in_ass: str, out_ass: str,
    fade_in_ms=300, fade_out_ms=200
):
    fadetag = r"{\fad(" + f"{int(fade_in_ms)},{int(fade_out_ms)}" + r")}"
    with open(in_ass, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    out_lines = []
    for ln in lines:
        if ln.startswith("Dialogue:"):
            # If line already has a \fad tag, leave it
            if r"\fad(" not in ln:
                # Insert fadetag right before the text payload (after the 9th comma)
                # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
                parts = ln.split(",", 9)
                if len(parts) == 10:
                    parts[9] = fadetag + parts[9]
                    ln = ",".join(parts)
        out_lines.append(ln)

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(out_lines)

def _ass_header(
    width: int,
    height: int,
    font: str,
    font_size: int,
    outline: int,
    align: int,
    margin_v: int,
    *,
    primary: str = "&H00FFFFFF&",
    outline_col: str = "&H00202020&",
    back_col: str = "&H00000000&",
    secondary: str = "&H000000FF&",   # rarely used; OK default
    shadow: int = 0
) -> str:
    """
    Emit a *valid* ASS header with a single style 'Base'.
    Colors must be ASS format (&HAABBGGRR&). AA=00 is fully opaque.
    """
    # Sanity / clamps
    try: align = int(align)
    except: align = 2
    if align < 1 or align > 9: align = 2
    try: outline = int(outline)
    except: outline = 1
    if outline < 0: outline = 0
    try: margin_v = int(margin_v)
    except: margin_v = 20
    if margin_v < 0: margin_v = 0
    try: font_size = int(font_size)
    except: font_size = 48
    if font_size < 6: font_size = 6

    # NOTE: Commas are field separators; ASS does not support commas in font names.
    font_clean = str(font).replace(",", " ")

    script = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: {}".format(int(width)),
        "PlayResY: {}".format(int(height)),
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "YCbCr Matrix: TV.601",
        "",
        "[V4+ Styles]",
        # FULL, canonical format line (do not abbreviate)
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # One base style; Encoding=1 (default Western), BorderStyle=1 (outline), ScaleX/Y=100
        "Style: Base,{font},{size},{pri},{sec},{outl},{back},0,0,0,0,100,100,0,0,1,{ol},{sh},{al},20,20,{marv},1".format(
            font=font_clean,
            size=int(font_size),
            pri=primary,
            sec=secondary,
            outl=outline_col,
            back=back_col,
            ol=int(outline),
            sh=int(shadow),
            al=int(align),
            marv=int(margin_v),
        ),
        "",
        "[Events]",
        # FULL, canonical format line for events
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(script)


def srt_to_ass_with_fade(
    srt_path: str,
    out_ass_path: str,
    *,
    width=1920, height=1080,
    font="Arial", font_size=48, outline=1, align=2, margin_v=20,
    fade_in_ms=500, fade_out_ms=350,
    primary_color_ass=None,        # &HAABBGGRR&
    outline_color_ass=None         # &HAABBGGRR&
):
    """
    Convert SRT -> ASS with a correct header and \fad() on each Dialogue line.
    Color args must be ASS format (&HAABBGGRR&). If omitted, sane defaults are used.
    """
    import re

    # Load/normalize SRT
    with open(srt_path, "r", encoding="utf-8-sig", errors="replace") as f:
        s = f.read()
    s = re.sub(r"\r\n?|\n", "\n", s).strip()

    # Build header (no truncation!)
    primary = primary_color_ass or "&H00FFFFFF&"
    outline_col = outline_color_ass or "&H00202020&"
    header = _ass_header(
        width, height, font, font_size, outline, align, margin_v,
        primary=primary, outline_col=outline_col, shadow=0
    )

    with open(out_ass_path, "w", encoding="utf-8") as out:
        out.write(header + "\n")

        blocks = re.split(r"\n{2,}", s)
        ts_re = re.compile(
            r"^\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$"
        )

        def _fmt(h, m, s, ms):
            # ASS time = H:MM:SS.cs (centiseconds)
            return f"{int(h)}:{int(m):02d}:{int(s):02d}.{int(ms)//10:02d}"

        def _esc_ass_text(line: str) -> str:
            # Escape backslashes/braces; ASS uses literal commas as field separators,
            # but commas in TEXT are fine because everything after the 9th field is text.
            return (line
                    .replace("\\", r"\\")
                    .replace("{", r"\{")
                    .replace("}", r"\}"))

        for blk in blocks:
            lines = [ln for ln in blk.split("\n") if ln.strip() != ""]
            if not lines:
                continue

            # Drop numeric index line if present
            if lines and lines[0].strip().isdigit():
                lines = lines[1:]
                if not lines:
                    continue

            m = ts_re.match(lines[0])
            if not m:
                continue

            sh, sm, ss, sms, eh, em, es, ems = m.groups()
            start = _fmt(sh, sm, ss, sms)
            end   = _fmt(eh, em, es, ems)

            txt_lines = lines[1:]
            if not txt_lines:
                continue

            txt = r"\N".join(_esc_ass_text(ln) for ln in txt_lines)

            # Add fade tag; if there are already override tags, putting \fad() first is fine
            fad = r"{\fad(" + f"{int(fade_in_ms)},{int(fade_out_ms)}" + r")}"
            txt = fad + txt

            # Dialogue follows the Events Format line exactly
            out.write(f"Dialogue: 0,{start},{end},Base,,0,0,{int(margin_v)},,")
            out.write(txt)
            out.write("\n")

# ---------- Word alignment ----------


def _hybrid_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_n, b_n = normalize_text(a), normalize_text(b)
    char = SequenceMatcher(None, a_n, b_n).ratio()
    A, B = _tokset(a_n), _tokset(b_n)
    tok = (len(A & B) / (len(A | B) or 1)) if (A or B) else 0.0
    return 0.5 * char + 0.5 * tok

def greedy_align_lines_to_words(
    words: List[Word],
    lyric_lines: List[str],
    min_window: int = 2,
    max_window_extra: int = 6,
    early_break: int = 60,
    backtrack: int = 3,          # allow small backtrack window
    lookahead: int = 30,         # don’t scan the whole future
    jump_penalty: float = 0.002, # penalty per token distance from cur_idx
    accept_thresh: float = 0.55, # minimum confidence to “commit”
    strong_thresh: float = 0.78, # early-exit when we’re clearly good
) -> Tuple[List[MatchSpan], List[float]]:
    # pre-tokenize once
    asr_tokens = [normalize_text(w.text) for w in words]
    asr_tokens = [t for t in asr_tokens if t]  # drop empties
    n = len(asr_tokens)

    spans: List[MatchSpan] = []
    scores: List[float] = []
    cur_idx = 0

    if n == 0:
        # nothing to align against: return zero-length spans
        for li, _ in enumerate(lyric_lines):
            spans.append(MatchSpan(line_index=li, start_word=0, end_word=0, score=0.0))
            scores.append(0.0)
        return spans, scores

    for li, raw_line in enumerate(lyric_lines):
        norm_line = normalize_text(raw_line)

        # Blank lyric line -> zero-length span at current cursor (don’t advance)
        if not norm_line:
            spans.append(MatchSpan(line_index=li, start_word=cur_idx, end_word=cur_idx, score=1.0))
            scores.append(1.0)
            continue

        tgt_len = max(len(norm_line.split()), min_window)

        # Local search window centered on cur_idx
        start_lo = max(0, cur_idx - backtrack)
        start_hi = min(n - 1, cur_idx + lookahead)

        best_score = -1.0
        best_start = cur_idx
        best_end = cur_idx

        # Scan candidate starts within the local window
        for start in range(start_lo, start_hi + 1):
            if start - cur_idx > early_break and best_score >= 0.60:
                break

            win_min = max(min_window, tgt_len - 3)
            win_max = min(tgt_len + max_window_extra, n - start)

            joined = None
            for win in range(win_min, win_max + 1):
                end = start + win
                if joined is None:
                    joined = " ".join(asr_tokens[start:end]).strip()
                else:
                    joined = (joined + " " + asr_tokens[end - 1]).strip()

                if not joined:
                    continue

                base = _hybrid_score(joined, norm_line)
                dist = abs(start - cur_idx)
                score = base - jump_penalty * dist

                if score > best_score:
                    best_score, best_start, best_end = score, start, end

                if base >= strong_thresh and start >= cur_idx:
                    break

        # Commit if good enough; ensure span consumes at least one token
        if best_score >= accept_thresh:
            if best_end <= best_start:
                best_end = min(best_start + max(min_window, 1), n)
            spans.append(MatchSpan(line_index=li, start_word=best_start, end_word=best_end, score=best_score))
            scores.append(best_score)
            cur_idx = best_end
            if cur_idx >= n:
                last_end = cur_idx
                for lj in range(li + 1, len(lyric_lines)):
                    spans.append(MatchSpan(line_index=lj, start_word=last_end, end_word=last_end, score=0.0))
                    scores.append(0.0)
                break
        else:
            # Low-confidence fallback: still advance to avoid front-loading collapse
            fallback_win = max(min_window, 1)
            start_word = cur_idx
            end_word = min(cur_idx + fallback_win, n)
            spans.append(MatchSpan(line_index=li, start_word=start_word, end_word=end_word, score=max(0.0, best_score)))
            scores.append(max(0.0, best_score))
            cur_idx = end_word  # advance!

    # Ensure we have one span per lyric line
    last_end = spans[-1].end_word if spans else 0
    for li in range(len(spans), len(lyric_lines)):
        spans.append(MatchSpan(line_index=li, start_word=last_end, end_word=last_end, score=0.0))
        scores.append(0.0)

    return spans, scores


def word_spans_to_timed_lines(
    words: List[Word],
    lyric_lines: List[str],
    spans: List[MatchSpan],
    pad_s: float = 0.02,
    min_gap_s: float = 0.08,
    min_dur_s: float = 0.75,   # ensure a readable on-screen time
) -> List[Tuple[int, int, float, float, str]]:
    if not words:
        # emergency spread
        t = 0.0
        out = []
        for i, line in enumerate(lyric_lines):
            start, end = t, t + max(0.5, min_dur_s)
            out.append((i, i, start, end, line))
            t = end + 0.1
        return out

    word_starts = [float(w.start) for w in words]
    word_ends   = [float(w.end)   for w in words]
    total_dur   = word_ends[-1] if word_ends else 0.0

    out: List[Tuple[int, int, float, float, str]] = []
    last_end_time = 0.0
    n = len(words)

    for span in spans:
        li = span.line_index
        text = lyric_lines[li]

        # Defensive clamp
        s = max(0, min(span.start_word, n))
        e = max(0, min(span.end_word,   n))

        if s < n:
            if e <= s:
                # Zero-length token span: anchor to the token's time if we have it
                anchor = word_starts[s]
                start_time = max(anchor - pad_s, last_end_time + min_gap_s)
                end_time   = max(start_time + min_dur_s, start_time + min_gap_s)
            else:
                # Normal span: use first/last token times with padding and gaps
                start_idx = s
                end_idx   = max(e - 1, s)
                start_time = max(word_starts[start_idx] - pad_s, last_end_time + min_gap_s)
                end_time   = max(word_ends[end_idx]   + pad_s,   start_time   + min_dur_s)
        else:
            # Span points beyond last token: place a stub after last line
            start_time = max(last_end_time + min_gap_s, 0.0)
            end_time   = start_time + max(min_dur_s, min_gap_s)

        # Clamp within track bounds and keep monotonic
        start_time = min(start_time, max(total_dur - min_gap_s, 0.0))
        end_time   = min(max(end_time, start_time + min_gap_s), total_dur)

        out.append((len(out), li, start_time, end_time, text))
        last_end_time = end_time

    return out


# ---------- Segment alignment ----------


def _tokset(s: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9']+", normalize_text(s)))


def align_lines_to_segments(
    segs: List[Seg],
    lyric_lines: List[str],
    pad_s: float = 0.02,
    min_gap_s: float = 0.08,
    window_back: int = 2,          # allow small backtrack
    window_ahead: int = 6,         # look a bit ahead
    max_merge: int = 3,            # test merging up to 3 segs
    jump_penalty: float = 0.04,    # score penalty per seg distance from cur
    accept_thresh: float = 0.55,   # minimum score to accept a match
    strong_thresh: float = 0.78,   # break early if we exceed this
) -> List[Tuple[int, int, float, float, str]]:
    """
    Align each lyric line to the best-matching segment (or short run of segments).
    Returns list of (line_idx, line_idx, start, end, original_line).
    """
    if not segs:
        # Fallback: uniform spread by rough word count
        t = 0.0
        out = []
        for i, line in enumerate(lyric_lines):
            dur = max(0.5, len(normalize_text(line).split()) * 0.25)
            start, end = t, t + dur
            out.append((i, i, start, end, line))
            t = end + 0.1
        return out

    n = len(segs)
    total = segs[-1].end
    out: List[Tuple[int, int, float, float, str]] = []

    # Precompute normalized texts to save work
    seg_texts = [segs[k].text for k in range(n)]
    cur = 0
    last_end = 0.0

    for i, line in enumerate(lyric_lines):
        raw_line = line
        norm_line = normalize_text(line)

        # Handle blank lines: keep tiny beat
        if not norm_line:
            start = max(last_end + min_gap_s, last_end)
            end = min(start + max(min_gap_s, pad_s * 2), total)
            out.append((i, i, start, end, raw_line))
            last_end = end
            continue

        # Build a local search window around cur
        lo = max(0, cur - window_back)
        hi = min(n - 1, cur + window_ahead)

        best = None  # (score, j0, j1, start, end, text_joined)
        # Search single segments and short merges
        for j0 in range(lo, hi + 1):
            joined = ""
            j1_limit = min(n - 1, j0 + max_merge - 1)
            for j1 in range(j0, j1_limit + 1):
                # Concatenate segs[j0..j1] text
                if j1 == j0:
                    joined = seg_texts[j0]
                else:
                    joined = (joined + " " + seg_texts[j1]).strip()

                base_score = _hybrid_score(joined, norm_line)

                # Penalize jumps away from cur to prefer local matches,
                # but allow far jumps if they are significantly better.
                dist = min(abs(j0 - cur), 10)
                score = base_score - jump_penalty * dist

                # Early breakout if we have a strong local match
                if best is None or score > best[0]:
                    # Proposed time span is union of segs[j0..j1]
                    st = max(segs[j0].start - pad_s, last_end + min_gap_s)
                    en = max(segs[j1].end + pad_s, st + min_gap_s)
                    # Clamp to total and non-decreasing
                    st = min(st, max(total - min_gap_s, 0.0))
                    en = min(max(en, st + min_gap_s), total)
                    best = (score, j0, j1, st, en, joined)

                if base_score >= strong_thresh and j0 >= cur:
                    # Good enough and forward—stop expanding this line
                    break

        if best and best[0] >= accept_thresh:
            _, j0, j1, st, en, _ = best
            out.append((i, i, st, en, raw_line))
            last_end = en
            # Advance cursor conservatively to the end of the used span
            cur = min(j1 + 1, n - 1)
        else:
            # Low confidence fallback: place a tiny stub after last_end.
            # This avoids pulling future segments backward and causing drift.
            st = max(last_end + min_gap_s, last_end)
            en = min(st + max(min_gap_s, pad_s * 2), total)
            out.append((i, i, st, en, raw_line))
            last_end = en
            # Do not advance cur; keep searching near the current location

    return out


# ---------- Transcription ----------

def _do_transcribe(audio_path: str,
                   model_size: str,
                   language: Optional[str],
                   device: str,
                   compute_type: str,
                   vad_filter: str,
                   show_progress: bool = False):
    """
    Runs WhisperX and returns (words, segs, total_dur).
    """
    try:
        import whisperx
    except ImportError as exc:
        raise SystemExit("WhisperX is required for transcription. Install with `pip install -U whisperx`") from exc

    def _resolve_device(pref: str) -> str:
        if pref and pref != "auto":
            return pref
        try:
            import torch  # type: ignore
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    resolved_device = _resolve_device(device)
    compute = compute_type or ("float16" if resolved_device == "cuda" else "int8")
    lang_arg = None if not language or language.lower() == "auto" else language

    if vad_filter not in ("auto", "on", "off"):
        vad_filter = "auto"

    audio = whisperx.load_audio(audio_path)
    model = whisperx.load_model(model_size, device=resolved_device, compute_type=compute)
    transcribe_kwargs = {}
    if lang_arg:
        transcribe_kwargs["language"] = lang_arg
    if vad_filter != "off":
        transcribe_kwargs["vad_options"] = {}

    try:
        result = model.transcribe(audio, **transcribe_kwargs)
    except TypeError:
        # Older WhisperX builds do not accept vad_options; retry without it.
        transcribe_kwargs.pop("vad_options", None)
        result = model.transcribe(audio, **transcribe_kwargs)

    detected_lang = result.get("language") or lang_arg or "en"
    align_model, metadata = whisperx.load_align_model(
        language_code=detected_lang,
        device=resolved_device,
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        resolved_device,
        return_char_alignments=False,
    )

    segments = aligned.get("segments", [])
    words: List[Word] = []
    segs: List[Seg] = []

    for seg in segments:
        segs.append(Seg(
            text=str(seg.get("text", "")),
            start=float(seg.get("start", 0.0) or 0.0),
            end=float(seg.get("end", 0.0) or 0.0),
        ))
        for w in seg.get("words", []) or []:
            token = str(w.get("word", "")).strip()
            if not token:
                continue
            words.append(Word(
                text=token,
                start=float(w.get("start", 0.0) or 0.0),
                end=float(w.get("end", 0.0) or 0.0),
            ))

    total = max((seg.end for seg in segs), default=0.0)
    if not total:
        total = max((w.end for w in words), default=0.0)

    if show_progress and tqdm:
        try:
            desc = f"WhisperX ({model_size})"
            bar = tqdm(total=total or 1.0, desc=desc, unit="s", dynamic_ncols=True)
            bar.update(total or 1.0)
            bar.close()
        except Exception:
            pass

    print(f"[WhisperX] Segments: {len(segs)}  Words: {len(words)}  Model: {model_size} ({resolved_device})")
    return words, segs, total



def _needs_vad_retry(words: List[Word], segs: List[Seg], total_dur: float, lyric_lines: List[str]) -> bool:
    if total_dur <= 0:
        return True
    coverage = (segs[-1].end if segs else 0.0) / total_dur
    est_lyric_words = sum(len(normalize_text(l).split()) for l in lyric_lines if l.strip())
    token_ratio = (len(words) / max(est_lyric_words, 1)) if est_lyric_words else 0.0
    # Retry if we clearly ended early OR captured far fewer tokens than expected
    return (coverage < 0.65) or (token_ratio < 0.35)

# ---------- ffmpeg preview with progress ----------

def _probe_duration_seconds(audio_path: str) -> float:
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_path],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        data = json.loads(proc.stdout or "{}")
        return float(data.get("format", {}).get("duration", 0.0) or 0.0)
    except Exception:
        return 0.0
        
def _hex_to_rgb(hexstr: str) -> tuple[int,int,int]:
    s = hexstr.strip()
    if s.startswith("#"): s = s[1:]
    if len(s) == 3:
        s = "".join(c*2 for c in s)
    if len(s) < 6: s = s.ljust(6, "0")
    r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
    return r,g,b

def _hex_to_ffmpeg_color(hexstr: str, alpha: float|None=None) -> str:
    r,g,b = _hex_to_rgb(hexstr)
    # ffmpeg color parser accepts 0xRRGGBB and optional @alpha (0..1)
    base = f"0x{r:02X}{g:02X}{b:02X}"
    if alpha is not None:
        a = max(0.0, min(1.0, float(alpha)))
        return f"{base}@{a:.3f}"
    return base

def _hex_to_ass_bbggrr(hexstr: str, alpha_byte: str = "00") -> str:
    """
    Return ASS color in &HAABBGGRR& format (AA=alpha byte, '00' = fully opaque).
    Input hex like '#RRGGBB' or '#RGB'.
    """
    r, g, b = _hex_to_rgb(hexstr)
    aa = alpha_byte.upper() if re.match(r"^[0-9A-Fa-f]{2}$", alpha_byte or "") else "00"
    return f"&H{aa}{b:02X}{g:02X}{r:02X}&"

def _parse_res(force_res: str) -> tuple[int, int]:
    try:
        w, h = map(int, (force_res or "1920:1080").split(":"))
        return max(16, w), max(16, h)
    except Exception:
        return 1920, 1080

def _scale_ass_metrics(font_size: int, outline: int, margin_v: int, target_height: int) -> tuple[int, int, int]:
    """
    Scale ASS metrics relative to a smaller baseline so UI sizes map to
    visually readable text on 1080p output and still grow with resolution.
    """
    height = max(int(target_height), 1)
    # Treat UI font sizes as if 20 ~= 80px at 1080p, i.e. baseline height 270.
    BASELINE_HEIGHT = 270.0
    scale = height / BASELINE_HEIGHT

    def _scaled(value: int, minimum: int = 0) -> int:
        return max(minimum, int(round(max(value, 0) * scale)))

    scaled_font = max(6, int(round(max(font_size, 1) * scale)))
    scaled_outline = _scaled(outline, 0)
    scaled_margin = _scaled(margin_v, 0)
    return scaled_font, scaled_outline, scaled_margin

def _wrap_text_for_width(text: str, font_px: int, frame_width: int) -> str:
    """
    Very rough word-wrap helper for drawtext/ASS overlays. Approximates the
    number of characters that fit on a line using the font size so long titles
    or thanks messages wrap before hitting the video edges.
    """
    if not text:
        return text

    font_px = max(1, int(font_px))
    frame_width = max(1, int(frame_width))
    # Allow a tiny extra width budget so long words can stay on the same
    # line a bit longer without visibly touching the edges.
    effective_width = frame_width * 1.02
    approx_char_width = max(1.0, font_px * 0.55)
    max_chars = max(8, int(effective_width / approx_char_width))

    words = text.replace("\r", "").split()
    lines: list[str] = []
    current: list[str] = []
    cur_len = 0

    def flush_current():
        nonlocal current, cur_len
        if current:
            lines.append(" ".join(current))
            current = []
            cur_len = 0

    for word in words:
        wlen = len(word)
        if wlen >= max_chars:
            flush_current()
            for i in range(0, wlen, max_chars):
                lines.append(word[i:i + max_chars])
            continue

        add_len = wlen if not current else wlen + 1
        if cur_len + add_len > max_chars:
            flush_current()
        current.append(word)
        cur_len += add_len

    flush_current()
    collapsed = [line for line in lines if line]
    if len(collapsed) <= 1:
        return text

    longest = max(len(line) for line in collapsed) if collapsed else 0
    if longest * approx_char_width <= frame_width * 0.98:
        # If the widest line still fits comfortably, skip wrapping.
        return text

    return "\n".join(collapsed)

def _write_ass_overlay(
    ass_path: str,
    *,
    text: str,
    start_s: float,
    end_s: float,
    width: int,
    height: int,
    font: str,
    font_size: int,
    outline: int,
    align: int,
    margin_v: int,
    primary_color: str,
    outline_color: str,
    shadow: int = 0,
) -> None:
    """Write a minimal ASS file for a single overlay event."""
    def _fmt_time(t: float) -> str:
        if t < 0:
            t = 0.0
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        cs = int(round((t - int(t)) * 100))
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    font_clean = (font or "Arial").replace(",", " ")
    safe_text = (
        (text or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
         "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        (f"Style: Overlay,{font_clean},{font_size},{primary_color},&H000000FF,{outline_color},&H00000000,"
         f"0,0,0,0,100,100,0,0,1,{max(0, min(outline, 20))},{max(0, shadow)},{max(1, min(9, align))},"
         f"20,20,{max(0, margin_v)},1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{_fmt_time(start_s)},{_fmt_time(max(start_s, end_s))},Overlay,,0,0,{margin_v},,{safe_text}",
    ]

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header) + "\n")


def make_preview(
    image,
    audio,
    out,
    duration=None,
    burn_subs=None,
    *,
    theme="default",
    font=None,
    font_size=20,
    outline=1,
    align=2,
    margin_v=20,
    font_file=None,
    font_color="#FFFFFF",
    outline_color="#000000",
    thanks_color="#FFFFFF",
    thanks_border_color="#000000",
    title_text: str | None = None,
    title_seconds: float = 0.0,
    force_res: str = "1920:1080",
    thanks_text: str | None = "Thank You for Watching",
    thanks_seconds: float = 5.0,
    endcard_color: str = "#FFFFFF",
    endcard_border_color: str = "#000000",
    effect: str = "none",
    effect_strength: float = 0.08,
    effect_cycle: float = 12.0,
    effect_zoom: float | None = None,
    effect_pan: float | None = None,
    fps: int = 30,
    image_clip_seconds: float | None = None,
    image_fade_seconds: float | None = None,
    image_playback: str = "story",
    image_slots: List[Dict] | None = None,
):
    if isinstance(image, (list, tuple, set)):
        images = [str(x) for x in image if x]
    elif image:
        images = [str(image)]
    else:
        images = []

    images = [os.path.abspath(img) for img in images if img and os.path.exists(img)]
    if not images:
        return

    multi_mode = len(images) > 1

    # Pick font priority: font-file > font family > Arial fallback
    if font_file:
        font_choice = None   # we'll handle fontfile later
        chosen_fontsdir = os.path.dirname(font_file)
    elif font:
        font_choice = font
        fontfile_choice = None
    else:
        font_choice = font or "Arial"
        fontfile_choice = None

    """
    Create the still-image video at 1920x1080, optionally burning subtitles.
    - burn_subs: path to .srt or .ass; if provided, hard-burns into the video.
    - theme/font/... control SRT force_style; ASS files keep their own styling.
    """
    if not (audio and os.path.exists(audio)):
        return

    total_duration = float(duration) if duration else _probe_duration_seconds(audio)
    playback_mode = (image_playback or "story").strip().lower()
    if playback_mode not in ("story", "loop"):
        playback_mode = "story"

    chosen_font_name = font or "Arial"
    if font_file:
        internal = _font_fullname_or_family(font_file)
        fallback_name = os.path.splitext(os.path.basename(font_file))[0] or chosen_font_name
        chosen_font_name = internal or fallback_name or "Arial"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    post_input_opts = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-progress", "pipe:1",
        "-c:a", "aac", "-b:a", "192k",
    ]

    clip_override = float(image_clip_seconds) if image_clip_seconds and image_clip_seconds > 0 else None
    fade_override = float(image_fade_seconds) if image_fade_seconds and image_fade_seconds > 0 else None
    clip_duration = None
    fade_duration = None
    sequence = list(images)
    audio_input_idx = None

    if playback_mode == "story" and image_slots:
        # Inputs added later once story slots are resolved
        pass
    elif multi_mode:
        est_total = total_duration if total_duration and total_duration > 0 else len(sequence) * 6.0
        auto_clip = est_total / len(sequence) if sequence else 6.0
        clip_duration = max(1.0, clip_override or auto_clip)
        fade_duration = fade_override if fade_override is not None else min(1.5, max(0.5, clip_duration * 0.25))
        fade_duration = min(fade_duration, max(clip_duration - 0.1, 0.1))
        if playback_mode == "loop" and clip_duration > 0 and sequence:
            if total_duration and total_duration > 0:
                needed = max(len(sequence), int(math.ceil(total_duration / clip_duration)))
            else:
                needed = len(sequence) * 2
            sequence = [sequence[i % len(sequence)] for i in range(needed)]
        for img in sequence:
            cmd += ["-loop", "1", "-t", f"{clip_duration:.3f}", "-i", img]
        audio_input_idx = len(sequence)
    else:
        cmd += ["-loop", "1", "-i", sequence[0]]
        audio_input_idx = 1

    # Normalize/parse target resolution once
    target_w, target_h = _parse_res(force_res)
    force_res = f"{target_w}:{target_h}"
    orig_size = f"{target_w}x{target_h}"
    scaled_font_size, scaled_outline, scaled_margin_v = _scale_ass_metrics(
        font_size, outline, margin_v, target_h
    )

    # Build -vf filter chain: **scale first**, then subtitles
    vf_parts = []
    if not multi_mode and playback_mode != "story":
        vf_parts.append(f"scale={force_res}")
    
    # Optional visual effect (e.g., slow zoom)
    if effect and effect != "none":
        try:
            eff = build_effect_filter(
                effect=effect,
                force_res=force_res,
                fps=int(fps),
                strength=float(effect_strength),
                cycle_s=float(effect_cycle),
                zoom=effect_zoom,
                pan=effect_pan,
            )  # e.g. "zoompan=...:s=1920x1080:fps=30"
            if eff:
                vf_parts.append(eff)
        except Exception as e:
            print(f"[WARN] effect build failed ({effect}): {e}")


    # --- Build subtitles video filter ---
    if burn_subs:
        ext = os.path.splitext(burn_subs)[1].lower()
        # escaped paths for filtergraph
        subs_esc = _esc_filter_path_win(burn_subs) if os.name == "nt" else burn_subs.replace("'", r"\'")
        # Stage to a short, UTF-8, flushed file Windows/libass will open reliably
        try:
            staged = safe_temp_copy_for_filters(burn_subs, suffix=ext if ext in (".srt", ".ass") else ".srt")
        except Exception as e:
            print(f"[WARN] Failed to stage subtitles: {e}")
            staged = burn_subs  # fall back

        # escaped path for filtergraph
        staged_esc = _esc_filter_path_win(staged) if os.name == "nt" else staged.replace("'", r"\'")

        def _fontsdir_opt(ffile: str | None) -> str:
            # If a specific font file is provided, point to its folder.
            if ffile:
                fdir = os.path.dirname(ffile)
                fdir_esc = _esc_filter_dir_win(fdir) if os.name == "nt" else fdir.replace("'", r"\'")
                return f":fontsdir='{fdir_esc}'"

            # Otherwise, on Windows, point libass at the system Fonts folder so Arial/etc resolve.
            if os.name == "nt":
                win_fonts = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
                if os.path.isdir(win_fonts):
                    fdir_esc = _esc_filter_dir_win(win_fonts)
                    return f":fontsdir='{fdir_esc}'"

            # No fontsdir hint on non-Windows without an explicit font file.
            return ""


         # Always use subtitles= so we can pass fontsdir + original_size
        if ext == ".srt":
            # Convert SRT -> ASS with your chosen font + colors + fade, then stage again
            fd, fade_ass = tempfile.mkstemp(prefix="subs_fade_", suffix=".ass"); os.close(fd)
            srt_to_ass_with_fade(
                staged, fade_ass,
                width=target_w, height=target_h,
                font=chosen_font_name, font_size=scaled_font_size,
                outline=scaled_outline, align=align, margin_v=scaled_margin_v,
                fade_in_ms=300, fade_out_ms=300,
                primary_color_ass=_hex_to_ass_bbggrr(font_color),
                outline_color_ass=_hex_to_ass_bbggrr(outline_color),
            )
            # Re-stage the ASS we just created to the short temp dir too
            try:
                staged_ass = safe_temp_copy_for_filters(fade_ass, suffix=".lyrics.ass")
            except Exception:
                staged_ass = fade_ass
            fade_esc = _esc_filter_path_win(staged_ass) if os.name == "nt" else staged_ass.replace("'", r"\'")
            vf_parts.append(
                f"subtitles=filename='{fade_esc}':charenc=UTF-8:original_size={orig_size}"
                f"{_fontsdir_opt(font_file)}"
            )

        elif ext == ".ass":
            vf_parts.append(f"subtitles=filename='{staged_esc}':charenc=UTF-8:original_size={orig_size}{_fontsdir_opt(font_file)}")
        else:
            # Fallback
            vf_parts.append(f"subtitles=filename='{staged_esc}':charenc=UTF-8:original_size={orig_size}{_fontsdir_opt(font_file)}")

    if title_text and title_seconds and title_seconds > 0:
        TITLE_FONT_RATIO = 0.14  # ~14% of the video height
        TITLE_FONT_MIN = 48
        TITLE_FONT_MAX = int(target_h * 0.35)  # don't exceed ~35% of height
        TITLE_FONT_SIZE = int(target_h * TITLE_FONT_RATIO)
        TITLE_FONT_SIZE = max(TITLE_FONT_MIN, min(TITLE_FONT_MAX, TITLE_FONT_SIZE))
        wrapped_title = _wrap_text_for_width(title_text, TITLE_FONT_SIZE, target_w)
        fd, tmp_title = tempfile.mkstemp(prefix="title_overlay_", suffix=".ass"); os.close(fd)
        _write_ass_overlay(
            tmp_title,
            text=wrapped_title,
            start_s=0.0,
            end_s=float(title_seconds),
            width=target_w,
            height=target_h,
            font=chosen_font_name,
            font_size=TITLE_FONT_SIZE,
            outline=max(1, scaled_outline),
            align=5,  # middle-center
            margin_v=scaled_margin_v,
            primary_color=_hex_to_ass_bbggrr(font_color),
            outline_color=_hex_to_ass_bbggrr(outline_color, alpha_byte="C0"),
        )
        try:
            staged_title = safe_temp_copy_for_filters(tmp_title, suffix=".ass")
        except Exception:
            staged_title = tmp_title
        title_esc = _esc_filter_path_win(staged_title) if os.name == "nt" else staged_title.replace("'", r"\'")
        vf_parts.append(
            f"subtitles=filename='{title_esc}':charenc=UTF-8:original_size={orig_size}{_fontsdir_opt(font_file)}"
        )
            
    if thanks_text and (thanks_seconds or 0) > 0:
        audio_len = float(_probe_duration_seconds(audio) or 0.0)
        title_pad = 0.0
        end_t   = audio_len + title_pad
        start_t = max(0.0, end_t - float(thanks_seconds))

        thanks_font_px = max(50, int(scaled_font_size * 2.2))
        wrapped_thanks = _wrap_text_for_width(thanks_text, thanks_font_px, target_w)
        fd, tmp_thanks = tempfile.mkstemp(prefix="thanks_overlay_", suffix=".ass"); os.close(fd)
        _write_ass_overlay(
            tmp_thanks,
            text=wrapped_thanks,
            start_s=start_t,
            end_s=end_t,
            width=target_w,
            height=target_h,
            font=chosen_font_name,
            font_size=thanks_font_px,
            outline=max(1, scaled_outline),
            align=5,
            margin_v=scaled_margin_v,
            primary_color=_hex_to_ass_bbggrr(thanks_color),
            outline_color=_hex_to_ass_bbggrr(thanks_border_color, alpha_byte="C0"),
        )
        try:
            staged_thanks = safe_temp_copy_for_filters(tmp_thanks, suffix=".ass")
        except Exception:
            staged_thanks = tmp_thanks
        thanks_esc = _esc_filter_path_win(staged_thanks) if os.name == "nt" else staged_thanks.replace("'", r"\'")
        vf_parts.append(
            f"subtitles=filename='{thanks_esc}':charenc=UTF-8:original_size={orig_size}{_fontsdir_opt(font_file)}"
        )

    filter_lines = []
    vf_chain = ",".join(vf_parts)
    story_applied = False
    if playback_mode == "story" and image_slots:
        concat_inputs = ""
        last_end_time = 0.0
        project_dir = os.path.dirname(os.path.abspath(out))
        story_fade_override = fade_override

        def _slot_times(entry):
            start = entry.get("start")
            end = entry.get("end")
            try:
                start = float(start) if start is not None else None
            except Exception:
                start = None
            try:
                end = float(end) if end is not None else None
            except Exception:
                end = None
            if start is None and end is not None:
                start = max(0.0, end - 0.1)
            if start is None:
                start = 0.0
            if end is None:
                end = start
            if end < start:
                start, end = end, start
            return start, end

        def _slot_fade(duration):
            fade_val = story_fade_override
            if fade_val is None:
                if duration <= 0.25:
                    return None
                fade_val = min(1.5, max(0.5, duration * 0.25))
            fade_val = max(0.0, min(fade_val, max(duration - 0.05, 0.0)))
            return fade_val if fade_val > 0 else None

        def _append_slot(src_idx, dst_label, duration, has_next):
            safe_duration = max(0.1, duration)
            chain = [
                f"[{src_idx}]trim=start=0:end={safe_duration:.3f},setpts=PTS-STARTPTS",
                f"scale={force_res}",
                "setsar=1",
                "format=yuv420p",
            ]
            fade_sec = _slot_fade(safe_duration)
            if fade_sec:
                chain.append(f"fade=t=in:st=0:d={fade_sec:.3f}")
                if has_next:
                    chain.append(
                        f"fade=t=out:st={max(safe_duration - fade_sec, 0.1):.3f}:d={fade_sec:.3f}"
                    )
            filter_lines.append(",".join(chain) + f"[{dst_label}]")

        def _resolve_slot_image_path(raw_path: str | None) -> str | None:
            if not raw_path:
                return None
            candidates = []
            norm = str(raw_path).strip()
            if not norm:
                return None
            if os.path.isabs(norm):
                candidates.append(norm)
            else:
                candidates.append(os.path.abspath(norm))
                candidates.append(os.path.abspath(os.path.join(project_dir, norm)))
            seen: set[str] = set()
            for cand in candidates:
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                if os.path.exists(cand):
                    return cand
            return None

        fallback_cursor = 0

        def _consume_fallback() -> str | None:
            nonlocal fallback_cursor
            if not images:
                return None
            idx = min(fallback_cursor, len(images) - 1)
            if fallback_cursor < len(images) - 1:
                fallback_cursor += 1
            return images[idx]

        resolved_slots: list[dict[str, float]] = []
        if playback_mode == "story" and image_slots:
            for slot in image_slots:
                start_time, end_time = _slot_times(slot)
                img_path = _resolve_slot_image_path(slot.get("image_path"))
                if not img_path:
                    img_path = _consume_fallback()
                if not img_path:
                    continue
                resolved_slots.append(
                    {
                        "path": img_path,
                        "start": start_time,
                        "end": end_time,
                    }
                )

        if total_duration and resolved_slots:
            try:
                resolved_slots[-1]["end"] = max(float(total_duration), float(resolved_slots[-1]["end"] or 0.0))
            except Exception:
                resolved_slots[-1]["end"] = float(total_duration)

        if resolved_slots:
            path_to_index: dict[str, int] = {}
            ordered_paths: list[str] = []
            for entry in resolved_slots:
                source = entry["path"]
                if source not in path_to_index:
                    path_to_index[source] = len(ordered_paths)
                    ordered_paths.append(source)
            for source in ordered_paths:
                cmd += ["-loop", "1", "-i", source]
            cmd += ["-i", audio]
            audio_input_idx = len(ordered_paths)
            cmd += post_input_opts
            for idx in range(len(ordered_paths)):
                filter_lines.append(f"[{idx}:v]scale={force_res},fps={int(fps)},setsar=1,format=yuv420p[v{idx}]")

            # Build a timeline: each slot holds until the next starts; gaps hold previous image
            resolved_slots.sort(key=lambda s: (s.get("start") or 0.0))
            timeline: list[dict[str, float | int]] = []
            prev_end = 0.0

            def _append_segment(idx: int, dur: float):
                if dur <= 0 or idx is None:
                    return
                if timeline and timeline[-1]["input_idx"] == idx:
                    timeline[-1]["duration"] += dur
                else:
                    timeline.append({"input_idx": idx, "duration": dur})

            if resolved_slots:
                first_idx = path_to_index[resolved_slots[0]["path"]]
                last_input_idx = first_idx
                first_start = float(resolved_slots[0].get("start") or 0.0)
                if first_start > 0:
                    _append_segment(first_idx, first_start)
                    prev_end = first_start
            else:
                last_input_idx = None

            for entry in resolved_slots:
                input_idx = path_to_index[entry["path"]]
                start = float(entry.get("start") or 0.0)
                end = float(entry.get("end") or start)
                if end < start:
                    end = start

                gap = max(0.0, start - prev_end)
                if gap > 0 and last_input_idx is not None:
                    _append_segment(last_input_idx, gap)
                    prev_end += gap

                duration = max(0.1, end - max(prev_end, start))
                _append_segment(input_idx, duration)
                prev_end = end
                last_input_idx = input_idx

            if total_duration and last_input_idx is not None and prev_end < total_duration:
                hold_duration = max(0.1, float(total_duration) - prev_end)
                _append_segment(last_input_idx, hold_duration)
                prev_end = float(total_duration)

            for idx, chunk in enumerate(timeline):
                has_more = idx < len(timeline) - 1
                label = f"slot{idx}"
                _append_slot(f"v{chunk['input_idx']}", label, chunk["duration"], has_more)
                concat_inputs += f"[{label}]"

            concat_count = len(timeline)

            filter_lines.append(f"{concat_inputs}concat=n={concat_count}:v=1:a=0[slides]")

            final_label = "slides"
        if vf_chain:
            filter_lines.append(f"[{final_label}]{vf_chain}[vout]")
        else:
            filter_lines.append(f"[{final_label}]format=yuv420p[vout]")
        cmd += ["-filter_complex", ";".join(filter_lines), "-map", "[vout]", "-map", f"{audio_input_idx}:a"]
        story_applied = True

    if story_applied:
        pass
    elif multi_mode and sequence:
        cmd += ["-i", audio]
        if audio_input_idx is None:
            audio_input_idx = len(sequence)
        cmd += post_input_opts
        for idx in range(len(sequence)):
            fades = [f"fade=t=in:st=0:d={fade_duration:.3f}"]
            fade_out_needed = (playback_mode == "loop") or (idx < len(sequence) - 1)
            if fade_out_needed and clip_duration and fade_duration:
                fades.append(f"fade=t=out:st={max(clip_duration - fade_duration, 0.1):.3f}:d={fade_duration:.3f}")
            base = (
                f"[{idx}:v]scale={force_res},fps={int(fps)},setsar=1,"
                f"format=yuv420p,{','.join(fades)}[slide{idx}]"
            )
            filter_lines.append(base)
        concat_inputs = "".join(f"[slide{i}]" for i in range(len(sequence)))
        filter_lines.append(f"{concat_inputs}concat=n={len(sequence)}:v=1:a=0[slides]")
        final_label = "slides"
        if total_duration and total_duration > 0:
            filter_lines.append(f"[{final_label}]trim=duration={total_duration:.3f},setpts=PTS-STARTPTS[{final_label}t]")
            final_label = f"{final_label}t"
        if vf_chain:
            filter_lines.append(f"[{final_label}]{vf_chain}[vout]")
        else:
            filter_lines.append(f"[{final_label}]format=yuv420p[vout]")
        cmd += ["-filter_complex", ";".join(filter_lines), "-map", "[vout]", "-map", f"{audio_input_idx}:a"]
    else:
        cmd += ["-i", audio]
        if audio_input_idx is None:
            audio_input_idx = 1
        if vf_chain:
            # Keep -vf after all inputs so ffmpeg treats it as an output filter, not an input option.
            cmd += ["-vf", vf_chain]
        cmd += post_input_opts

    cmd += ["-shortest", out]

    # Progress handling
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1) as proc:
            bar = tqdm(total=total_duration, desc="Encoding preview", unit="s") if (tqdm and total_duration > 0) else None
            last = 0.0
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = (line or "").strip()
                    if line.startswith("out_time_ms="):
                        try:
                            sec = float(line.split("=", 1)[1]) / 1_000_000.0
                            if bar and sec > last:
                                bar.update(sec - last)
                            last = sec
                        except Exception:
                            pass
                    elif line == "progress=end":
                        break
            ret = proc.wait()
            if bar:
                if last < total_duration:
                    bar.update(max(total_duration - last, 0.0))
                bar.close()
            if ret != 0 and proc.stderr:
                err = proc.stderr.read()
                if err:
                    sys.stderr.write(err)
    except Exception:
        pass
        
def _looks_piled_up(timed_lines):
    if not timed_lines: return True
    durs = [en-st for (_,_,st,en,_) in timed_lines]
    short = sum(1 for d in durs if d < 0.25)
    uniq_st = len({round(st,2) for (_,_,st,_,_) in timed_lines})
    return (short > 0.2*len(timed_lines)) or (uniq_st < 0.5*len(timed_lines))
    
def _ass_bgr_from_hsv(h, s, v):
    """Return ASS BGR color like &HBBGGRR& from HSV 0-1 floats."""
    # HSV -> RGB (0-255)
    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = int(255 * v * (1.0 - s))
    q = int(255 * v * (1.0 - f * s))
    t = int(255 * v * (1.0 - (1.0 - f) * s))
    v255 = int(255 * v)
    i %= 6
    if i == 0: r, g, b = v255, t, p
    elif i == 1: r, g, b = q, v255, p
    elif i == 2: r, g, b = p, v255, t
    elif i == 3: r, g, b = p, q, v255
    elif i == 4: r, g, b = t, p, v255
    else: r, g, b = v255, p, q
    # ASS is &HBBGGRR&
    return f"&H{b:02X}{g:02X}{r:02X}&"

def _parse_srt_timestamp(ts):
    # "HH:MM:SS,mmm"
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    t = (int(h) * 3600) + (int(m) * 60) + int(s) + (int(ms) / 1000.0)
    return t

def shift_srt_timestamps(in_path: str, out_path: str, delta_s: float) -> None:
    """
    Create a copy of `in_path` with all cue times shifted by +delta_s seconds.
    Negative times are clamped to 0.
    """
    if not in_path or not os.path.exists(in_path) or abs(delta_s) < 1e-6:
        # No-op: just copy if needed
        if in_path and out_path and os.path.abspath(in_path) != os.path.abspath(out_path):
            try:
                shutil.copyfile(in_path, out_path)
            except Exception:
                pass
        return

    def _clamp(x): return max(0.0, x)
    def _fmt(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    out_lines = []
    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\d\d:\d\d:\d\d,\d{3})\s*-->\s*(\d\d:\d\d:\d\d,\d{3})", line.strip())
            if m:
                # parse → shift → format
                def _parse(ts):
                    h, m2, rest = ts.split(":")
                    s2, ms = rest.split(",")
                    return int(h)*3600 + int(m2)*60 + int(s2) + int(ms)/1000.0
                st = _clamp(_parse(m.group(1)) + delta_s)
                en = _clamp(_parse(m.group(2)) + delta_s)
                out_lines.append(f"{_fmt(st)} --> {_fmt(en)}\n")
            else:
                out_lines.append(line)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)

def _format_ass_time(t):
    # ASS uses H:MM:SS.cs (centiseconds)
    if t < 0: t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))  # centiseconds
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

def _iterate_srt_events(srt_path):
    """
    Robust SRT iterator: yields (idx, start, end, text).
    - Handles CRLF/CR/LF newlines.
    - Accepts ',' or '.' milliseconds.
    - Tolerates optional numeric index line.
    - Ignores empty/garbage blocks gracefully.
    """
    import re, html

    # Read with BOM handling, then normalize newlines
    with open(srt_path, "r", encoding="utf-8-sig", errors="replace") as f:
        blob = f.read()
    blob = re.sub(r"\r\n?|\n", "\n", blob)            # CRLF/CR -> LF
    blob = blob.strip()

    # Split on blank lines (two or more LFs)
    blocks = re.split(r"\n{2,}", blob)

    # Timestamp: allow comma or dot millis; be liberal with spaces around arrow
    ts_re = re.compile(
        r"^\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*[-\u2012-\u2015]*>\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*$"
    )

    for blk in blocks:
        if not blk.strip():
            continue
        lines = [ln.strip("\ufeff") for ln in blk.split("\n") if ln.strip() != ""]
        if not lines:
            continue

        # First line may be a numeric index.
        has_index = bool(re.match(r"^\d+$", lines[0]))
        time_line_i = 1 if has_index and len(lines) > 1 else 0
        if time_line_i >= len(lines):
            continue

        m = ts_re.match(lines[time_line_i])
        if not m:
            # Not an SRT timecode line; skip this block
            continue

        # Parse timestamps (support '.' or ',' millis)
        s_raw, e_raw = m.group(1), m.group(2)
        start = _parse_srt_timestamp(s_raw.replace(".", ","))
        end   = _parse_srt_timestamp(e_raw.replace(".", ","))

        # Remaining lines are text
        text_start = time_line_i + 1
        text_lines = lines[text_start:] if text_start < len(lines) else []
        # Basic HTML entity unescape; keep formatting otherwise
        text = html.unescape("\n".join(text_lines)).strip()

        # Provide an index if present; else None
        idx = int(lines[0]) if has_index else None
        yield (idx, start, end, text)


def build_rainbow_ass_from_srt(
    srt_path: str,
    ass_path: str,
    width: int = 1920,
    height: int = 1080,
    font: str = "Arial",
    font_size: int = 40,
    outline: int = 2,
    align: int = 2,
    margin_v: int = 20,
    cycle_seconds: float = 0.5,
    saturation: float = 1.0,
    brightness: float = 1.0,
    phase_stagger: float = 0.0,
):
    """
    Create an ASS file where each SRT event's primary color cycles through the rainbow.
    We keyframe \1c with small steps across the event duration for smooth animation.
    """
    # Header
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Primary=white, Outline=dark gray. Tweak here as you like.
        f"Style: Rainbow,{font},{font_size},&H00FFFFFF,&H000000FF,&H00202020,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,{max(0, min(outline, 10))},0,{max(1, min(9, align))},80,80,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events = []
    # Generate events
    line_index = 0
    for i, (st, en, text) in enumerate(_iterate_srt_events(srt_path)):
        if en <= st:
            en = st + 0.5
        dur = en - st
        # How many color steps? ~10 steps per cycle, minimum 4
        steps_per_cycle = max(10, int(10 * (cycle_seconds / 3.0)))
        total_steps = max(4, int((dur / cycle_seconds) * steps_per_cycle))
        # If very short, still animate a couple steps
        total_steps = max(total_steps, 6)

        # Start hue with optional phase stagger (per event)
        phase = (i * phase_stagger) % cycle_seconds if phase_stagger > 0 else 0.0
        # Build \t() transitions from step to step
        tags = []
        # Initial color at t=0
        h0 = ((0.0 + phase) % cycle_seconds) / cycle_seconds
        c0 = _ass_bgr_from_hsv(h0, max(0.0, min(1.0, saturation)), max(0.0, min(1.0, brightness)))
        tags.append(rf"{{\1c{c0}\an{align}\q2}}")  # \an for alignment, \q2 no line wrap

        # Distribute steps uniformly over the event
        for step in range(1, total_steps + 1):
            t0 = (dur * (step - 1) / (total_steps))
            t1 = (dur * (step) / (total_steps))
            h = (((t1 + phase) % cycle_seconds) / cycle_seconds)
            c = _ass_bgr_from_hsv(h, max(0.0, min(1.0, saturation)), max(0.0, min(1.0, brightness)))
            # ASS \t uses milliseconds relative to event start
            tags.append(rf"{{\t({int(t0*1000)},{int(t1*1000)},\1c{c})}}")

        # Escape line breaks for ASS
        ass_text = (text or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
        ass_text = ass_text.replace("\r", "").replace("\n", r"\N")

        dialogue = (
            f"Dialogue: 0,{_format_ass_time(st)},{_format_ass_time(en)},"
            f"Rainbow,,0,0,{margin_v},,{' '.join(tags)}{ass_text}"
        )
        events.append(dialogue)
        line_index += 1

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + events) + "\n")
        
def build_credits_ass_from_txt(
    lyrics_txt_path: str,
    ass_path: str,
    width: int = 1920,
    height: int = 1080,
    font: str = "Arial",
    font_size: int = 32,
    outline: int = 1,
    align: int = 8,             # top-center
    margin_v: int = 60,
    line_spacing: float = 1.2,  # ~1.1–1.4 looks good
    scroll_pad: int = 120,      # px offscreen padding
    duration_seconds: float = 180.0,
):
    """
    Make a single ASS event that scrolls all lyrics upward like end credits.
    We place the block below the frame and move it above the top over the song duration.
    """
    # Read raw lines (preserve intentional blank lines)
    with open(lyrics_txt_path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\r\n") for ln in f.readlines()]

    # Compose the body with ASS line breaks
    body = "\\N".join(line.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}") for line in lines)

    # Estimate total block height to set the travel distance
    est_line_px = int(font_size * line_spacing)
    total_text_height = max(est_line_px * max(1, len(lines)), est_line_px)  # at least one line

    # Start well below the bottom, end well above the top
    start_y = height + scroll_pad
    end_y   = - total_text_height - scroll_pad
    cx      = width // 2

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
         "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        f"Style: Credits,{font},{font_size},&H00FFFFFF,&H000000FF,&H00202020,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,{max(0,min(outline,10))},0,{max(1,min(9,align))},80,80,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]


    def _ass_time(t):
        if t < 0: t = 0
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
        cs = int(round((t - int(t)) * 100))
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    # One long dialogue covering the full duration; \pos sets initial anchor, \move handles the scroll
    dlg = (f"Dialogue: 0,{_ass_time(0.0)},{_ass_time(max(0.5, duration_seconds))},"
           f"Credits,,0,0,{margin_v},,"
           f"{{\\an{align}\\q2\\pos({cx},{start_y})\\move({cx},{start_y},{cx},{end_y})}}{body}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + [dlg]) + "\n")

def _ass_force_style_for_theme(theme: str, font: str, font_size: int, outline: int, align: int, margin_v: int) -> str:
    """
    Returns an ASS force_style string (without surrounding quotes) for ffmpeg's subtitles filter.
    Colors are ASS BGR format: &HBBGGRR&.
    """
    if theme == "blood":
        primary = "&H0000B0&"      # deep red (#B00000)
        outline_col = "&H00FFFFFF&"  # white outline
        shadow = 0
    else:
        primary = "&H00FFFFFF&"    # white
        outline_col = "&H00202020&"  # dark gray
        shadow = 0

    return (
        f"FontName={font},Fontsize={font_size},"
        f"PrimaryColour={primary},OutlineColour={outline_col},"
        f"Outline={max(0, min(outline, 10))},Shadow={shadow},"
        f"Alignment={align},MarginV={margin_v}"
    )

import tempfile, subprocess, shlex, sys, os

def _ffmpeg(*args):
    """
    Tiny wrapper that raises on failure but also **prints stderr** so we can see what's wrong.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *args]
    print(f"[INFO] Running ffmpeg from cwd: {os.getcwd()}")
    try:
        result = subprocess.run(
            cmd,
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,  # raise on non-zero
        )
        return result
    except subprocess.CalledProcessError as e:
        # make ffmpeg's error visible in our logs
        sys.stderr.write(f"[FFMPEG ERROR] command: {' '.join(cmd)}\n")
        if e.stderr:
            sys.stderr.write(e.stderr + "\n")
        raise

def _make_prep_wav(src_path: str, mode: str) -> str:
    """
    Make a mono, speech-friendly WAV for ASR. `mode` choices:
      - "off":        no filtering, just resample mono 16k
      - "center":     collapse L/R to center, then light speech EQ + dynaudnorm
      - "bandpass":   narrow speech band (HPF/LPF) + dynaudnorm
      - "nr":         light noise reduction using afftdn (if available) + dynaudnorm
      - "speech":     general speech EQ + dynaudnorm
      - "auto":       same as "speech" for now
    """
    if not os.path.exists(src_path):
        raise SystemExit(f"[ERROR] Input audio not found: {src_path}")

    mode = (mode or "speech").lower()
    fd, tmpwav = tempfile.mkstemp(prefix="prep_", suffix=".wav"); os.close(fd)

    # Build an -af chain. Keep it simple and robust across ffmpeg builds.
    af = None
    if mode in ("off",):
        af = None
    elif mode in ("auto", "speech"):
        # mild speech emphasis + leveling
        af = "highpass=f=100,lowpass=f=6000,dynaudnorm=f=150:g=31"
    elif mode == "center":
        # collapse to mono from center, then speech emphasis
        af = "pan=mono|c0=0.5*FL+0.5*FR,highpass=f=100,lowpass=f=6000,dynaudnorm=f=150:g=31"
    elif mode == "bandpass":
        # narrower band for intelligibility in noisy music
        af = "highpass=f=180,lowpass=f=3800,dynaudnorm=f=150:g=31"
    elif mode == "nr":
        # light frequency-domain denoise if available; will fallback on error
        af = "afftdn=nr=12,highpass=f=120,lowpass=f=6000,dynaudnorm=f=150:g=31"
    else:
        # unknown mode → safe default
        af = "highpass=f=100,lowpass=f=6000,dynaudnorm=f=150:g=31"

    try:
        if af:
            _ffmpeg("-y", "-i", src_path, "-ac", "1", "-af", af, "-ar", "16000", tmpwav)
        else:
            _ffmpeg("-y", "-i", src_path, "-ac", "1", "-ar", "16000", tmpwav)
    except Exception:
        # ✅ robust fallback: plain mono resample (works on most builds)
        _ffmpeg("-y", "-i", src_path, "-ac", "1", "-ar", "16000", tmpwav)

    # sanity check
    if (not os.path.exists(tmpwav)) or os.path.getsize(tmpwav) < 1000:
        raise SystemExit("[ERROR] Preprocessed WAV missing/too small. Check ffmpeg and input audio.")
    return tmpwav

def read_audio_title(audio_path: str) -> str | None:
    """Return embedded title metadata from a saved audio file, if present."""
    if not audio_path:
        return None

    try:
        path = Path(audio_path)
    except Exception:
        return None

    if not path.exists():
        return None

    # 1) MP3 fast path via EasyID3
    if EasyID3 is not None:
        try:
            ez = EasyID3(str(path))
            title = ez.get("title", [None])[0]
            if title:
                title = str(title).strip()
                if title:
                    return title
        except Exception:
            pass

    # 2) Raw ID3 frames (covers many MP3 variants)
    if ID3 is not None:
        try:
            id3 = ID3(str(path))
            for key in ("TIT2", "TT2"):
                frame = id3.get(key)
                if frame is None:
                    continue
                texts = getattr(frame, "text", None)
                if not texts:
                    continue
                text = str(texts[0]).strip()
                if text:
                    return text
        except Exception:
            pass

    # 3) Generic mutagen loader (MP4/M4A, FLAC/Vorbis, etc.)
    if MutagenFile is not None:
        try:
            audio = MutagenFile(str(path))
            tags = getattr(audio, "tags", None)
            if not tags:
                return None

            candidate_keys = (
                "TIT2", "TT2",
                "title", "TITLE", "Title",
                "\u00a9nam", "\xa9nam",  # MP4/M4A atoms (UTF-8/latin)
                "NAM", "titl",
            )

            for key in candidate_keys:
                if key not in tags:
                    continue
                value = tags[key]

                if hasattr(value, "text"):
                    items = value.text
                elif isinstance(value, (list, tuple)):
                    items = value
                else:
                    items = [value]

                for item in items:
                    if item:
                        text = str(item).strip()
                        if text:
                            return text
        except Exception:
            pass

    return None



def resolve_title_for_card(
    use_mp3_flag: bool,
    mp3_path: str | None,
    fallback: str | None = None,
) -> str:
    """Resolve the title card text.

    Parameters
    ----------
    use_mp3_flag: bool
        Whether `--title-from-mp3` (or equivalent UI flag) was supplied.
    mp3_path: str | None
        Path to the audio file whose metadata title should be read when the flag is on.
    fallback: str | None
        Optional text to use when metadata is missing. Defaults to an empty title so we
        don't hard-code "final.mp4".
    """

    if use_mp3_flag:
        title = read_audio_title(mp3_path or "")
        if title:
            return title

    candidate = fallback if fallback is not None else None
    if not candidate and mp3_path:
        candidate = Path(mp3_path).stem

    return (candidate or "").strip()

def build_title_ass(
    ass_path: str,
    title_text: str,
    duration_s: float = 3.0,
    width: int = 1920,
    height: int = 1080,
    font: str = "Arial",
    font_size: int = 78,
    outline: int = 2,
    align: int = 5,     # 5 = middle-center
    margin_v: int = 40, # top/bottom margin area used by ASS alignment
):
    """
    Write a minimal ASS file that displays `title_text` centered for [0, duration_s].
    Uses same styling philosophy as your lyric styles.
    """
    # Colors to match default theme (white text, dark outline)
    primary = "&H00FFFFFF&"
    outline_col = "&H00202020&"
    shadow = 0

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
         "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        (f"Style: Title,{font},{font_size},{primary},&H000000FF,{outline_col},&H00000000,"
         f"0,0,0,0,100,100,0,0,1,{max(0,min(outline,10))},{shadow},{max(1,min(9,align))},80,80,{margin_v},1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def _t(t):
        if t < 0: t = 0
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
        cs = int(round((t - int(t)) * 100))
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    # Escape for ASS
    txt = (title_text or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
    dialogue = f"Dialogue: 0,{_t(0.0)},{_t(max(0.1,duration_s))},Title,,0,0,{margin_v},,{{\\an{align}\\q2}}{txt}"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + [dialogue]) + "\n")


# ---------- Main ----------

def main() -> None:
    if "--debug-metadata" in sys.argv:
        tmp = argparse.ArgumentParser(add_help=False)   # use the top-level import
        tmp.add_argument("--debug-metadata", metavar="AUDIO_PATH")
        args, _ = tmp.parse_known_args()
        title = read_audio_title(args.debug_metadata) or "final.mp4"
        print(f"Detected Title: {title}")
        raise SystemExit(0)

        
    ap = argparse.ArgumentParser(description="Minimal lyric sync with auto VAD retry + segment fallback")
    ap.add_argument("--audio", required=True,
                    help="Audio file (mp3/wav/etc.)")
    ap.add_argument("--lyrics", required=True,
                    help="Official lyrics .txt (one line per lyric)")
    ap.add_argument("--out-srt", default="synced_lyrics.srt",
                    help="Output SRT path")
    ap.add_argument("--model-size", default="large-v2",
                    help="WhisperX model preset (tiny/base/small/medium/large-v2/large-v3)")
    ap.add_argument("--language", default="auto",
                    help="Language code or autodetect if omitted")
    ap.add_argument("--preview-image", dest="preview_image", action="append", default=[],
                    metavar="IMG", help="Optional image(s) (jpg/png) for preview video; repeat for multiples")
    ap.add_argument("--image-clip-seconds", type=float, default=None,
                    help="Seconds to display each image (multi-image preview). Leave unset to auto spread.")
    ap.add_argument("--image-fade-seconds", type=float, default=None,
                    help="Seconds for crossfade between images (auto if omitted).")
    ap.add_argument("--image-playback", choices=["story", "loop"], default="story",
                    help="story = play images sequentially once, loop = cycle through images until audio ends.")
    ap.add_argument("--image-slots", type=str, default=None,
                    help="JSON string of timed image slots for story mode.")
    ap.add_argument("--preview-out", default="preview.mp4",
                    help="Preview video output path")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                    help="Inference device")
    ap.add_argument("--compute-type", default="int8",
                    help="CTranslate2 compute_type: int8/float16/…")
    ap.add_argument("--align-mode", default="auto", choices=["auto", "words", "segments"], help="Alignment mode")
    ap.add_argument("--resize", type=int, default=None,
                    help="Resize preview width (e.g., 1280)")
    ap.add_argument("--burn-subs", default=None,
                    help="Path to .srt to burn into the preview (optional)")
    ap.add_argument("--vad", default="auto", choices=["auto","on","off"],
                    help="Voice activity detection: auto/on/off")
    ap.add_argument("--no-burn", action="store_true",
                    help="Do NOT burn the final SRT into the video preview (default is to burn it).")
    ap.add_argument("--force-res", type=str, default="1920:1080",
                    help="Force output resolution (e.g., 1920:1080, 1280:720).")
    ap.add_argument("--srt-only", action="store_true",
                    help="Skip transcription/align; use the provided SRT as the caption source.")
    ap.add_argument("--no-subs", action="store_true",
                    help="Do not transcribe or render any subtitles; produce video from image + audio only.")
    ap.add_argument("--shift-seconds", type=float, default=0.0,
                    help="If > 0, also write a second SRT shifted by this many seconds.")
    ap.add_argument("--out-srt-shifted", default=None,
                    help="Path for the shifted SRT. If omitted and shift-seconds>0, derives from --out-srt (e.g., *_shifted.srt).")
    ap.add_argument("--thanks-text", default="Thank You for Watching",
                    help="Text to display as an end card overlay.")
    ap.add_argument("--thanks-seconds", type=float, default=5.0,
                    help="How long the end card should remain on screen at the end.")
    ap.add_argument("--title-seconds", type=float, default=3.0,
                    help="Duration (in seconds) to display the title card when enabled.")
    ap.add_argument("--enable-word-highlight", action="store_true",
                    help="Reserved flag for future WhisperX per-word highlight overlays.")

              # ---- style selection ----
    ap.add_argument("--style", default="burn-srt", choices=["none", "still", "burn-srt", "rainbow-cycle", "credits"],
                    help="Rendering style for the preview video/output.")
    ap.add_argument("--font", default="Arial",
                    help="Subtitle font for ASS styles.")
    ap.add_argument("--font-size", type=int, default=20,
                    help="Subtitle font size.")
    ap.add_argument("--font-file", default=None, 
                    help="Path to .ttf/.otf font")
    ap.add_argument("--outline", type=int, default=2,
                    help="Subtitle outline thickness (0-5).")
    ap.add_argument("--align", type=int, default=2,
                    help="ASS alignment 1-9 (2=bottom-center).")
    ap.add_argument("--margin-v", type=int, default=20,
                    help="Vertical margin (pixels).")

    # rainbow-cycle params
    ap.add_argument("--cycle-seconds", type=float, default=3.0,
                    help="Seconds per full hue cycle for rainbow style.")
    ap.add_argument("--saturation", type=float, default=1.0,
                    help="Hue saturation (0-1).")
    ap.add_argument("--brightness", type=float, default=1.0,
                    help="Hue value/brightness (0-1).")
    ap.add_argument("--phase-stagger", type=float, default=0.0,
                    help="Phase offset (seconds) to stagger hue start per line (0 = none).")

    # Credits style params

    ap.add_argument("--line-spacing", type=float, default=1.2,
                    help="Line spacing multiplier (credits)")
    ap.add_argument("--scroll-pad", type=int, default=120,
                    help="Extra pixels above/below screen for smooth entry/exit")
    ap.add_argument("--text-theme", default=None,
                    help="Name or slug of a saved font theme (managed via the LyricSync GUI).")

    ap.add_argument("--keep-prep", action="store_true",
                    help="Keep the temporary preprocessed WAV used for transcription (for debugging).")
     
     # Get Stems
    
    ap.add_argument("--separate", choices=["none", "vocals"], default="none",
                    help="If 'vocals', isolate a vocal stem before ASR (requires demucs installed).")
    ap.add_argument("--separator", choices=["demucs"], default="demucs",
                    help="Separator backend to use when --separate!=none.")
    ap.add_argument("--demucs-model", default="htdemucs",
                    help="Demucs model name (e.g., htdemucs, htdemucs_ft, mdx_extra, etc.).")
    ap.add_argument("--demucs-device", default="auto",
                    help="'cuda'|'cpu'|'auto' for separation backend.")
    ap.add_argument(
        "--prep-audio",
        choices=["auto", "off", "center", "bandpass", "nr", "speech"],
        default="auto",
        help=("Preprocess audio for ASR. "
              "'auto' applies a sensible chain (center+bandpass+nr+dynaudnorm). "
              "'center' = mono mid; 'bandpass' = speech band; 'nr' = light noise reduction; "
              "'speech' = minimal ASR-focused chain.")
    )
    ap.add_argument(
        "--title-from-mp3",
        action="store_true",
        help="Use embedded audio title metadata (ID3/MP4/etc.) for the title card text."    )

    ap.add_argument(
        "--debug-metadata",
        metavar="AUDIO_PATH",
        help="Print detected audio title metadata to stdout for quick debug and exit."    )
    ap.add_argument("--font-color", default="#FFFFFF", help="Hex color for subtitle text, e.g. #FFFFFF")
    ap.add_argument("--outline-color", default="#000000", help="Hex color for subtitle outline, e.g. #000000")
    ap.add_argument("--thanks-color", default="#FFFFFF", help="Hex color for end-card text")
    ap.add_argument("--thanks-border-color", default="#000000", help="Hex color for end-card border")

    # --- visual effects ---
    ap.add_argument("--effect", default="none", choices=effect_choices(),
                    help="Visual effect applied to the background (e.g. 'zoom').")
    ap.add_argument("--effect-strength", type=float, default=0.08,
                    help="Zoom amplitude for --effect=zoom (0.03..0.15 good range).")
    ap.add_argument("--effect-cycle", type=float, default=12.0,
                    help="Seconds for a full in→out cycle for --effect=zoom.")
    ap.add_argument("--effect-zoom", type=float, default=None,
                    help="Ken Burns zoom amplitude override (0.01-0.6 suggested).")
    ap.add_argument("--effect-pan", type=float, default=None,
                    help="Ken Burns pan amount override (0-1 suggested).")
    ap.add_argument("--fps", type=int, default=30,
                    help="Preview render FPS (used by image-based effects).")


    
    args = ap.parse_args()
    if getattr(args, "enable_word_highlight", False):
        print("[WhisperX] --enable-word-highlight acknowledged (render integration pending).")
    applied_theme = _apply_text_theme_to_args(args)
    if applied_theme:
        print(f"[INFO] Applied text theme '{applied_theme.get('name') or args.text_theme}'")

    target_w, target_h = _parse_res(args.force_res)
    height_scale = max(target_h, 1) / 1080.0
    scaled_font_size, scaled_outline, scaled_margin_v = _scale_ass_metrics(
        args.font_size, args.outline, args.margin_v, target_h
    )
    scaled_scroll_pad = max(0, int(round(max(args.scroll_pad, 0) * height_scale)))
    # Ensure SRT goes to the same folder as preview_out unless an absolute path was provided
    preview_dir = os.path.dirname(os.path.abspath(args.preview_out)) or "."
    os.makedirs(preview_dir, exist_ok=True)

    if not os.path.isabs(args.out_srt):
        args.out_srt = os.path.join(preview_dir, os.path.basename(args.out_srt))

        
    if args.debug_metadata:
        title = read_audio_title(args.debug_metadata) or "final.mp4"
        print(f"Detected Title: {title}")
        raise SystemExit(0)

    # Decide which font expression to use
    if args.font_file:
        internal_name = _font_fullname_or_family(args.font_file)
        fallback_name = os.path.splitext(os.path.basename(args.font_file))[0] or args.font
        font_for_ass = internal_name or fallback_name or "Arial"
    else:
        font_for_ass = args.font or "Arial"
    
    # --- Optional vocal separation step ---
    sep_audio = args.audio
    if args.separate == "vocals":
        if args.separator == "demucs":
            print("Separating vocals with Demucs…")
            sep_audio = _separate_vocals_demucs(
                src_path=args.audio,
                model=args.demucs_model,
                device=args.demucs_device
            )
            print(f"      Using vocal stem: {sep_audio}")

    # Decide whether we will preprocess (use sep_audio instead of args.audio)
    def _should_prep(args):
        if getattr(args, "no_subs", False) or getattr(args, "srt_only", False):
            return False
        return args.prep_audio != "off"

    prep_path = None
    cleanup_prep = False

    if _should_prep(args):
        mode = args.prep_audio
        print(f"Preprocessing audio for ASR (mode={mode})…")
        prep_path = _make_prep_wav(sep_audio, mode=mode if mode != "auto" else "auto")
        cleanup_prep = not args.keep_prep
        asr_audio = prep_path
    else:
        asr_audio = sep_audio

    # Load lyrics first (used by auto-VAD heuristics)
    lyric_lines = load_lyrics(args.lyrics)

    # Pick compute type defaults
    if args.device == "cuda" and args.compute_type == "int8":
        args.compute_type = "float16"
    if args.device == "auto":
        try:
            from ctranslate2 import get_supported_compute_types  # type: ignore
            if "float16" in get_supported_compute_types("cuda"):
                args.device, args.compute_type = "cuda", "float16"
            else:
                args.device, args.compute_type = "cpu", "int8"
        except Exception:
            args.device, args.compute_type = "cpu", "int8"

    # --- Image + audio only mode ---
    if args.no_subs:
        print("Image + audio only mode: skipping transcription and captions.")
        # (Optional) force title/thanks off in this mode
        # If you want to keep them, remove the next two lines:
        # style["show_title"] = False
        # style["thanks_text"] = None

        # Ensure output folder exists and overwrite behavior is respected
        out_dir = os.path.dirname(os.path.abspath(args.preview_out)) or "."
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(args.preview_out) and not getattr(args, "overwrite", False):
            raise SystemExit(f"[ERROR] Output exists: {args.preview_out} (use --overwrite)")

        # Build preview via make_preview()
        base_title_secs = max(0.0, float(getattr(args, "title_seconds", 0.0)))
        title_card_text = resolve_title_for_card(getattr(args, "title_from_mp3", False), args.audio)
        title_secs = base_title_secs if (title_card_text and base_title_secs > 0.0) else 0.0

        image_slots = None
        if args.image_slots:
            try:
                image_slots = _sort_story_slots(json.loads(args.image_slots))
            except json.JSONDecodeError:
                print("[WARN] Invalid JSON in --image-slots argument. Ignoring.")

        make_preview(
            image=args.preview_image,
            audio=args.audio,
            out=args.preview_out,
            duration=_probe_duration_seconds(args.audio),
            burn_subs=None,
            theme=args.text_theme,
            font=args.font,
            font_size=args.font_size,
            outline=args.outline,
            align=args.align,
            margin_v=args.margin_v,
            font_file=args.font_file,
            title_text=title_card_text,
            title_seconds=title_secs,
            force_res=args.force_res,
            thanks_text=args.thanks_text,
            thanks_seconds=args.thanks_seconds,
            font_color=args.font_color,
            outline_color=args.outline_color,
            thanks_color=args.thanks_color,
            thanks_border_color=args.thanks_border_color,
            effect=args.effect,
            effect_strength=args.effect_strength,
            effect_cycle=args.effect_cycle,
            effect_zoom=args.effect_zoom,
            effect_pan=args.effect_pan,
            fps=args.fps,
            image_clip_seconds=args.image_clip_seconds,
            image_fade_seconds=args.image_fade_seconds,
            image_playback=args.image_playback,
            image_slots=image_slots,
        )
        print("Done.")
        print(f"  - {args.preview_out}")
        raise SystemExit(0)


    # First pass transcription (VAD depends on flag)
    vad_mode = (args.vad or "auto").lower()
    if vad_mode not in ("on", "off"):
        vad_mode = "auto"

    base_title_secs = max(0.0, float(getattr(args, "title_seconds", 0.0)))
    title_card_text = resolve_title_for_card(getattr(args, "title_from_mp3", False), args.audio)
    title_secs = base_title_secs if (title_card_text and base_title_secs > 0.0) else 0.0
    
    if args.srt_only:
        print("SRT-only mode: skipping transcription.")
        words, segs = [], []
        total_dur = _probe_duration_seconds(args.audio)  # get duration for preview/credits
    else:
        print(f"Transcribing (VAD={vad_mode}).")
        words, segs, total_dur = _do_transcribe(
            audio_path=asr_audio,
            model_size=args.model_size,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            vad_filter=vad_mode,
            show_progress=True,
        )

    print(f"    Segments: {len(segs)}  Words: {len(words)}  Duration: {total_dur:.1f}s")

    # Auto-VAD retry — only when we are actually transcribing
    if (not args.srt_only) and vad_mode == "auto" and _needs_vad_retry(words, segs, total_dur, lyric_lines):
        print("First pass looks weak; retrying transcription with VAD=off…")
        words2, segs2, total2 = _do_transcribe(
            audio_path=asr_audio,
            model_size=args.model_size,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            vad_filter="off",
            show_progress=True,
        )
        improved = (len(words2) > len(words)) or ((segs2[-1].end if segs2 else 0.0) > (segs[-1].end if segs else 0.0))
        if improved:
            words, segs, total_dur = words2, segs2, total2
            print(f"    Segments: {len(segs2)}  Words: {len(words2)}  Duration: {total_dur:.1f}s")
        else:
            print("    Keeping first pass (retry did not improve content).")

    # Alignment + SRT writing 
    final_srt_for_burning = None # This will be the path to the SRT used by ffmpeg
    cleanup_final_srt = False
    
    if not args.srt_only:
        print("Using official lyrics…")
        print(f"    {len(lyric_lines)} lines (including blanks).")

        print("Aligning lyrics…")
        if args.align_mode == "segments":
            timed_lines = align_lines_to_segments(segs, lyric_lines)
        elif args.align_mode == "words":
            spans, scores = greedy_align_lines_to_words(words, lyric_lines)
            timed_lines = word_spans_to_timed_lines(words, lyric_lines, spans)
        else:
            spans, scores = greedy_align_lines_to_words(words, lyric_lines)
            tmp = word_spans_to_timed_lines(words, lyric_lines, spans)
            low = [s for s in scores if (s or 0.0) < 0.45]
            late = 0
            if total_dur and tmp:
                tail = tmp[int(max(0, len(tmp)*0.8)):]
                late = sum(1 for _, _, st, _, _ in tail if st > total_dur*0.8)
            if (len(low) > len(scores) * 0.5) or (late > max(2, len(tmp)//5)):
                print("    Word alignment looked poor; using segment-level fallback.")
                timed_lines = align_lines_to_segments(segs, lyric_lines)
            else:
                timed_lines = tmp


        print(f"[DEBUG] words={len(words)} segs={len(segs)}")
        if 'spans' in locals() and isinstance(spans, list):
            for li, span in enumerate(spans[:10]):
                print(f"L{li}: start={span.start_word} end={span.end_word} score={span.score:.2f}")

        print(f"Writing SRT to {args.out_srt} …")
        write_srt(timed_lines, args.out_srt)
        print("    Done.")
        
                      # Also write a shifted copy if requested
        if getattr(args, "shift_seconds", 0.0) and abs(float(args.shift_seconds)) > 1e-6:
            shifted_path = args.out_srt_shifted
            if not shifted_path:
                root, ext = os.path.splitext(args.out_srt)
                shifted_path = f"{root}_shifted{ext or '.srt'}"
            try:
                shift_srt_timestamps(args.out_srt, shifted_path, float(args.shift_seconds))
                print(f"    Wrote shifted SRT (+{float(args.shift_seconds):.3f}s): {shifted_path}")
            except Exception as e:
                print(f"[WARN] Could not create shifted SRT: {e}")

    # After: write_srt(timed_lines, args.out_srt)

# ----- Decide subtitles source and style ------------
    # Decide subtitles source and style
    # Default: burn the generated SRT (or the user SRT in srt-only), unless --no-burn
    burn_path = None
    if not args.no_subs: # Only if we intend to have subtitles at all
        if args.style in ("none", "still"):
            burn_path = None # Explicitly no subs for these styles
        elif args.style == "burn-srt":
            burn_path = args.burn_subs if (args.srt_only and args.burn_subs) else args.out_srt
        elif args.style == "rainbow-cycle":
            print("[4b/6] Converting to ASS format for Rainbow Style")
            src_srt = args.burn_subs if (args.srt_only and args.burn_subs) else args.out_srt
            style_ass = os.path.splitext(args.out_srt)[0] + ".rainbow.ass"
            build_rainbow_ass_from_srt(
                srt_path=src_srt,
                ass_path=style_ass,
                width=target_w,
                height=target_h,
                font=font_for_ass,
                font_size=scaled_font_size,
                outline=scaled_outline,
                align=args.align,
                margin_v=scaled_margin_v,
                cycle_seconds=args.cycle_seconds,
                saturation=args.saturation,
                brightness=args.brightness,
                phase_stagger=args.phase_stagger,
            )
            burn_path = style_ass
        elif args.style == "credits":
            print("Converting to ASS format for Credits Style")
            style_ass = os.path.splitext(args.lyrics)[0] + ".credits.ass"
            build_credits_ass_from_txt(
                lyrics_txt_path=args.lyrics,
                ass_path=style_ass,
                width=target_w,
                height=target_h,
                font=font_for_ass,
                font_size=scaled_font_size,
                outline=scaled_outline,
                align=args.align,
                margin_v=scaled_margin_v,
                line_spacing=args.line_spacing,
                scroll_pad=scaled_scroll_pad,
                duration_seconds=total_dur,
            )
            burn_path = style_ass
            
        # If --no-burn is set, override the burn_path regardless of style
        if args.no_burn:
            burn_path = None

    print("Encoding preview")
    if args.preview_image:
        image_slots = None
        if args.image_slots:
            try:
                image_slots = _sort_story_slots(json.loads(args.image_slots))
            except json.JSONDecodeError:
                print("[WARN] Invalid JSON in --image-slots argument. Ignoring.")
        make_preview(
            image=args.preview_image,
            audio=args.audio,
            out=args.preview_out,
            duration=total_dur,
            burn_subs=burn_path, 
            theme=args.text_theme,
            font=font_for_ass, 
            font_size=args.font_size,
            outline=args.outline,
            align=args.align,
            margin_v=args.margin_v,
            font_file=args.font_file,
            title_text=title_card_text,
            title_seconds=title_secs, 
            force_res=args.force_res,
            thanks_text=args.thanks_text,
            thanks_seconds=args.thanks_seconds,
            font_color=args.font_color,
            outline_color=args.outline_color,
            thanks_color=args.thanks_color,
            thanks_border_color=args.thanks_border_color,
            effect=args.effect,
            effect_strength=args.effect_strength,
            effect_cycle=args.effect_cycle,
            fps=args.fps,
            image_clip_seconds=args.image_clip_seconds,
            image_fade_seconds=args.image_fade_seconds,
            image_playback=args.image_playback,
            image_slots=image_slots,
        )
        
    print("[Complete] Files ready")

    # Cleanup temp preprocessed audio if requested
    if cleanup_prep and prep_path and os.path.exists(prep_path):
        try:
            os.remove(prep_path)
        except Exception:
            pass

if __name__ == "__main__":
    main()
