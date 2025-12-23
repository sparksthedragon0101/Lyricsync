# effects/drift.py
from .base import parse_res
NAME = "drift"

def build(*, force_res: str, fps: int, strength: float, cycle_s: float) -> str:
    """
    'drift' = slow breathing zoom + gentle XY drift.
    - strength ∈ [0..0.5] controls zoom amplitude and drift in px (scaled by width)
    - cycle_s  = seconds per full cycle
    """
    w, h = parse_res(force_res)
    fps_i = max(1, int(fps))
    # frames per sinusoid cycle; clamp tiny values so math stays stable
    cycle = max(1, int(fps_i * max(0.001, float(cycle_s))))

    # Drift amplitude in pixels, scaled by resolution and strength.
    # Baseline 8px at 1920 wide, plus up to ~20px as strength approaches 0.5.
    # Tweak the 8 and 40 if you want more/less motion.
    amp = max(1, int(round((8 + 40 * float(strength)) * (w / 1920))))

    zoom_expr = f"1+{strength}*sin(2*PI*on/{cycle})"
    x_expr    = f"iw/2-(iw/zoom/2)+{amp}*sin(2*PI*on/{cycle})"
    y_expr    = f"ih/2-(ih/zoom/2)+{max(1, amp//2)}*sin(2*PI*on/{cycle}+PI/2)"

    return (
        "zoompan="
        f"z='{zoom_expr}':"
        "d=1:"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"s={w}x{h}:"
        f"fps={fps_i}"
    )
