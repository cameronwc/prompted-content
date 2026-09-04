"""Typography auto-fit for text pins.

Start at the maximum point size and step down until the wrapped text fits
the safe area within the line limit. The floor is a *cap height* in pixels
(config `min_cap_height`) rather than a point size, so it holds for whatever
font is available. Wrapping is word-based (never mid-word); a trailing
single-word line is rebalanced by narrowing the wrap width at the same size
before stepping down. If the floor still overflows, `FitError` is raised and
the caller skips the prompt — a pin is never rendered overflowing,
truncated, or below the floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


class FitError(ValueError):
    pass


@dataclass
class Fit:
    size: int
    lines: list[str]
    line_height: int
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    cap_height: int

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines)


@lru_cache(maxsize=None)
def resolve_font_path(candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


@lru_cache(maxsize=512)
def _load(path: str | None, size: int):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow: fixed-size bitmap font
        return ImageFont.load_default()


def load_font(candidates: list[str] | tuple[str, ...], size: int):
    return _load(resolve_font_path(tuple(candidates)), size)


def cap_height(font) -> int:
    """Rendered height of a capital H, in pixels."""
    left, top, right, bottom = font.getbbox("H")
    return bottom - top


def min_size_for_cap(candidates, min_cap: int, max_size: int) -> int:
    """Smallest point size whose cap height reaches min_cap (at most max_size)."""
    lo, hi = 1, max_size
    if cap_height(load_font(candidates, hi)) < min_cap:
        return hi + 1  # unreachable: caller will fail the fit
    while lo < hi:
        mid = (lo + hi) // 2
        if cap_height(load_font(candidates, mid)) >= min_cap:
            hi = mid
        else:
            lo = mid + 1
    return lo


def text_width(font, text: str) -> int:
    left, _, right, _ = font.getbbox(text)
    return right - left


def tokens(text: str) -> list[tuple[str, bool]]:
    """(piece, glue) pairs; glue=True means the piece continues the previous
    one without a space (a break after a hyphen or em dash)."""
    out: list[tuple[str, bool]] = []
    for word in text.split():
        pieces, buf = [], ""
        for ch in word:
            buf += ch
            if ch in "—-" and len(buf) > 2:
                pieces.append(buf)
                buf = ""
        if buf:
            pieces.append(buf)
        for i, piece in enumerate(pieces):
            out.append((piece, i > 0))
    return out


def wrap(font, text: str, max_width: int) -> list[str] | None:
    """Greedy word wrap that may break after a hyphen or em dash. None if a
    single unbreakable piece is wider than max_width."""
    lines: list[str] = []
    current = ""
    for piece, glue in tokens(text):
        if text_width(font, piece) > max_width:
            return None
        trial = current + piece if (glue or not current) else current + " " + piece
        if current and text_width(font, trial) > max_width:
            lines.append(current)
            current = piece
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def has_orphan(lines: list[str]) -> bool:
    return len(lines) > 1 and len(lines[-1].split()) == 1


def _try(font, size, text, safe_width, safe_height, max_lines, leading, allow_orphan):
    line_height = int(round(size * leading))
    for shrink in (1.0, 0.94, 0.88):
        lines = wrap(font, text, int(safe_width * shrink))
        if lines is None or len(lines) > max_lines or line_height * len(lines) > safe_height:
            return None
        if has_orphan(lines) and not allow_orphan:
            continue
        return Fit(size=size, lines=lines, line_height=line_height, font=font,
                   cap_height=cap_height(font))
    return None


def fit_text(text: str, font_candidates, safe_width: int, safe_height: int,
             max_size: int, min_cap: int, step: int, max_lines: int,
             leading: float) -> Fit:
    text = " ".join(text.split())
    if not text:
        raise FitError("empty text")
    floor = min_size_for_cap(font_candidates, min_cap, max_size)
    if floor > max_size:
        raise FitError(f"font cannot reach a {min_cap}px cap height at {max_size}pt")
    sizes = list(range(max_size, floor - 1, -step))
    if sizes[-1] != floor:
        sizes.append(floor)
    for size in sizes:
        fit = _try(load_font(font_candidates, size), size, text, safe_width, safe_height,
                   max_lines, leading, allow_orphan=False)
        if fit:
            return fit
    # At the floor, accept an orphan rather than fail — but never go smaller.
    fit = _try(load_font(font_candidates, floor), floor, text, safe_width, safe_height,
               max_lines, leading, allow_orphan=True)
    if fit:
        return fit
    raise FitError(f"text does not fit the safe area at the {min_cap}px cap-height floor "
                   f"({floor}pt, {len(text)} chars)")
