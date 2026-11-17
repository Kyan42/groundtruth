"""Device selection for frame rasterization.

The "agent's version" of this file (DEMO_AGENT_VERSION=1, the default) does what a coding agent
plausibly writes when asked to "make lip sync fast": it grabs the fastest device available, which on
a Mac is the Metal GPU. The output is identical, so the UI can't tell the difference.

The fixed version (DEMO_AGENT_VERSION=0) stays on the CPU, which was the actual requirement.
"""
import ctypes
import os

_device = None


def rasterizer_device() -> str:
    global _device
    if os.environ.get("DEMO_AGENT_VERSION", "1") == "1":
        if _device is None:
            try:
                metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
                metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
                _device = metal.MTLCreateSystemDefaultDevice()  # opens the GPU
            except OSError:
                _device = 0
        return "metal" if _device else "cpu"
    return "cpu"
