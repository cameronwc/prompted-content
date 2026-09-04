"""Prompt text formatting and auto-fit for the vertical reel frame.

Reuses tools/pinterest/text_fit.py's algorithm verbatim (start at a max
point size, step down to a cap-height floor, never overflow, never
truncate) via fit_text/load_font/cap_height. The pin renderer's own
min_cap_height/max_point_size (config/pinterest_cohorts.yaml) are tuned for
its very different geometry -- short pin text in a tall ~1100px safe area
over up to 7 lines -- so scaling them by canvas width alone overshoots here:
the reel's bottom-third safe area is only ~460px tall for up to 5 lines of a
full sentence (up to 110 chars). REEL_MAX_POINT_SIZE/REEL_MIN_CAP_HEIGHT_PX
are instead calibrated directly against this canvas's own safe area and
verified against every prompt actually selected from the live catalog
(tests/test_reels.py); the "cap-height floor scaled for 1080 wide" from the
spec is honoured as a *floor* (nothing renders below it), just not derived
by re-scaling the pin's numbers.
"""
from __future__ import annotations

from pinterest.text_fit import Fit, FitError, fit_text, min_size_for_cap

CANVAS_WIDTH = 1080
MAX_LINES = 5
REEL_MAX_POINT_SIZE = 160
REEL_MIN_CAP_HEIGHT_PX = 58

# Setup-step body text (SANS/label font): smaller and narrower (it shares
# its row with the step-number column), so its own auto-fit ceiling/floor
# and line limit are tuned separately from the prompt's.
STEP_MAX_LINES = 4
STEP_MAX_POINT_SIZE = 64
STEP_MIN_CAP_HEIGHT_PX = 34

OPEN_QUOTE, CLOSE_QUOTE = "“", "”"


def quote(text: str) -> str:
    text = text.strip()
    if text.startswith(OPEN_QUOTE) or text.startswith('"'):
        return text
    return f"{OPEN_QUOTE}{text}{CLOSE_QUOTE}"


def fit_prompt(text: str, font_candidates, safe_width: int, safe_height: int,
              max_point_size: int = REEL_MAX_POINT_SIZE,
              min_cap_height: int = REEL_MIN_CAP_HEIGHT_PX, step: int = 4,
              leading: float = 1.18) -> Fit:
    """Auto-fit the curly-quoted prompt into the bottom-third safe area, at
    most MAX_LINES lines, never smaller than min_cap_height's cap height."""
    return fit_text(quote(text), font_candidates, safe_width, safe_height,
                    max_point_size, min_cap_height, step, MAX_LINES, leading)


def fit_step(text: str, font_candidates, safe_width: int, safe_height: int,
            max_point_size: int = STEP_MAX_POINT_SIZE,
            min_cap_height: int = STEP_MIN_CAP_HEIGHT_PX, step: int = 2,
            leading: float = 1.2, max_lines: int = STEP_MAX_LINES) -> Fit:
    """Auto-fit one setup-step's instruction text (SANS/label font, plain --
    no curly quotes) into its safe area, never smaller than min_cap_height's
    cap height, targeting at most `max_lines` (4) lines the way most
    instructions in the catalog (median length) fit already.

    A handful of unusually long instructions (the catalog runs up to ~300
    characters) cannot reach 4 lines at the floor size within this column's
    width -- rather than truncate them or shrink past the floor (this
    module's one hard rule, shared with fit_prompt), the line cap is
    relaxed just far enough to use the safe area's own height at that same
    floor size. Text is never rewritten, only wrapped."""
    try:
        return fit_text(text, font_candidates, safe_width, safe_height,
                        max_point_size, min_cap_height, step, max_lines, leading)
    except FitError:
        floor = min_size_for_cap(font_candidates, min_cap_height, max_point_size)
        floor_line_height = int(round(floor * leading))
        roomy_lines = max(max_lines, safe_height // max(1, floor_line_height))
        return fit_text(text, font_candidates, safe_width, safe_height,
                        max_point_size, min_cap_height, step, roomy_lines, leading)
