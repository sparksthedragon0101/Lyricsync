# Lyricsync

**Lyricsync** is a powerful tool for automating lyric syncing using VAD, WhisperX, and segment alignment. It includes both a CLI for batch processing and a Web UI for interactive editing.

## Features
- **Auto VAD Logic**: Automatically retries transcription without VAD if quality is low.
- **Segment Fallback**: Uses greedy word alignment with fallback to segment matching for better timing.
- **SRT Export**: Generates perfectly timed subtitles.
- **Web Interface**: Interactive editor for fine-tuning lyrics and styles.
- **Preview Generation**: Optional MP4 preview with burned-in subtitles.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/Lyricsync.git
    cd Lyricsync
    ```

2.  **Install Dependencies:**
    It is recommended to use a virtual environment.
    ```bash
    python -m venv .venv
    # Windows
    .venv\Scripts\activate
    # Linux/Mac
    # source .venv/bin/activate

    pip install -r requirements.txt
    ```

3.  **Install FFmpeg:**
    Ensure `ffmpeg` and `ffprobe` are in your system PATH.

## Usage

### Quick Start: Lyric Video
To generate a lyric video, you **must** provide at least one background image.

```bash
python lyricsync.py --audio song.mp3 --lyrics lyrics.txt --preview-image cover.jpg
```
This generates `preview.mp4` with burned-in subtitles.

### CLI Usage Guide

**Basic Command**
```bash
python lyricsync.py \
  --audio "path/to/song.mp3" \
  --lyrics "path/to/lyrics.txt" \
  --preview-image "path/to/image.jpg"
```

**Customizing the Output**
- **Resolution**: Force a specific resolution (default 1920:1080).
  ```bash
  --force-res 1080:1920  # for TikTok/Shorts/Reels
  ```
- **Visual Effects**: Add motion to your background image.
  ```bash
  --effect zoom --effect-strength 0.05
  ```
- **Styles**: Change how lyrics are rendered.
  ```bash
  --style rainbow-cycle  # karaoke-style individual word highlighting
  --style credits        # scrolling end-credits style
  ```

**Advanced Options**
- **Vocal Separation**: Isolate vocals before transcribing for better accuracy.
  ```bash
  --separate vocals --demucs-model htdemucs
  ```
- **Multiple Images**: Create a slideshow by repeating the argument.
  ```bash
  --preview-image img1.jpg --preview-image img2.jpg --image-fade-seconds 1.0
  ```
- **Hardware Acceleration**: Use CUDA if you have an NVIDIA GPU.
  ```bash
  --device cuda
  ```

### Web App
To launch the interactive web interface:

```bash
uvicorn lyricsync_web.app.main:app --app-dir "lyricsync_web" --reload --host 0.0.0.0 --port 8787
```
Then visit `http://localhost:8787` in your browser.

## Project Structure
- `lyricsync.py`: Core logic for audio processing and alignment.
- `lyricsync_web/`: FastAPI web application code.
- `effects/`: Visual effect processors.
- `configs/`: Theme and configuration files.

## License
[Your License Here]
