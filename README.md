**Note**: This was a Zero Code project for myself. I am not a developer, everything in this has been AI generated. I made this because I have been struggling from burnout for a while now and the process of making a lyricvideo was frustrating to the point of not wanting to do it at all. 

 - I know there is an issue where uploading the image at the start of the project will not actually populate it to the images section. This is because I just added on the image generation stuff and now the images live in a different folder. You can get around this by just waiting to upload an image until after the project creation. 
 - For those running on windows I have included 2 batch files. One for running the web app and one for running the CLI. The batch files are named autorun.bat and lyricsync_gui.bat. The autorun file will start up the server on port 8787. Lyricsync_gui is how I first ran and tested the program and may no longer work as a large part of the program has been modified since it was last updated. It is a commandline ran menu that will allow you to run the basics of the program without having to use the web app, excluding the image generation.
# Lyricsync

**Lyricsync** is a tool for automating lyric syncing using VAD, WhisperX, and segment alignment. It includes both a CLI for batch processing and a Web UI for interactive editing.

## Features
- **Auto VAD Logic**: Automatically retries transcription without VAD if quality is low.
- **Segment Fallback**: Uses greedy word alignment with fallback to segment matching for better timing.
- **SRT Export**: Generates perfectly timed subtitles.
- **Web Interface**: Interactive editor for fine-tuning lyrics and styles.
- **Preview Generation**: Optional MP4 preview with burned-in subtitles.
- **Title Metatag Editing**: In the lyric timing editor you can update the Title metatag data in case it gets striped or was non-existant in the first place.

##Incomplete Features
- **Image Generation**: You need to point the program to where you have your checkpoints saved
- **AI Assist Image Prompt**: This is currently only set up for Ollama only.
- **Image Style Prompts**: I just began working on getting the stylized prompts so only one of them really working out of the box (Stylized - Pixel Art)
- **LORA's**: I tried adding these but I am not sure how effective they have been in my testing.

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
