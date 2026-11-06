"""Timestamps and frame rates. Every time in this package is an integer number of
milliseconds; every piece of arithmetic that must be exact uses fractions.Fraction."""

from __future__ import annotations

import math
import re
from fractions import Fraction

from .errors import SubtitleError

# [H+:]MM:SS[.,]fraction. Hours may have any number of digits (hour > 99 is legal in
# practice), minutes and seconds one or two. The fraction is a decimal fraction of a
# second with any number of digits ("5" is 500 ms, "0005" rounds to 1 ms).
TS_PATTERN = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?"
_SECONDS_ONLY = re.compile(r"\d+(?:\.\d+)?")
_TS_FULL = re.compile(TS_PATTERN)


def round_half_up(x: Fraction) -> int:
    """Round to the nearest integer, halves toward +infinity (0.5 -> 1)."""
    return math.floor(x + Fraction(1, 2))


def round_half_away(x: Fraction) -> int:
    """Round to the nearest integer, halves away from zero (-0.5 -> -1)."""
    r = round_half_up(abs(x))
    return -r if x < 0 else r


def ts_from_groups(h: str | None, m: str, s: str, frac: str | None) -> int:
    ms = (int(h or 0) * 3600 + int(m) * 60 + int(s)) * 1000
    if frac:
        ms += round_half_up(Fraction(int(frac), 10 ** len(frac)) * 1000)
    return ms


def format_time(ms: int, sep: str = ",") -> str:
    """HH:MM:SS,mmm (SRT, sep=',') or HH:MM:SS.mmm (WebVTT, sep='.'). Hours are at
    least two digits and grow past 99 when needed."""
    if ms < 0:
        raise SubtitleError(f"a time of {ms} ms is before 00:00:00 and cannot be written")
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def parse_time_arg(value: str, what: str = "time") -> int:
    """Parse a time given to a tool: 'HH:MM:SS,mmm', 'HH:MM:SS.mmm', 'MM:SS.mmm' or
    plain seconds ('62.5')."""
    text = value.strip()
    if _SECONDS_ONLY.fullmatch(text):
        return round_half_up(Fraction(text) * 1000)
    m = _TS_FULL.fullmatch(text)
    if not m:
        raise SubtitleError(
            f"cannot read {what} {value!r}: use HH:MM:SS,mmm (for example 00:01:02,500), "
            "MM:SS.mmm, or plain seconds such as 62.5"
        )
    return ts_from_groups(*m.groups())


_NTSC = [Fraction(n, 1001) for n in (24000, 30000, 48000, 60000, 120000)]


def fps_fraction(fps: float) -> Fraction:
    """Exact frame rate. 23.976, 29.97, 59.94 and friends map to n/1001 exactly."""
    if not math.isfinite(fps) or fps <= 0:
        raise SubtitleError(f"fps must be a positive number, got {fps}")
    for ntsc in _NTSC:
        if abs(fps - float(ntsc)) < 0.01:
            return ntsc
    return Fraction(str(fps))


def frames_to_ms(frames: int, fps: float) -> int:
    """Duration of a number of frames in whole milliseconds (halves away from zero)."""
    return round_half_away(Fraction(frames * 1000) / fps_fraction(fps))
