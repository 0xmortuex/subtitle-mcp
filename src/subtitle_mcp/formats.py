"""Parsers and writers for SRT, WebVTT and (read-only) ASS/SSA.

The SRT and WebVTT parser is one tolerant, block-oriented routine: it finds timing
lines and works outward from them, so a missing index, a blank line between the index
and the timestamps, a blank line inside a cue, dots instead of commas, or two cues
glued together without a separator all still parse. Everything it had to guess or skip
is recorded in Subtitle.warnings.
"""

from __future__ import annotations

import re

from .errors import SubtitleError
from .model import Cue, Subtitle
from .timecode import TS_PATTERN, format_time, ts_from_groups

_TIMING_RE = re.compile(
    r"^\s*" + TS_PATTERN + r"\s*-+>\s*" + TS_PATTERN + r"(?:\s+(.*?))?\s*$"
)
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_EXTRA_RE = re.compile(r"(NOTE|STYLE|REGION)(?:[ \t]|$)")
_ASS_SECTION_RE = re.compile(r"^\s*\[(?:script info|events)\]", re.IGNORECASE | re.MULTILINE)


def split_lines(text: str) -> list[str]:
    """Split on CRLF, CR or LF only (str.splitlines also splits on U+2028 and friends,
    which can legitimately appear inside subtitle text)."""
    return _NEWLINE_RE.split(text)


def sniff_format(text: str) -> str:
    body = text.lstrip("﻿")
    for line in split_lines(body):
        if line.strip():
            if line.startswith("WEBVTT"):
                return "vtt"
            break
    else:
        raise SubtitleError("the file is empty")
    if _ASS_SECTION_RE.search(body) and re.search(r"^\s*Dialogue:", body, re.MULTILINE):
        return "ass"
    if any(_TIMING_RE.match(line) for line in split_lines(body)):
        return "srt"
    raise SubtitleError(
        "no subtitle cues found: expected SRT ('00:00:01,000 --> 00:00:02,000'), WebVTT "
        "(starts with 'WEBVTT') or ASS/SSA (has [Events] with Dialogue lines). If the file is "
        "not UTF-8, the wrong encoding may have been detected: pass encoding=."
    )


def _timing(line: str) -> tuple[int, int, str] | None:
    m = _TIMING_RE.match(line)
    if not m:
        return None
    g = m.groups()
    return ts_from_groups(*g[0:4]), ts_from_groups(*g[4:8]), (g[8] or "").strip()


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if line.strip():
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def parse_cues(text: str, fmt: str) -> Subtitle:
    """Parse SRT (fmt='srt') or WebVTT (fmt='vtt')."""
    vtt = fmt == "vtt"
    sub = Subtitle(format=fmt)
    blocks = _split_blocks(split_lines(text.lstrip("﻿")))
    pending_index: int | None = None
    first = True
    for bi, block in enumerate(blocks):
        if vtt and first and block[0].startswith("WEBVTT"):
            first = False
            timing_at = next((k for k, ln in enumerate(block) if _timing(ln)), None)
            if timing_at is None:
                sub.header = "\n".join(block)
                continue
            sub.header = block[0]
            sub.warnings.append("no blank line after the WEBVTT header")
            block = block[1:]
        first = False
        if vtt and _EXTRA_RE.match(block[0]):
            sub.extras.append((len(sub.cues), "\n".join(block)))
            continue
        timings = [k for k, ln in enumerate(block) if _timing(ln)]
        if not timings:
            nxt = blocks[bi + 1][0] if bi + 1 < len(blocks) else ""
            if len(block) == 1 and block[0].strip().isdigit() and _timing(nxt):
                pending_index = int(block[0].strip())
                continue
            if sub.cues:
                prev = sub.cues[-1]
                prev.text = (prev.text + "\n\n" if prev.text else "\n") + "\n".join(block)
                sub.warnings.append(
                    f"cue {len(sub.cues)}: a blank line inside the cue text was kept in "
                    "the text (run fix_common_problems to remove it)"
                )
            else:
                sub.warnings.append(f"skipped text before the first cue: {block[0].strip()!r}")
            continue
        pre = block[: timings[0]]
        label: str | None = pre[-1].strip() if pre else None
        if len(pre) > 1:
            sub.warnings.append(
                f"cue {len(sub.cues) + 1}: ignored {len(pre) - 1} unexpected line(s) before "
                "the timing line"
            )
        for j, tp in enumerate(timings):
            start, end, settings = _timing(block[tp]) or (0, 0, "")
            cue = Cue(start, end, settings=settings)
            seg_end = timings[j + 1] if j + 1 < len(timings) else len(block)
            body = block[tp + 1 : seg_end]
            next_label: str | None = None
            if j + 1 < len(timings):
                if body and body[-1].strip().isdigit():
                    # "text / next index / next timing" with no blank line between cues
                    next_label = body[-1].strip()
                    body = body[:-1]
                sub.warnings.append(
                    f"cue {len(sub.cues) + 2}: no blank line between it and the previous cue"
                )
            if j == 0 and label is None and pending_index is not None:
                cue.index = pending_index
            if label is not None:
                if not vtt and label.isdigit():
                    cue.index = int(label)
                else:
                    cue.ident = label
                    if not vtt:
                        sub.warnings.append(
                            f"cue {len(sub.cues) + 1}: non-numeric index line {label!r} kept "
                            "as an identifier"
                        )
            cue.text = "\n".join(body)
            sub.cues.append(cue)
            label = next_label
        pending_index = None
    return sub


