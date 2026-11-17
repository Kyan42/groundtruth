"""Turns text into a sequence of mouth frames for the avatar."""
from accel import rasterizer_device
from visemes import viseme_for

FRAME_MS = 80


def render(text: str) -> dict:
    device = rasterizer_device()
    frames = [viseme_for(ch) for ch in text if not ch.isspace()]
    # Pretend rasterization: a little CPU work per frame so the render is measurable.
    checksum = 0
    for i, f in enumerate(frames):
        for j in range(20_000):
            checksum = (checksum * 31 + j + len(f) + i) % 1_000_003
    return {"frames": frames, "frame_ms": FRAME_MS, "count": len(frames), "checksum": checksum, "_device": device}
