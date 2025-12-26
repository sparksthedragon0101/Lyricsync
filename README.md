# Lyricsync
**Lyricsync** is a tool for automating lyric syncing using VAD, WhisperX, and segment alignment. It includes both a CLI for batch processing and a Web UI for interactive editing.

## Features
- **Auto VAD Logic**: Automatically retries transcription without VAD if quality is low.
- **Segment Fallback**: Uses greedy word alignment with fallback to segment matching for better timing.
- **SRT Export**: Generates timed subtitles.
- **Karaoke Mode**: Options for per-word highlighting effects using advanced ASS subtitles.
    - **Robust Interpolation**: Uses official lyrics as the master source, ensuring no words are skipped even if transcription misses them.
    - **Multi-pass**: Automatically retries transcription to maximize accuracy for karaoke timing.
    - **Editor Support**: Timings can be fine-tuned in the web editor (Per-word Timing panel).
- **Web Interface**: Interactive editor for fine-tuning lyrics and styles.
- **Preview Generation**: Optional MP4 preview with burned-in subtitles.

## Planned Features
- **Kinetic Typography**: Animated subtitles with kinetic typography effects.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/Lyricsync.git
    cd Lyricsync
    ```

2.  **Install Dependencies:**
    The recommended way is to simply run `autorun.bat`. This script will:
    - Automatically create a virtual environment using Python 3.10.
    - Install all dependencies (including PyTorch fixes for NVIDIA 50-series cards).
    - Start the application.

    **Manual Installation:**
    If you prefer to set it up manually:
    ```bash
    # Ensure you are using Python 3.10
    py -3.10 -m venv .venv
    # Windows
    .venv\Scripts\activate
    # Linux/Mac
    # source .venv/bin/activate

    # Install specific torch version first (for CUDA support)
    pip install torch==2.5.1+cu124 torchvision==0.20.1+cu124 torchaudio==2.5.1+cu124 --extra-index-url https://download.pytorch.org/whl/cu124

    # Install remaining requirements
    pip install -r requirements.txt
    ```

3.  **Install FFmpeg:**
    Ensure `ffmpeg` and `ffprobe` are in your system PATH.

## Usage

### CLI Mode
Run the `lyricsync.py` script directly:

```bash
# Basic usage
python lyricsync.py --audio song.mp3 --lyrics lyrics.txt

# Specify device (CPU or CUDA)
python lyricsync.py --audio song.mp3 --lyrics lyrics.txt --device cuda

# Enable Karaoke (Per-Word) Timing
python lyricsync.py --audio song.mp3 --lyrics lyrics.txt --enable-word-highlight --style burn-srt
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
Everything I have here was made using open source materials. This was a no code project from myself, all of it has been generated using AI. I have done my best to ensure that the program works as intended. But if there are any issues please let me know. I am also open to any suggestions for features or improvemens, please keep in mind that I am not a coder and it will take time for me to research and implement features. 