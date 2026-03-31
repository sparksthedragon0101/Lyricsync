# Lyricsync
**Lyricsync** is a tool for automating lyric syncing using VAD, WhisperX, and segment alignment. It includes both a CLI for batch processing and a Web UI for interactive editing.

**Notice**
- There is code in this repo for using Z-Image and Flux however I have not been able to test Flux due to a lack of VRAM and Z-Image is only producing static at this moment. I highly suggest using SD/SDXL for image generation. 

## Updates
- **MFA**: Montreal Forced Aligner support for high-precision word-level timings.
- **Improved UI Feedback**: Added stage-based toast notifications (Separating Vocals, Transcribing, Aligning) and improved progress tracking for background jobs.
- **JSON Compatibility**: Optimized `words.json` handling to support both legacy and versioned (v4) formats.
- **Ollama Integration**: Added support for local LLMs via Ollama for image prompt generation (configured via `.env`).
- **Settings Persistence**: Unified web dashboard settings persistence and fixed UI synchronization issues (e.g., Ken Burns options visibility).
- **Autostart**: Updated the autostart bat file for the Windows users. It should check for a .venv and dependencies and install them before starting the application now which should make it easier for non-technical users to get started.
- **Increased Zoom**: Increased the zoom level of the web interface to make it easier to see the lyrics and the waveform for word level alignment. 
        -**Note** There seems to be some compatability issues with the timing editor and the Opera browser. It works fine in Firefox. I have not had a chance to test it on Opera yet. The issue is a desync between where the audio is and what is playing making it difficult to time the words accurately.
- **CPU Video Rendering & GPU Fallback**: Added flexible video encoding options (`libx264`, `libx265`, `h264_nvenc`, `h264_qsv`).
    - **Smart Fallback**: The new `auto` mode automatically detects and uses GPU acceleration (NVIDIA/Intel) when available, falling back to CPU rendering if hardware is missing.

## Features
- **Flexible Video Encoding**: Configure video rendering for high-performance GPUs or low-resource CPU environments.
    - **Auto-Detection**: Seamlessly switches between NVIDIA NVENC, Intel QuickSync, and standard CPU rendering.
    - **Quality Controls**: Custom CRF (Constant Rate Factor) and Bitrate settings for fine-tuned output quality.
- **Auto VAD Logic**: Automatically retries transcription without VAD if quality is low.
- **Segment Fallback**: Uses greedy word alignment with fallback to segment matching for better timing.
- **SRT Export**: Generates timed subtitles.
- **Karaoke Mode**: Options for per-word highlighting effects using advanced ASS subtitles.
    - **Robust Interpolation**: Uses official lyrics as the master source, ensuring no words are skipped even if transcription misses them.
    - **Multi-pass**: Automatically retries transcription to maximize accuracy for karaoke timing.
    - **Editor Support**: Timings can be fine-tuned in the web editor (Per-word Timing panel).
- **Web Interface**: Interactive editor for fine-tuning lyrics and styles.
- **Preview Generation**: MP4 preview with burned-in subtitles and download link if you are working remotely.
  
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

4.  **LLM Configuration (Optional):**
    To enable AI lyric polishing and image prompt generation, create a `.env` file in the `lyricsync_web/` directory:
    ```bash
    LLM_PROVIDER=ollama
    OLLAMA_BASE_URL=http://127.0.0.1:11434
    ```
    Ensure [Ollama](https://ollama.ai/) is installed and running with your preferred model (e.g., `llama3`).

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

# Advanced Video Encoding
python lyricsync.py --audio song.mp3 --vcodec auto --vpreset fast --vcrf 18
python lyricsync.py --audio song.mp3 --vcodec h264_nvenc --vbitrate 8M
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
