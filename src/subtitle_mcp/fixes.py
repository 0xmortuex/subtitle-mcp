"""Repairing common subtitle problems. Every repair is recorded as a Fix so a dry run
can list exactly what would change."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analysis import (
    MARKUP_RE,
    DEFAULT_MAX_CPS,
    DEFAULT_MAX_LINE,
    LONG_CUE_MS,
    cps,
    detect_problems,
    strip_markup,
    time_str,
)
from .files import RawInfo
from .formats import has_blank_line
from .model import Subtitle

MIN_FIXED_DURATION_MS = 1000
MAX_FIXED_DURATION_MS = 7000
FIXED_DURATION_CPS = 15  # a repaired cue is shown long enough to read at this speed


# A "word" for wrapping: non-space characters, where a whole tag (which may contain
# spaces, like <font color="red">) counts as part of the word it touches.
_WORD_RE = re.compile("(?:" + MARKUP_RE.pattern + r"|\S)+")


@dataclass
class Fix:
    kind: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class FixReport:
    fixes: list[Fix] = field(default_factory=list)
    flagged: list[Fix] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)


def balanced_wrap(text: str, width: int) -> list[str]:
    """Wrap one paragraph into the fewest lines of at most `width` visible characters,
    then narrow the target width as far as that line count allows, so lines come out
    balanced rather than one full line and one stub. Markup does not count toward
    width. A single word longer than `width` stays on its own (over-long) line."""
    words = _WORD_RE.findall(text)
    if not words:
        return []

    def greedy(w: int) -> list[str]:
        lines: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for word in words:
            wl = len(strip_markup(word))
            if cur and cur_len + 1 + wl > w:
                lines.append(" ".join(cur))
                cur, cur_len = [word], wl
            else:
                cur_len = wl if not cur else cur_len + 1 + wl
                cur.append(word)
        lines.append(" ".join(cur))
        return lines

    best = greedy(width)
    lo = max(len(strip_markup(w)) for w in words)
    for w in range(min(lo, width), width + 1):
        cand = greedy(w)
        if len(cand) == len(best):
            return cand
    return best


def _duration_for(text: str) -> int:
    ms = round(len(strip_markup(text).replace("\n", "")) * 1000 / FIXED_DURATION_CPS)
    return max(MIN_FIXED_DURATION_MS, min(MAX_FIXED_DURATION_MS, ms))


def fix_subtitle(
    sub: Subtitle,
    raw: RawInfo,
    strip_tags: bool = False,
    max_line_length: int = DEFAULT_MAX_LINE,
    max_cps: float = DEFAULT_MAX_CPS,
) -> tuple[Subtitle, FixReport]:
    """Return a repaired copy and the report. The input is not modified."""
    rep = FixReport()
    fmt = sub.format
    out = Subtitle(fmt, [c.copy() for c in sub.cues], sub.header, list(sub.extras), list(sub.warnings))
    n_orig = len(out.cues)
    problems = {p.kind: p for p in detect_problems(sub, raw, max_line_length, max_cps)}

    if "bom" in problems:
        rep.fixes.append(Fix("bom", "byte order mark will not be written (output is UTF-8 without BOM by default)"))
    if "mixed_newlines" in problems:
        nl = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[raw.newline]
        rep.fixes.append(Fix("mixed_newlines", f"all line endings normalised to {nl} (the most common one in the file)"))

    # Work on (original position, cue) pairs so details quote the numbers the user saw.
    items = list(enumerate(out.cues, 1))

    empties = [(i, c) for i, c in items if not c.text.strip()]
    if empties:
        rep.fixes.append(Fix("empty_cue", f"removed {len(empties)} cue(s) with no text",
                             [f"cue {i} at {time_str(c.start, fmt)}" for i, c in empties]))
        items = [(i, c) for i, c in items if c.text.strip()]

    blanks = [(i, c) for i, c in items if has_blank_line(c.text)]
    if blanks:
        for _, c in blanks:
            c.text = "\n".join(ln for ln in c.text.split("\n") if ln.strip())
        rep.fixes.append(Fix("blank_line_in_cue", f"removed blank lines inside {len(blanks)} cue(s)",
                             [f"cue {i}" for i, _ in blanks]))

    moved = [i for (_, a), (i, b) in zip(items, items[1:], strict=False) if b.start < a.start]
    if moved:
        items.sort(key=lambda p: (p[1].start, p[1].end))  # stable
        rep.fixes.append(Fix("out_of_order", f"re-sorted cues by start time ({len(moved)} cue(s) were out of order)",
                             [f"cue {i} started before the cue above it" for i in moved]))

    changed: list[str] = []
    for k, (i, c) in enumerate(items):
        if c.end <= c.start:
            new_end = c.start + _duration_for(c.text)
            nxt = items[k + 1][1] if k + 1 < len(items) else None
            if nxt is not None and nxt.start > c.start:
                new_end = min(new_end, nxt.start)
            changed.append(f"cue {i}: end {time_str(c.end, fmt)} -> {time_str(new_end, fmt)}")
            c.end = new_end
    if changed:
        rep.fixes.append(Fix("bad_duration", f"gave {len(changed)} zero/negative-length cue(s) a readable duration "
                             f"({MIN_FIXED_DURATION_MS // 1000}-{MAX_FIXED_DURATION_MS // 1000} s at "
                             f"{FIXED_DURATION_CPS} chars/s, never past the next cue)", changed))

    trimmed: list[str] = []
    stuck: list[str] = []
    for (i, c), (j, nxt) in zip(items, items[1:], strict=False):
        if c.end > nxt.start:
            if nxt.start > c.start:
                trimmed.append(f"cue {i}: end {time_str(c.end, fmt)} -> {time_str(nxt.start, fmt)} (cue {j} starts)")
                c.end = nxt.start
            else:
                stuck.append(f"cues {i} and {j} start at the same time")
    if trimmed:
        rep.fixes.append(Fix("overlap", f"trimmed {len(trimmed)} cue(s) that overlapped the next one", trimmed))
    if stuck:
        rep.flagged.append(Fix("overlap_same_start", "overlapping cues that start together (probably two speakers or "
                               "a duplicate; edit by hand)", stuck))

    if strip_tags:
        stripped: list[str] = []
        for i, c in items:
            new = "\n".join(ln for ln in (strip_markup(x) for x in c.text.split("\n")) if ln.strip())
            if new != c.text:
                stripped.append(f"cue {i}")
                c.text = new
        emptied = [(i, c) for i, c in items if not c.text.strip()]
        items = [(i, c) for i, c in items if c.text.strip()]
        if stripped:
            rep.fixes.append(Fix("strip_tags", f"removed HTML/font/WebVTT/ASS tags from {len(stripped)} cue(s)", stripped))
        if emptied:
            rep.fixes.append(Fix("empty_cue", f"removed {len(emptied)} cue(s) that held only tags",
                                 [f"cue {i}" for i, _ in emptied]))

    wrapped: list[str] = []
    unbreakable: list[str] = []
    for i, c in items:
        lines = c.text.split("\n")
        if not any(len(strip_markup(ln)) > max_line_length for ln in lines):
            continue
        dialogue = len(lines) > 1 and all(ln.lstrip().startswith(("-", "–", "—")) for ln in lines)
        if dialogue:
            new_lines = [w for ln in lines for w in balanced_wrap(ln, max_line_length)]
        else:
            new_lines = balanced_wrap(" ".join(lines), max_line_length)
        if any(len(strip_markup(ln)) > max_line_length for ln in new_lines):
            unbreakable.append(f"cue {i} has a single word longer than {max_line_length} characters")
        wrapped.append(f"cue {i}: {len(lines)} -> {len(new_lines)} line(s)")
        c.text = "\n".join(new_lines)
    if wrapped:
        rep.fixes.append(Fix("long_line", f"re-wrapped {len(wrapped)} cue(s) to at most {max_line_length} "
                             "characters per line (balanced; dialogue dashes keep one speaker per line)", wrapped))
    if unbreakable:
        rep.flagged.append(Fix("unbreakable_word", "words that cannot be wrapped", unbreakable))

    out.cues = [c for _, c in items]
    if fmt == "srt" and any(p in problems for p in ("index_missing", "index_duplicate", "index_sequence")):
        kinds = [k for k in ("index_missing", "index_duplicate", "index_sequence") if k in problems]
        rep.fixes.append(Fix("renumber", "cues renumbered 1..N ("
                             + ", ".join(k.replace("index_", "") for k in kinds) + " indices)"))
    for n, c in enumerate(out.cues, 1):
        c.index = n if fmt == "srt" else None

    # Flag-only: judged on the repaired cues, numbered as in the repaired file.
    fast = [(n, v) for n, c in enumerate(out.cues, 1) if (v := cps(c)) is not None and v > max_cps]
    if fast:
        rep.flagged.append(Fix("reading_speed", f"{len(fast)} cue(s) read faster than {max_cps:g} chars/s "
                               "(not changed; shorten the text or extend the timing)",
                               [f"cue {n}: {v:.1f} chars/s" for n, v in fast]))
    longc = [n for n, c in enumerate(out.cues, 1) if c.end - c.start > LONG_CUE_MS]
    if longc:
        rep.flagged.append(Fix("long_cue", f"{len(longc)} cue(s) stay on screen longer than {LONG_CUE_MS // 1000} s "
                               "(not changed)", [f"cue {n}" for n in longc]))
    rep.remaining = [p.kind for p in detect_problems(out, None, max_line_length, max_cps) if p.fixable]
    if n_orig != len(out.cues):
        out.warnings.append(f"{n_orig - len(out.cues)} cue(s) removed")
    return out, rep
