from pathlib import Path
import re
lines = Path("lyricsync_web/app/Templates/editor.html").read_text(encoding="utf-8").splitlines()
for idx,line in enumerate(lines,1):
    if "<label" in line:
        if "for=" not in line and "/label" in line:
            print(idx, line.strip())
