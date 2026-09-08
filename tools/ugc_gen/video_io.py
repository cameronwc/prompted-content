"""ffmpeg decode (VideoReader) and encode (Encoder) over raw pipes.

This machine's ffmpeg has no drawtext filter (see tools/reels_gen/video.py),
so tools/reels_gen renders every pixel with Pillow and never decodes a
source video at all. The UGC composer is different: it overlays two real
video clips (a reaction and an app-action recording), so this module reads
frames out of them the same way reels_gen/video.py writes frames in --
rgb24 rawvideo over a subprocess pipe -- and tools/ugc_gen/compose.py does
all the actual (Pillow) compositing and text.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def probe_video(path: Path) -> dict:
    """{'width', 'height', 'duration'} for the first video stream, via ffprobe."""
    cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0",
          "-show_entries", "stream=width,height", "-show_entries", "format=duration",
          "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return {"width": int(stream["width"]), "height": int(stream["height"]),
           "duration": float(data["format"]["duration"])}


class VideoReader:
    """Decodes `path` through `vf` -- which must yield frames of exactly
    `width`x`height` -- as rgb24 over a pipe. `read()` returns None once the
    stream is exhausted; `read_held()` instead keeps returning the last
    frame forever (for holding a reaction clip shorter than the app-action
    clip on its last frame)."""

    def __init__(self, path: Path, vf: str, width: int, height: int):
        self.width, self.height = width, height
        self._frame_bytes = width * height * 3
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
              "-vf", vf, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._last: Image.Image | None = None

    def _read_exact(self) -> bytes | None:
        buf = bytearray()
        n = self._frame_bytes
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def read(self) -> Image.Image | None:
        data = self._read_exact()
        if data is None:
            return None
        im = Image.frombytes("RGB", (self.width, self.height), data)
        self._last = im
        return im

    def read_held(self) -> Image.Image | None:
        im = self.read()
        return im if im is not None else self._last

    def close(self) -> None:
        """Closing early (e.g. after reading only the first frame for a
        dry-run) makes ffmpeg exit non-zero on the broken pipe; that is not
        an error. Only a decode that never produced a single frame is worth
        surfacing."""
        if self.proc.stdout:
            self.proc.stdout.close()
        ret = self.proc.wait()
        err = self.proc.stderr.read() if self.proc.stderr else b""
        if self.proc.stderr:
            self.proc.stderr.close()
        if ret != 0 and self._last is None:
            raise RuntimeError(f"ffmpeg failed decoding {self.width}x{self.height}: "
                               f"{err.decode('utf-8', 'replace')}")


class Encoder:
    """Streams RGB frames to an H.264 MP4: yuv420p, no audio, faststart --
    the write side of the same rawvideo-pipe pattern as
    tools/reels_gen/video.py's `encode`, just opened for incremental writes
    instead of one call with a fixed frame count (the UGC composer streams
    frames as it decodes them rather than rendering frame_at(t) at random
    access)."""

    def __init__(self, out_path: Path, fps: int, width: int, height: int, crf: int = 24):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.width, self.height, self.out_path = width, height, out_path
        self.n = 0
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
              "-f", "rawvideo", "-pixel_format", "rgb24",
              "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-",
              "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, im: Image.Image) -> None:
        if im.mode != "RGB":
            im = im.convert("RGB")
        if im.size != (self.width, self.height):
            raise ValueError(f"frame {self.n} is {im.size}, expected {(self.width, self.height)}")
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(im.tobytes())
        except BrokenPipeError:
            pass
        self.n += 1

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        ret = self.proc.wait()
        if ret != 0:
            err = self.proc.stderr.read().decode("utf-8", "replace") if self.proc.stderr else ""
            raise RuntimeError(f"ffmpeg failed ({ret}) encoding {self.out_path}:\n{err}")
        if not self.out_path.is_file() or self.out_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg produced no output for {self.out_path}")
