"""The translation contract. The server never translates anything: it hands the AI
numbered batches of cue text and later merges the AI's answers back in, checking
that nothing was lost, invented or damaged on the way.

Cue IDs are the 1-based position of the cue in the file ("1", "2", ...). They are
stable for as long as the file is not edited, and apply_translation checks them
against the file it is given."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .analysis import DEFAULT_MAX_CPS, MARKUP_RE, visible_chars
from .errors import SubtitleError
from .model import Subtitle

MIN_MAX_CHARS = 200
LENGTH_RATIO_HIGH = 1.6
LENGTH_RATIO_LOW = 0.4
RATIO_MIN_SOURCE_CHARS = 12


def make_chunks(sub: Subtitle, max_chars: int) -> list[list[tuple[str, str]]]:
    """Group (id, text) pairs so each group's text totals at most max_chars (a single
    longer cue gets a group of its own). Cues keep file order."""
    if max_chars < MIN_MAX_CHARS:
        raise SubtitleError(f"max_chars must be at least {MIN_MAX_CHARS} (got {max_chars})")
    chunks: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for i, cue in enumerate(sub.cues, 1):
        n = len(cue.text)
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append((str(i), cue.text))
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _tags(text: str) -> list[str]:
    return sorted(MARKUP_RE.findall(text))


def apply_mapping(
    sub: Subtitle, mapping: Mapping[str, str], partial: bool, max_cps: float = DEFAULT_MAX_CPS
) -> tuple[int, list[str]]:
    """Replace cue text in place. Timing, identifiers and settings are untouched.
    Returns (cues replaced, warnings). Raises if the mapping does not fit the file."""
    n = len(sub.cues)
    valid = {str(i) for i in range(1, n + 1)}
    unknown = sorted((k for k in mapping if k not in valid), key=lambda k: (len(k), k))
    if unknown:
        raise SubtitleError(
            f"the mapping has {len(unknown)} ID(s) that are not cues of this file "
            f"(it has {n} cues, IDs '1'..'{n}'): {', '.join(unknown[:10])}"
            + (" ..." if len(unknown) > 10 else "")
            + ". Was the file edited after chunk_for_translation, or is this a different file?"
        )
    bad_type = [k for k, v in mapping.items() if not isinstance(v, str)]
    if bad_type:
        raise SubtitleError(f"the translation for ID(s) {', '.join(bad_type[:10])} is not a string")
    missing = [str(i) for i in range(1, n + 1) if str(i) not in mapping]
    if missing and not partial:
        raise SubtitleError(
            f"{len(missing)} of {n} cue(s) have no translation: IDs {', '.join(missing[:20])}"
            + (" ..." if len(missing) > 20 else "")
            + ". Translate every cue, or pass partial=true to keep the untranslated ones as they are."
        )
    warnings: list[str] = []
    replaced = 0
    for i, cue in enumerate(sub.cues, 1):
        new = mapping.get(str(i))
        if new is None:
            continue
        new = new.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        src_chars = len(cue.text.replace("\n", ""))
        new_chars = len(new.replace("\n", ""))
        if not new.strip() and cue.text.strip():
            warnings.append(f"cue {i}: the translation is empty but the source has text")
        elif src_chars >= RATIO_MIN_SOURCE_CHARS:
            ratio = new_chars / src_chars
            if ratio > LENGTH_RATIO_HIGH or ratio < LENGTH_RATIO_LOW:
                warnings.append(
                    f"cue {i}: length changed a lot ({src_chars} -> {new_chars} characters, "
                    f"x{ratio:.2f}); check for an omission or an addition"
                )
        if _tags(cue.text) != _tags(new):
            warnings.append(
                f"cue {i}: formatting tags differ (source {_tags(cue.text)}, translation {_tags(new)})"
            )
        dur = cue.end - cue.start
        if dur > 0 and visible_chars(new) * 1000 / dur > max_cps and (
            visible_chars(cue.text) * 1000 / dur <= max_cps
        ):
            warnings.append(
                f"cue {i}: the translation reads at {visible_chars(new) * 1000 / dur:.1f} chars/s "
                f"(limit {max_cps:g}); the source did not exceed it"
            )
        cue.text = new
        replaced += 1
    return replaced, warnings


_ID_RE = re.compile(r"\d+")


def check_ids(mapping: Mapping[str, str]) -> None:
    bad = [k for k in mapping if not _ID_RE.fullmatch(str(k))]
    if bad:
        raise SubtitleError(
            f"mapping keys must be cue IDs like '1', '2', ... (got {bad[:5]}). "
            "Use exactly the IDs returned by chunk_for_translation."
        )
