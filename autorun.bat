@echo off
set "VENV_DIR=.venv"
set "ExpectedVer=3.10"

IF EXIST "%VENV_DIR%" (
    echo Checking existing virtual environment version...
    "%VENV_DIR%\Scripts\python.exe" --version > temp_ver.txt 2>&1
    findstr /C:"Python %ExpectedVer%" temp_ver.txt >nul
    if errorlevel 1 (
        echo Incorrect Python version detected. Recreating with Python %ExpectedVer%...
        rmdir /s /q "%VENV_DIR%"
    ) else (
        echo Virtual environment is correct.
    )
    if exist temp_ver.txt del temp_ver.txt
)

IF NOT EXIST "%VENV_DIR%" (
    echo Creating virtual environment with Python %ExpectedVer%...
    py -%ExpectedVer% -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"
echo Installing PyTorch (Stable 2.5.1 + CUDA 12.4)...
pip install torch==2.5.1+cu124 torchvision==0.20.1+cu124 torchaudio==2.5.1+cu124 --extra-index-url https://download.pytorch.org/whl/cu124

echo Installing dependencies...
pip install -r requirements.txt

echo Starting server...
uvicorn app.main:app --app-dir "lyricsync_web" --reload --host 0.0.0.0 --port 8787