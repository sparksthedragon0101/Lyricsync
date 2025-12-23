# effects/base.py
from typing import Tuple

def parse_res(force_res: str | None) -> Tuple[int, int]:
    try:
        w, h = map(int, (force_res or "1920:1080").split(":"))
        return max(16, w), max(16, h)
    except Exception:
        return 1920, 1080
