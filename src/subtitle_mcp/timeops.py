"""Shifting and resynchronising: exact linear time maps on integer milliseconds."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from .errors import SubtitleError
from .model import Subtitle
from .timecode import fps_fraction, format_time, round_half_up


@dataclass(frozen=True)
class LinearMap:
    """t' = a*t + b, with a and b exact fractions. Results are rounded half up to a
    whole millisecond only at the very end."""

    a: Fraction
    b: Fraction

    def apply(self, t: int) -> int:
        return round_half_up(self.a * t + self.b)

    def exact(self, t: int) -> Fraction:
        return self.a * t + self.b


def fit_linear(points: Sequence[tuple[int, int]]) -> LinearMap:
    """Least-squares line through (old_time, new_time) points, in exact arithmetic.
    With exactly two points the line passes through both."""
    n = len(points)
    if n < 2:
        raise SubtitleError("resync needs at least two anchor points")
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        raise SubtitleError(
            "the anchor points all have the same current time, so no line can be fitted; "
            "choose anchors at clearly different places in the file (ideally near the start and the end)"
        )
    a = Fraction(n * sxy - sx * sy, denom)
    b = (Fraction(sy) - a * sx) / n
    return LinearMap(a, b)


def residuals(m: LinearMap, points: Sequence[tuple[int, int]]) -> list[int]:
    """Signed error in ms at each anchor after rounding: applied time minus wanted time."""
    return [m.apply(x) - y for x, y in points]


def fps_ratio(fps_from: float, fps_to: float) -> LinearMap:
    """Subtitles timed for a video at fps_from, to be shown on the same frames of a
    video at fps_to: frame n sits at n/fps_from in the file and n/fps_to in the video."""
    return LinearMap(fps_fraction(fps_from) / fps_fraction(fps_to), Fraction(0))


def apply_map(sub: Subtitle, m: LinearMap, from_index: int = 0) -> int:
    """Apply a linear map to the cues from 0-based from_index onward, in place. Raises
    if any time would become negative. Returns the number of cues changed."""
    new: list[tuple[int, int]] = []
    for pos in range(from_index, len(sub.cues)):
        c = sub.cues[pos]
        s, e = m.apply(c.start), m.apply(c.end)
        if s < 0 or e < 0:
            raise SubtitleError(
                f"cue {pos + 1} would start at {s} ms and end at {e} ms, before "
                f"{format_time(0)}. Use a smaller negative offset, or apply it only from a "
                "later cue."
            )
        new.append((s, e))
    for (pos, (s, e)) in zip(range(from_index, len(sub.cues)), new, strict=True):
        sub.cues[pos].start, sub.cues[pos].end = s, e
    return len(new)
