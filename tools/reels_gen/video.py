"""ffmpeg encoding.

The ffmpeg build on this machine has no drawtext filter (checked with
`ffmpeg -filters`), so it never touches text: tools/reels_gen/frames.py
renders every frame as a finished RGB image with Pillow, and this module
just pipes the raw frame bytes into ffmpeg over stdin (image2pipe-style
rawvideo) for H.264 encoding.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def encode(frame_at: Callable[[float], Image.Image], out_path: Path, *, fps: int,
          duration: float, width: int, height: int, crf: int = 24) -> None:
    """Render `frame_at(t)` for every frame of `duration` seconds at `fps`
    and encode to `out_path`: 1080x1920 H.264, yuv420p, no audio, faststart."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = round(fps * duration)
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", f"{duration}",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i in range(n_frames):
            im = frame_at(i / fps)
            if im.mode != "RGB":
                im = im.convert("RGB")
            if im.size != (width, height):
                raise ValueError(f"frame {i} is {im.size}, expected {(width, height)}")
            proc.stdin.write(im.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    ret = proc.wait()
    if ret != 0:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise RuntimeError(f"ffmpeg failed ({ret}) encoding {out_path}:\n{err}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no output for {out_path}")