def write_srt(sub: Subtitle, newline: str = "\n") -> str:
    blocks: list[str] = []
    for i, cue in enumerate(sub.cues, 1):
        timing = f"{format_time(cue.start)} --> {format_time(cue.end)}"
        if cue.settings:
            timing += " " + cue.settings
        lines = [str(i), timing] + _clean_lines(cue.text)
        blocks.append(newline.join(lines))
    if not blocks:
        return ""
    return newline.join(_join_blocks(blocks, newline)) + newline


def write_vtt(sub: Subtitle, newline: str = "\n") -> str:
    blocks: list[str] = [sub.header.replace("\n", newline)]
    extras = sorted(sub.extras, key=lambda e: e[0])  # stable: keeps source order per slot
    ei = 0
    for i, cue in enumerate(sub.cues):
        while ei < len(extras) and extras[ei][0] <= i:
            blocks.append(extras[ei][1].replace("\n", newline))
            ei += 1
        timing = f"{format_time(cue.start, '.')} --> {format_time(cue.end, '.')}"
        if cue.settings:
            timing += " " + cue.settings
        lines = ([cue.ident] if cue.ident else []) + [timing] + _clean_lines(cue.text)
        blocks.append(newline.join(lines))
    while ei < len(extras):
        blocks.append(extras[ei][1].replace("\n", newline))
        ei += 1
    return newline.join(_join_blocks(blocks, newline)) + newline


def _join_blocks(blocks: list[str], newline: str) -> list[str]:
    out: list[str] = []
    for b in blocks:
        out.append(b)
        out.append("")
    return out[:-1]


def _clean_lines(text: str) -> list[str]:
    """Cue text lines without blank lines: a blank line would end the cue in SRT/VTT."""
    return [ln for ln in text.split("\n") if ln.strip()]


def has_blank_line(text: str) -> bool:
    return any(not ln.strip() for ln in text.split("\n")) if text else False


# --- ASS / SSA (read only) ---------------------------------------------------------

_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
_ASS_DRAWING_RE = re.compile(r"\{[^}]*\\p[1-9]")
_ASS_DEFAULT_FIELDS = [
    "layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text",
]


