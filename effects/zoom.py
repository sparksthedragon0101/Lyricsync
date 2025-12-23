# effects/zoom.py
from .base import parse_res

NAME = "zoom"

def build(*, force_res: str, fps: int, strength: float, cycle_s: float) -> str:
    """Return a -vf fragment for a smooth in/out zoom using zoompan (image/video-safe)."""
    w, h = parse_res(force_res)
    frames_per_cycle = max(1, int(fps * max(0.001, cycle_s)))
    A = max(0.0, strength)
    return (
        "zoompan="
        f"z='1+{A}*sin(2*PI*on/{frames_per_cycle})':d=1:"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}:fps={max(1, int(fps))}"
    )
