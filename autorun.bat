@echo off
uvicorn app.main:app --app-dir "lyricsync_web" --reload --host 0.0.0.0 --port 8787