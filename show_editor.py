from pathlib import Path
lines = Path('lyricsync_web/app/Templates/editor.html').read_text(encoding='utf-8').splitlines()
for idx in range(60, 110):
    print(f"{idx+1}: {lines[idx]}")
