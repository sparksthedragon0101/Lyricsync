# effects/kenburns.py
from __future__ import annotations
from .base import parse_res

NAME = "kenburns"

def build(*, force_res: str, fps: int, strength: float, cycle_s: float, zoom: float | None = None, pan: float | None = None, **_kwargs) -> str:
    """
    Classic Ken Burns: slow zoom + gentle pan across the frame.
    - strength: zoom amplitude and pan amount (0-0.5 recommended)
    - cycle_s: seconds per full pan/zoom sweep
    """
    w, h = parse_res(force_res)
    fps_i = max(1, int(fps))
    cycle = max(1, int(fps_i * max(0.001, float(cycle_s))))

    # Zoom gently between 1x and 1+zoom_amp
    zoom_base = float(strength) if zoom is None else float(zoom)
    zoom_amp = max(0.01, min(0.6, zoom_base if zoom_base is not None else 0.1))
    zoom_expr = f"1+{zoom_amp}*sin(2*PI*on/{cycle})"

    # Pan amplitude in pixels, scaled by resolution and strength
    pan_base = float(strength) if pan is None else float(pan)
    pan_amp = max(2, int(round((12 + 28 * pan_base) * (w / 1920))))
    pan_x = f"iw/2-(iw/zoom/2)+{pan_amp}*sin(2*PI*on/{cycle})"
    pan_y = f"ih/2-(ih/zoom/2)+{max(2, pan_amp//2)}*sin(2*PI*on/{cycle}+PI/3)"

    return (
        "zoompan="
        f"z='{zoom_expr}':"
        "d=1:"
        f"x='{pan_x}':"
        f"y='{pan_y}':"
        f"s={w}x{h}:"
        f"fps={fps_i}"
    )
