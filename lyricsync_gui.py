
import os, sys, threading, subprocess, shlex, queue, glob, re, json
import FreeSimpleGUI as sg  # Fork with classic v4 API (theme, etc.)

# Resolve base directory for both normal and PyInstaller-frozen execution
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONTS_DIR = os.path.join(BASE_DIR, "fonts")
THEMES_CONFIG = os.path.join(BASE_DIR, "lyricsync_web", "app", "configs", "themes.json")


def list_text_themes():
    try:
        with open(THEMES_CONFIG, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        names = []
        seen = set()
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                label = (entry.get("name") or entry.get("slug") or "").strip()
                if not label:
                    continue
                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)
                names.append(label)
        return names or ["default"]
    except Exception:
        return ["default"]


TEXT_THEMES = list_text_themes()
STYLE_CHOICES = ["burn-srt", "rainbow-cycle", "credits", "still", "none"]

FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

def list_local_fonts():
    files = []
    for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
        files.extend(glob.glob(os.path.join(FONTS_DIR, ext)))
    names = sorted({os.path.basename(p) for p in files})
    return names

def ffprobe_duration_seconds(audio_path: str) -> float:
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return float(out.strip())
    except Exception:
        return 0.0

def parse_ffmpeg_time_to_seconds(line: str) -> float:
    m = FFMPEG_TIME_RE.search(line)
    if not m:
        return -1.0
    hh = int(m.group(1)); mm = int(m.group(2)); ss = float(m.group(3))
    return hh * 3600 + mm * 60 + ss

def build_command(py_exe, script_path, values):
    cmd = [py_exe, script_path]

    # Required
    audio = values.get("-SONG-") or ""
    lyrics = values.get("-LYRICS-") or ""
    if not audio:
        raise ValueError("Please select a Song (audio file).")
    if not lyrics:
        raise ValueError("Please select a Lyrics file (.srt or .txt).")
    cmd += ["--audio", audio, "--lyrics", lyrics]

    # Title card from MP3 metadata
    if values.get("-TITLEMP3-"):
        cmd += ["--title-from-mp3"]

    # Preview image
    if values.get("-COVER-"):
        cmd += ["--preview-image", values["-COVER-"]]

    # Style selection (force 'credits' if end-credits is checked)
    chosen_style = values.get("-STYLE-") or "burn-srt"
    if values.get("-ENDCRED-"):
        chosen_style = "credits"
    cmd += ["--style", chosen_style]

    # Font & theme
    chosen_font = (values.get("-FONT-") or "").strip()
    external_font = (values.get("-FONTFILE-") or "").strip()
    if external_font:
        cmd += ["--font-file", external_font]
    elif chosen_font:
        cmd += ["--font", chosen_font]

    theme = (values.get("-THEME-") or "").strip()
    if theme:
        cmd += ["--text-theme", theme]

    # Optional advanced style params
    def add_opt(flag, key, cast=str):
        val = (values.get(key) or "").strip()
        if val:
            cmd.extend([flag, cast(val)])

    add_opt("--font-size", "-FONTSIZE-")
    add_opt("--outline", "-OUTLINE-")
    add_opt("--margin-v", "-MARGINV-")
    add_opt("--line-spacing", "-LINESP-")
    add_opt("--scroll-pad", "-SCRLPAD-")
    add_opt("--cycle-seconds", "-CYCLE-")
    add_opt("--saturation", "-SAT-")
    add_opt("--brightness", "-BRT-")
    add_opt("--phase-stagger", "-PHASE-")

    # Resolution
    force_res = (values.get("-RES-") or "").strip()
    if force_res:
        cmd += ["--force-res", force_res]

    # Alignment toggle
    if values.get("-ALIGN-"):
        cmd += ["--align-mode", "words"]

    # SRT-only
    if values.get("-SRTONLY-"):
        cmd += ["--srt-only"]

    # Verbose
    if values.get("-VERBOSE-"):
        cmd += ["--verbose"]

    # Output handling
    outdir = (values.get("-OUTDIR-") or "").strip()
    audio_base = os.path.splitext(os.path.basename(audio))[0]
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        preview_out = os.path.join(outdir, f"{audio_base}_preview.mp4")
        cmd += ["--preview-out", preview_out]

        if values.get("-FORCE_OUT_SRT-"):
            srt_out = os.path.join(outdir, f"{audio_base}.srt")
            cmd += ["--out-srt", srt_out]
    else:
        srt_out = None

    return cmd, outdir or None

