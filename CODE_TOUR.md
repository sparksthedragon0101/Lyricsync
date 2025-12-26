# Lyricsync Code Tour

This document serves as a high-level guide to the Lyricsync codebase, designed to help new developers (and AI agents) quickly orient themselves for feature implementation.

## Project Overview

Lyricsync is a tool for generating lyric videos with AI-generated backgrounds. It has two main modes:
1.  **CLI/Local Script**: `lyricsync.py` (The original monolithic script).
2.  **Web Application**: `lyricsync_web/` (The modern, server-based implementation).

**Focus for new features is primarily on the Web Application.**

## Directory Structure

### Root
-   `lyricsync_web/`: The core web application package.
-   `lyricsync.py`: Legacy/CLI core logic (still used for some shared logic or standalone runs).
-   `requirements.txt`: Python dependencies.

### Web Application (`lyricsync_web/`)
This is where most "Server" and "Dashboard" work happens.

-   `app/`: FastAPI application and UI.
    -   `main.py`: **Entry point.** Contains the FastAPI app definition, API routes, and startup logic.
    -   `projects.py`: Logic for managing project folders, metadata, and file operations.
    -   `jobs.py`: simple job tracking (often superseded by image_pipeline worker).
    -   `Templates/`: Jinja2 HTML templates for the frontend.
        -   `dashboard.html`: The main landing page.
        -   `project.html`: The individual project workspace.
            -   The editor is built with vanilla JS and WaveSurfer.js.
            -   It supports dragging lines on a timeline, splitting/merging subtitle segments.
            -   **Word Timing**: If per-word data matches the segment, a hidden panel reveals precise start/end controls for each word.
            -   Data is persisted to `timing.json` (segments) and `words.json` (word timings).
    -   `Static/`: CSS and Client-side JS.
-   `image_pipeline/`: The Stable Diffusion backend.
    -   `worker.py`: **The async worker** that processes generation jobs. Handles the GPU loop.
    -   `loader.py`: Logic for loading SDXL pipelines and LoRAs.
    -   `registry.py`: Scans and registers available model files.

## Key Architectural Concepts

### 1. Projects
A "Project" is a folder on the filesystem (under `projects/` or a custom root).
-   It contains an audio file, lyrics, and resources (images).
-   `projects.py` handles the CRUD operations for these folders.
-   **Key Pattern**: Almost everything is file-based. State is stored in JSON files or the filesystem structure itself.

### 2. The Image Pipeline
-   **Async Queue**: The web server (`main.py`) pushes jobs to a `JOB_QUEUE` in `image_pipeline/worker.py`.
-   **GPU Worker**: A dedicated background loop (`_gpu_worker`) picks up jobs, loads models (caching them), and runs inference.
-   **Model Management**: Models are loaded dynamically. `force_reset_worker` (added recently) can unload them to free VRAM.

### 3. Frontend
-   **Tech**: Server-side rendered HTML (Jinja2) + Vanilla JS + CSS.
-   **State**: The UI mostly reads state from the DOM or fetches JSON from the API.
-   **Interaction**: Buttons usually trigger `fetch()` calls to API endpoints, then update the DOM or reload the page.

## Common workflows

### Adding a New API Endpoint
1.  Define the route in `lyricsync_web/app/main.py`.
2.  Implement the logic (calling into `projects.py` or `image_pipeline` if needed).
3.  Restart the dev server to test.

### Adding a UI Feature to the Dashboard
1.  Modify `lyricsync_web/app/Templates/dashboard.html`.
2.  Add HTML for the new component.
3.  Add `<script>` logic at the bottom of the file (or in `static/`) to handle interaction.

### Adding a New Image Generation Feature
1.  **Backend**: Update `lyricsync_web/image_pipeline/schemas.py` (if request shape changes) and `worker.py` (to handle the new parameter).
2.  **API**: Update the `ImagePromptRequest` model in `main.py` if needed.
3.  **Frontend**: Update `project.html` inputs and the JS payload construction in `project.html` (or `project.js`).

## "Gotchas" / Notes
-   **Path Configuration**: The app uses `storage_paths` (saved to `.env`) to find where projects and fonts live. Always use the `Projects` class to resolve paths rather than hardcoding.
-   **Async Regression**: There is a specific workaround in `main.py` for a Python 3.13/Windows asyncio bug. Be careful when touching the startup/shutdown logic.

## Deep Dive: Video Rendering & Transcription

While `lyricsync_web` handles the UI, the heavy lifting for audio processing and video rendering often relies on logic in `lyricsync.py`.

### 1. `lyricsync.py` is the Core Engine
It is **not** just legacy code. It contains the logic for:
    -   **Transcription**: Uses `WhisperX` to transcribe audio and align words.
        -   *New*: **Multi-pass Strategy** for Karaoke. Automatically retries with different VAD settings to maximize word coverage.
    -   **VAD (Voice Activity Detection)**: Logic to detecting singing vs silence.
    -   **Vocal Separation**: Uses `Demucs` to isolate vocals.
    -   **Video Generation**: Uses `FFmpeg` (via `subprocess` calls) to:
        -   Burn subtitles (SRT/ASS) into video.
        -   **Karaoke Generation**: Generates ASS with `\k` tags. 
            -   Uses **Interpolation Logic**: Aligns transcribed words to the *Official Lyrics* text. Gaps or missing words are smoothly interpolated so the displayed text is always correct.
            -   **Caching**: Saves word timings to `words.json` to speed up subsequent renders.
        -   Combine audio with generated images.
    -   Apply visual effects (Zoom/Pan).

### 2. Rendering Workflow
When the user clicks "Render" in the Dashboard:
1.  The frontend calls an API endpoint (e.g., `/api/projects/{slug}/render`).
2.  The backend likely shells out to `lyricsync.py` or imports its functions to run the FFmpeg commands.
3.  **Critical Dependency**: **FFmpeg** must be installed and on the system PATH.

### 3. Debugging
-   **Logs**: storage is in `metrics.log` or dashboard "Logs" panel.
-   **Console**: The `lyricsync_web` server prints to stdout.
-   **Generated Files**: Look in the project folder (`projects/<slug>/`) for intermediate files like `aligned.srt`, `vocals.wav`, or `temp_render.mp4` to diagnose issues.

## Deep Dive: The Style System

The Image Generator allows users to select artistic styles (e.g., Anime, Animated, Stylized) which modify the prompts sent to the LLM or Stable Diffusion.

### 1. Data Structure (`main.py`)
-   `STYLE_HINTS`: Maps the main style (value of the primary `<select>`) to a base prompt suffix.
-   `SUB_STYLE_HINTS`: Maps specific sub-style keys (like `anime_90s` or `anim_looney`) to detailed prompt instructions.
    -   *New Feature*: Fully supports Anime Decades/Studios and Western Animation Styles.

### 2. Frontend Logic
-   **HTML**: `project.html` contains the main `<select id="img-style">` and several hidden `<div>`s for sub-menus (e.g., `submenu-anime`, `submenu-animated`).
-   **JavaScript**: `project.js` listens for changes on the main style and toggles the visibility of the corresponding sub-menu.
-   **Prompt Construction**: When sending a request to the LLM (`callImagePromptLLM` in `project.js`), the code checks which style is active and grabs the value from the *visible* sub-menu to send as `sub_style`.