def parse_ass(text: str) -> Subtitle:
    """Read Dialogue lines out of an ASS/SSA file. Only timing and text survive: styles,
    fonts, colours, positioning and effects are discarded (and counted in warnings)."""
    sub = Subtitle(format="ass")
    section = ""
    fields: list[str] | None = None
    comments = drawings = empty = styled = 0
    for raw in split_lines(text.lstrip("﻿")):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        if section != "[events]":
            continue
        low = line.lower()
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in line[7:].split(",")]
        elif low.startswith("comment:"):
            comments += 1
        elif low.startswith("dialogue:"):
            names = fields or _ASS_DEFAULT_FIELDS
            for need in ("start", "end", "text"):
                if need not in names:
                    raise SubtitleError(f"the [Events] Format line has no {need!r} column")
            parts = line[9:].lstrip().split(",", len(names) - 1)
            if len(parts) < len(names):
                sub.warnings.append(f"skipped a malformed Dialogue line: {line[:60]!r}")
                continue
            row = dict(zip(names, parts, strict=True))
            times = []
            for key in ("start", "end"):
                m = re.fullmatch(TS_PATTERN, row[key].strip())
                if not m:
                    raise SubtitleError(f"cannot read ASS time {row[key]!r}")
                times.append(ts_from_groups(*m.groups()))
            body = row["text"]
            if _ASS_DRAWING_RE.search(body):
                drawings += 1
                continue
            if "{" in body:
                styled += 1
            body = _ASS_OVERRIDE_RE.sub("", body)
            body = body.replace("\\N", "\n").replace("\\n", " ").replace("\\h", " ").strip()
            if not body:
                empty += 1
                continue
            sub.cues.append(Cue(times[0], times[1], body))
    if not sub.cues:
        raise SubtitleError("no usable Dialogue lines found in the ASS/SSA [Events] section")
    sub.cues.sort(key=lambda c: (c.start, c.end))  # ASS files are not stored in time order
    sub.warnings.append(
        "ASS/SSA: only timing and text were read; styles, fonts, colours, positions and "
        "effects are discarded"
    )
    for count, what in (
        (styled, "cue(s) had override tags ({...}) that were stripped"),
        (comments, "Comment: line(s) ignored"),
        (drawings, "vector drawing cue(s) skipped"),
        (empty, "cue(s) with no visible text skipped"),
    ):
        if count:
            sub.warnings.append(f"ASS/SSA: {count} {what}")
    return sub


def parse_subtitle(text: str, fmt: str | None = None) -> Subtitle:
    fmt = fmt or sniff_format(text)
    if fmt == "ass":
        return parse_ass(text)
    return parse_cues(text, fmt)


def render(sub: Subtitle, fmt: str, newline: str = "\n") -> str:
    if fmt == "srt":
        return write_srt(sub, newline)
    if fmt == "vtt":
        return write_vtt(sub, newline)
    raise SubtitleError(f"cannot write {fmt!r}: only srt and vtt can be written")


def convert_model(sub: Subtitle, fmt: str) -> tuple[Subtitle, list[str]]:
    """Re-target a subtitle to another format. Returns the new model and a list of
    things that could not be carried over."""
    notes: list[str] = []
    cues = [c.copy() for c in sub.cues]
    if fmt == sub.format:
        return Subtitle(fmt, cues, sub.header, list(sub.extras), list(sub.warnings)), notes
    settings_lost = sum(1 for c in cues if c.settings)
    if settings_lost:
        what = "WebVTT cue settings" if sub.format == "vtt" else "SRT position coordinates"
        notes.append(f"dropped {what} on {settings_lost} cue(s)")
    for c in cues:
        c.settings = ""
        if fmt != "vtt":
            c.ident = None
    out = Subtitle(fmt, cues, warnings=list(sub.warnings))
    if sub.format == "vtt" and sub.extras:
        notes.append(f"dropped {len(sub.extras)} WebVTT NOTE/STYLE/REGION block(s)")
    if sub.format == "vtt" and fmt == "srt":
        if any("<v " in c.text or "<c" in c.text for c in cues):
            notes.append(
                "WebVTT voice/class tags (<v ...>, <c...>) were kept as text; "
                "fix_common_problems(strip_tags=true) removes them"
            )
    return out, notes