def run_process(cmd, q_lines, q_done, cwd=None):
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
    except Exception as e:
        q_lines.put(f"[LAUNCH ERROR] {e}\n")
        q_done.put(("exit", 1))
        return

    for line in iter(proc.stdout.readline, ''):
        q_lines.put(line)
    proc.stdout.close()
    ret = proc.wait()
    q_done.put(("exit", ret))

def find_timing_server():
    # Look for ./timing_editor/timing_server.py or ./timing_editor_v1/timing_server.py
    candidates = [
        os.path.join(BASE_DIR, "timing_editor", "timing_server.py"),
        os.path.join(BASE_DIR, "timing_editor_v1", "timing_server.py"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

def decide_project_path_for_editor(lyrics_path, audio_path, outdir):
    """
    If lyrics is .srt or .json, use it.
    Else, if outdir present, use <outdir>/<audio_base>.srt so the server can create JSON next to it.
    Else return None (we'll skip auto-launch).
    """
    lp = (lyrics_path or "").strip()
    if lp.lower().endswith(".srt") or lp.lower().endswith(".json"):
        return lp
    if outdir and audio_path:
        audio_base = os.path.splitext(os.path.basename(audio_path))[0]
        return os.path.join(outdir, f"{audio_base}.srt")
    return None

def launch_timing_editor_async(project_path, audio_path, port=8787):
    server = find_timing_server()
    if not server:
        return False, "[WARN] timing_server.py not found. Place 'timing_editor' next to this GUI."
    def _run():
        try:
            subprocess.Popen([sys.executable, server, "--project", project_path, "--audio", audio_path, "--port", str(port)])
        except Exception as e:
            print("[TimingEditor ERROR]", e)
    threading.Thread(target=_run, daemon=True).start()
    return True, f"[INFO] Timing Editor starting on http://127.0.0.1:{port}/"

def make_window():
    sg.theme("DarkGrey12")
    local_fonts = list_local_fonts()
    default_font = "DejaVuSans.ttf" if "DejaVuSans.ttf" in local_fonts else (local_fonts[0] if local_fonts else "")

    files_frame = [
        [sg.Text("Song (audio)"),
         sg.Input(key="-SONG-", expand_x=True),
         sg.FileBrowse(file_types=(("Audio", "*.mp3;*.wav;*.flac;*.m4a;*.ogg"),))],

        [sg.Text("Lyrics (.srt or .txt)"),
         sg.Input(key="-LYRICS-", expand_x=True),
         sg.FileBrowse(file_types=(("Text/SRT", "*.srt;*.txt"),))],

        [sg.Text("Cover (image)"),
         sg.Input(key="-COVER-", expand_x=True),
         sg.FileBrowse(file_types=(("Images", "*.png;*.jpg;*.jpeg"),))],

        [sg.Text("Output Folder"),
         sg.Input(key="-OUTDIR-", expand_x=True),
         sg.FolderBrowse()],
    ]

    look_frame = [
        [sg.Text("Style"),
         sg.Combo(STYLE_CHOICES, default_value="burn-srt", key="-STYLE-", size=(18,1), readonly=False),
         sg.Checkbox("End credits scroll", key="-ENDCRED-"),
         sg.Checkbox("Use MP3 Title for title card", key="-TITLEMP3-")],

        [sg.Text("Font"),
         sg.Combo(local_fonts, default_value=default_font, key="-FONT-", size=(30,1), enable_events=True, readonly=True),
         sg.Text(" or Other Font File"),
         sg.Input(key="-FONTFILE-", size=(35,1)),
         sg.FileBrowse(file_types=(("Fonts", "*.ttf;*.otf"),)),
         sg.Button("Refresh Fonts", key="-REFONTS-")],

        [sg.Text("Theme"),
         sg.Combo(TEXT_THEMES, default_value=TEXT_THEMES[0], key="-THEME-", size=(14,1), readonly=False),
         sg.Text("Font Size"), sg.Input(key="-FONTSIZE-", size=(6,1)),
         sg.Text("Outline"), sg.Input(key="-OUTLINE-", size=(6,1)),
         sg.Text("MarginV"), sg.Input(key="-MARGINV-", size=(6,1)),
         sg.Text("Line Sp."), sg.Input(key="-LINESP-", size=(6,1)),
         sg.Text("ScrollPad"), sg.Input(key="-SCRLPAD-", size=(6,1))],

        [sg.Text("Cycle(s)"), sg.Input(key="-CYCLE-", size=(6,1)),
         sg.Text("Sat"), sg.Input(key="-SAT-", size=(6,1)),
         sg.Text("Bright"), sg.Input(key="-BRT-", size=(6,1)),
         sg.Text("Phase"), sg.Input(key="-PHASE-", size=(6,1)),
         sg.Text("Force Res (W:H)"),
         sg.Input("1920:1080", key="-RES-", size=(12,1)),
         sg.Checkbox("Use alignment", key="-ALIGN-"),
         sg.Checkbox("SRT only", key="-SRTONLY-"),
         sg.Checkbox("Verbose", key="-VERBOSE-"),
         sg.Checkbox("Write SRT to OutDir", key="-FORCE_OUT_SRT-")],
    ]

    console_frame = [
        [sg.Multiline(size=(100, 16), key="-LOG-", autoscroll=True, disabled=True, font=("Consolas", 10))],
        [sg.Text("Render Progress"), sg.ProgressBar(100, orientation="h", size=(45, 20), key="-PGBAR-"),
         sg.Text("0%", key="-PGTXT-", size=(6,1))],
    ]

    actions_row = [
        sg.Button("Generate", key="-RUN-", bind_return_key=True),
        sg.Button("Open Timing Editor", key="-OPENEDITOR-"),
        sg.Checkbox("Auto-open Timing Editor", key="-AUTOEDITOR-", default=True),
        sg.Button("Stop", key="-STOP-", disabled=True),
        sg.Button("Open Output", key="-OPENOUT-"),
        sg.StatusBar("idle", key="-STATUS-", size=(60,1)),
    ]

    layout = [
        [sg.Frame("Files", files_frame, expand_x=True)],
        [sg.Frame("Look & Options", look_frame, expand_x=True)],
        [sg.Frame("Console", console_frame, expand_x=True, expand_y=True)],
        [actions_row],
    ]

    return sg.Window("LyricSync – Preview Maker (GUI)", layout, resizable=True, finalize=True)

def main():
    win = make_window()
    win["-LOG-"].print("Welcome to LyricSync GUI.\nPick your Song + Lyrics (required), optional Cover, then click Generate.\n")

    worker = None
    q_lines, q_done = queue.Queue(), queue.Queue()
    current_outdir = ""
    total_seconds = 0.0
    progress_active = False

    def enable_ui(running: bool):
        win["-RUN-"].update(disabled=running)
        win["-STOP-"].update(disabled=not running)

    def reset_progress():
        win["-PGBAR-"].update(current_count=0, max=100)
        win["-PGTXT-"].update("0%")

    def auto_launch_editor_if_enabled(values, outdir):
        if not values.get("-AUTOEDITOR-", False):
            return
        audio_path = (values.get("-SONG-") or "").strip()
        lyrics_path = (values.get("-LYRICS-") or "").strip()
        proj = decide_project_path_for_editor(lyrics_path, audio_path, outdir)
        ok, msg = (False, "")
        if proj and audio_path:
            ok, msg = launch_timing_editor_async(proj, audio_path)
        if msg:
            win["-LOG-"].print(msg)

    while True:
        event, values = win.read(timeout=100)
        if event in (sg.WIN_CLOSED, "Exit"):
            break

        if event == "-REFONTS-":
            fonts = list_local_fonts()
            win["-FONT-"].update(values=fonts)
            if fonts:
                win["-FONT-"].update(value=fonts[0])
            win["-LOG-"].print(f"[INFO] Found {len(fonts)} fonts in ./fonts")

        if event == "-OPENEDITOR-":
            audio_path = (values.get("-SONG-") or "").strip()
            lyrics_path = (values.get("-LYRICS-") or "").strip()
            outdir = (values.get("-OUTDIR-") or "").strip()
            proj = decide_project_path_for_editor(lyrics_path, audio_path, outdir)
            if not proj or not audio_path:
                win["-LOG-"].print("[INFO] Need an audio and SRT/JSON path (or choose an Output folder) before opening the editor.")
            else:
                ok, msg = launch_timing_editor_async(proj, audio_path)
                win["-LOG-"].print(msg if msg else "[INFO] Tried to launch editor.")

        if event == "-RUN-":
            # Resolve python + script path
            py_exe = sys.executable or "python"
            script_path = os.path.join(BASE_DIR, "lyricsync.py")
            if not os.path.exists(script_path):
                win["-LOG-"].print(f"[ERROR] lyricsync.py not found at {script_path}")
                continue

            # Probe duration for progress bar
            audio_path = (values.get("-SONG-") or "").strip()
            total_seconds = ffprobe_duration_seconds(audio_path) if audio_path else 0.0
            reset_progress()
            progress_active = total_seconds > 0.0
            if not progress_active:
                win["-LOG-"].print("[INFO] Could not detect duration with ffprobe. Progress bar will be approximate/disabled.")

            # Build command
            try:
                cmd, outdir = build_command(py_exe, script_path, values)
            except Exception as e:
                win["-LOG-"].print(f"[ERROR] {e}")
                continue

            current_outdir = outdir or os.path.dirname(values.get("-SONG-", "")) or os.getcwd()
            win["-LOG-"].print("> " + " ".join(shlex.quote(c) for c in cmd))
            win["-STATUS-"].update("running…")
            enable_ui(True)

            # Start Timing Editor immediately for seamless feel
            auto_launch_editor_if_enabled(values, outdir)

            # Start worker (cwd = outdir if set, else the folder containing lyricsync.py)
            run_cwd = outdir or os.path.dirname(script_path)
            worker = threading.Thread(target=run_process, args=(cmd, q_lines, q_done, run_cwd), daemon=True)
            worker.start()

        if event == "-STOP-":
            win["-LOG-"].print("[INFO] Stop requested. If the process doesn't return quickly, close the console/terminal window or wait for the current step to finish.")

        if event == "-OPENOUT-":
            target = current_outdir or os.getcwd()
            if os.path.isdir(target):
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(target)  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", target])
                    else:
                        subprocess.Popen(["xdg-open", target])
                except Exception as e:
                    win["-LOG-"].print(f"[ERROR] Could not open folder: {e}")
            else:
                win["-LOG-"].print("[INFO] No output folder yet.")

        # Drain logs and update progress
        try:
            while True:
                line = q_lines.get_nowait()
                win["-LOG-"].print(line, end="")
                if progress_active:
                    cur_sec = parse_ffmpeg_time_to_seconds(line)
                    if cur_sec >= 0 and total_seconds > 0:
                        pct = max(0, min(100, int((cur_sec / total_seconds) * 100)))
                        win["-PGBAR-"].update(pct)
                        win["-PGTXT-"].update(f"{pct}%")
        except queue.Empty:
            pass

        # Completion check
        try:
            kind, code = q_done.get_nowait()
            if kind == "exit":
                if code == 0 and progress_active:
                    win["-PGBAR-"].update(100)
                    win["-PGTXT-"].update("100%")
                if code == 0:
                    win["-STATUS-"].update("complete ✓")
                    win["-LOG-"].print("[DONE] Completed successfully.")
                else:
                    win["-STATUS-"].update(f"error (code {code})")
                    win["-LOG-"].print(f"[ERROR] Process exited with code {code}")
                enable_ui(False)
                worker = None
        except queue.Empty:
            pass

    win.close()

if __name__ == "__main__":
    main()
