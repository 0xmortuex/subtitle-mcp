"""Measuring a subtitle and listing what is wrong with it."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .files import RawInfo
from .formats import has_blank_line
from .model import Cue, Subtitle
from .timecode import format_time

# HTML-ish tags (<i>, </font>, <font color="red">, <v Bob>, <c.yellow>), WebVTT inline
# timestamps (<00:00:01.000>) and ASS-style override blocks ({\an8}).
MARKUP_RE = re.compile(r"</?[A-Za-z][^<>]*>|<\d+:\d\d(?::\d\d)?\.\d+>|\{\\[^{}]*\}")

DEFAULT_MAX_LINE = 42
DEFAULT_MAX_CPS = 25.0
LONG_CUE_MS = 10_000


def strip_markup(text: str) -> str:
    return MARKUP_RE.sub("", text)


def visible_chars(text: str) -> int:
    """Characters a viewer has to read: markup and line breaks do not count."""
    return len(strip_markup(text).replace("\n", ""))


def cps(cue: Cue) -> float | None:
    dur = cue.end - cue.start
    if dur <= 0:
        return None
    return visible_chars(cue.text) * 1000 / dur


def time_str(ms: int, fmt: str) -> str:
    return format_time(ms, "." if fmt == "vtt" else ",")


@dataclass
class Problem:
    kind: str
    message: str
    positions: list[int] = field(default_factory=list)  # 1-based cue numbers
    count: int = 0
    fixable: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "count": self.count,
            "fixable": self.fixable,
            "message": self.message,
            "cues": self.positions[:10],
        }
        if self.count > 10:
            d["cues_truncated"] = True
        return d


def _problem(kind: str, message: str, positions: list[int], fixable: bool = True) -> Problem:
    return Problem(kind, message, positions, len(positions), fixable)


def sorted_pairs(sub: Subtitle) -> list[tuple[int, Cue]]:
    """(1-based file position, cue) in start-time order."""
    return sorted(enumerate(sub.cues, 1), key=lambda p: (p[1].start, p[1].end))


def overlaps(sub: Subtitle) -> list[tuple[int, int]]:
    pairs = sorted_pairs(sub)
    return [
        (a[0], b[0]) for a, b in zip(pairs, pairs[1:], strict=False) if b[1].start < a[1].end
    ]


def detect_problems(
    sub: Subtitle,
    raw: RawInfo | None,
    max_line_length: int = DEFAULT_MAX_LINE,
    max_cps: float = DEFAULT_MAX_CPS,
) -> list[Problem]:
    out: list[Problem] = []
    cues = sub.cues
    if raw is not None:
        if raw.encoding.bom:
            out.append(
                Problem("bom", "the file starts with a byte order mark (BOM); some players "
                        "show it as a stray character", [], 1)
            )
        if raw.mixed_newlines:
            out.append(
                Problem(
                    "mixed_newlines",
                    f"mixed line endings (CRLF {raw.crlf}, LF {raw.lf}, CR {raw.cr})",
                    [], 1,
                )
            )
    empty = [i for i, c in enumerate(cues, 1) if not c.text.strip()]
    if empty:
        out.append(_problem("empty_cue", "cue has no text", empty))
    blank = [i for i, c in enumerate(cues, 1) if has_blank_line(c.text) and c.text.strip()]
    if blank:
        out.append(_problem("blank_line_in_cue", "blank line inside the cue text (breaks most players)", blank))
    if any(b.start < a.start for a, b in zip(cues, cues[1:], strict=False)):
        out_of_order = [i + 1 for i, (a, b) in enumerate(zip(cues, cues[1:], strict=False), 1) if b.start < a.start]
        out.append(_problem("out_of_order", "cue starts before the cue above it", out_of_order))
    bad_dur = [i for i, c in enumerate(cues, 1) if c.end <= c.start]
    if bad_dur:
        out.append(_problem("bad_duration", "end time is not after the start time", bad_dur))
    ov = overlaps(sub)
    if ov:
        out.append(_problem("overlap", "cue starts before the previous cue (in time order) ends", [b for _, b in ov]))
    if sub.format == "srt":
        missing = [i for i, c in enumerate(cues, 1) if c.index is None]
        if missing:
            out.append(_problem("index_missing", "cue has no index number", missing))
        seen: dict[int, int] = {}
        dup: list[int] = []
        for i, c in enumerate(cues, 1):
            if c.index is not None:
                if c.index in seen:
                    dup.append(i)
                seen[c.index] = i
        if dup:
            out.append(_problem("index_duplicate", "cue repeats an index number used earlier", dup))
        wrong = [i for i, c in enumerate(cues, 1) if c.index is not None and c.index != i]
        if wrong and not missing and not dup:
            out.append(_problem("index_sequence", "index numbers are not 1, 2, 3, ... in order", wrong))
    long_lines = [
        i for i, c in enumerate(cues, 1)
        if any(len(strip_markup(ln)) > max_line_length for ln in c.text.split("\n"))
    ]
    if long_lines:
        out.append(_problem("long_line", f"a line is longer than {max_line_length} characters", long_lines))
    fast = [i for i, c in enumerate(cues, 1) if (v := cps(c)) is not None and v > max_cps]
    if fast:
        out.append(
            _problem("reading_speed", f"reads faster than {max_cps:g} characters/second "
                     "(flagged only; fixing it needs editing the text or the timing by hand)", fast, False)
        )
    longc = [i for i, c in enumerate(cues, 1) if c.end - c.start > LONG_CUE_MS]
    if longc:
        out.append(_problem("long_cue", f"cue stays on screen longer than {LONG_CUE_MS // 1000} s (flagged only)", longc, False))
    return out


def _stats(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    return {
        "min": round(s[0], 2),
        "mean": round(statistics.fmean(s), 2),
        "median": round(statistics.median(s), 2),
        "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
        "max": round(s[-1], 2),
    }


def analyze(
    sub: Subtitle,
    raw: RawInfo,
    max_line_length: int = DEFAULT_MAX_LINE,
    max_cps: float = DEFAULT_MAX_CPS,
) -> dict[str, Any]:
    fmt = sub.format
    cues = sub.cues
    info: dict[str, Any] = {
        "format": fmt,
        "encoding": {
            "name": raw.encoding.name,
            "confidence": raw.encoding.confidence,
            "reason": raw.encoding.reason,
            "bom": raw.encoding.bom,
        },
        "newlines": {
            "dominant": {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[raw.newline],
            "mixed": raw.mixed_newlines,
            "crlf": raw.crlf, "lf": raw.lf, "cr": raw.cr,
        },
        "cue_count": len(cues),
    }
    if cues:
        first = min(c.start for c in cues)
        last = max(c.end for c in cues)
        info["first_start"] = time_str(first, fmt)
        info["last_end"] = time_str(last, fmt)
        info["span_ms"] = last - first
        info["total_on_screen_ms"] = sum(max(0, c.end - c.start) for c in cues)
        ov = overlaps(sub)
        info["overlaps"] = {"count": len(ov), "first_pairs": [list(p) for p in ov[:5]]}
        pairs = sorted_pairs(sub)
        gaps = [(b[1].start - a[1].end, a[0]) for a, b in zip(pairs, pairs[1:], strict=False) if b[1].start > a[1].end]
        if gaps:
            longest = max(gaps)
            info["gaps"] = {
                "count": len(gaps),
                "total_ms": sum(g for g, _ in gaps),
                "longest_ms": longest[0],
                "longest_after_cue": longest[1],
            }
        else:
            info["gaps"] = {"count": 0}
        info["max_line_length"] = max(
            len(strip_markup(ln)) for c in cues for ln in (c.text.split("\n") or [""])
        )
        speeds = [v for c in cues if (v := cps(c)) is not None]
        if speeds:
            rs = _stats(speeds)
            rs["limit"] = max_cps
            rs["cues_over_limit"] = sum(1 for v in speeds if v > max_cps)
            info["reading_speed_chars_per_sec"] = rs
    problems = detect_problems(sub, raw, max_line_length, max_cps)
    info["problems"] = [p.to_dict() for p in problems]
    info["parse_warnings"] = sub.warnings[:30]
    if len(sub.warnings) > 30:
        info["parse_warnings_truncated"] = len(sub.warnings) - 30
    return info
