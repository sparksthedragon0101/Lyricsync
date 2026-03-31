#!/usr/bin/env python3
"""
Thin wrapper around demucs.separate that patches torchaudio.save
to use soundfile instead of torchcodec (which crashes on Windows
without full-shared FFmpeg DLLs).
"""
import sys

# --- Patch torchaudio.save BEFORE demucs imports it ---
try:
    import torchaudio
    import soundfile as _sf
    import torch as _torch

    def _soundfile_save(uri, src, sample_rate, **kwargs):
        """Drop-in torchaudio.save via soundfile."""
        if isinstance(src, _torch.Tensor):
            src = src.cpu()
            if src.dim() == 2:
                src = src.t()  # (channels, samples) -> (samples, channels)
            src = src.numpy()
        _sf.write(str(uri), src, sample_rate)

    torchaudio.save = _soundfile_save
except Exception:
    pass

# --- Now run demucs ---
from demucs.separate import main
if __name__ == "__main__":
    main()
