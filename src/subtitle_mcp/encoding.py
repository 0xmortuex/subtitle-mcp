"""Text encoding detection and conversion.

The heuristic, in order (the first rule that fires wins):

1. A byte order mark: UTF-8, UTF-16 or UTF-32. Certain.
2. Many NUL bytes (30%+): BOM-less UTF-16, endianness from where the NULs sit.
3. Valid UTF-8. Legacy single-byte text is almost never valid UTF-8 once it holds a
   non-ASCII letter, so this is a strong signal. Pure ASCII is labelled as such.
4. Legacy single-byte text (cannot be UTF-8):
   a. Central European (iso-8859-2): letters like A-ogonek, l-stroke, s-acute, z-dot
      live at 0xA1-0xBF, where Latin-1/cp1252 only have symbols that never sit between
      two letters in real text. Two or more letter-symbol-letter hits choose iso-8859-2.
   b. Turkish (cp1254): the bytes D0 DD DE F0 FD FE are G-breve, dotted I, S-cedilla,
      g-breve, dotless i, s-cedilla in cp1254 but the rare Icelandic letters in cp1252.
      Any of them chooses cp1254.
   c. Long runs of high bytes (a word made only of non-ASCII bytes) mean a non-Latin
      script (Cyrillic, Greek, ...). The server refuses to guess which one.
   d. Bytes cp1252 leaves undefined (0x81 0x8D 0x8F 0x90 0x9D): refuse to guess.
   e. Otherwise cp1252. Turkish text that happens to contain none of its six special
      letters decodes identically, so this is harmless; Latin-1 and cp1252 differ only
      in 0x80-0x9F (smart quotes), where cp1252 is the sane reading.

It cannot tell iso-8859-1 from iso-8859-15, nor cp1251 from iso-8859-5, and it says so
instead of guessing: pass `encoding` when you know better.
"""

from __future__ import annotations

import codecs
from collections import Counter
from dataclasses import dataclass

from .errors import SubtitleError

_TURKISH_BYTES = (0xD0, 0xDD, 0xDE, 0xF0, 0xFD, 0xFE)
_LATIN2_LETTER_BYTES = frozenset(
    (0xA1, 0xA3, 0xA5, 0xA6, 0xA9, 0xAB, 0xAC, 0xAE, 0xAF)
    + (0xB1, 0xB3, 0xB5, 0xB6, 0xB9, 0xBB, 0xBC, 0xBE, 0xBF)
)
_CP1252_UNDEFINED = (0x81, 0x8D, 0x8F, 0x90, 0x9D)


@dataclass(frozen=True)
class EncodingGuess:
    name: str  # a Python codec name usable to decode the bytes
    confidence: str  # certain | high | medium | user-specified
    reason: str
    bom: bool = False


def _is_letter_like(b: int) -> bool:
    """ASCII letter, or a byte that is a letter in every Latin-1-like code page (0xC0-0xFF
    except the multiplication and division signs)."""
    return 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A or (0xC0 <= b <= 0xFF and b not in (0xD7, 0xF7))


def _high_byte_runs(data: bytes) -> tuple[int, int]:
    """(high bytes that sit in runs of 3+ consecutive high bytes, all high bytes)."""
    in_runs = total = run = 0
    for b in data + b"\x00":
        if b >= 0x80:
            run += 1
            total += 1
        else:
            if run >= 3:
                in_runs += run
            run = 0
    return in_runs, total


def detect_encoding(data: bytes) -> EncodingGuess:
    if data.startswith(codecs.BOM_UTF8):
        return EncodingGuess("utf-8-sig", "certain", "UTF-8 byte order mark", True)
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return EncodingGuess("utf-32", "certain", "UTF-32 byte order mark", True)
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return EncodingGuess("utf-16", "certain", "UTF-16 byte order mark", True)
    if data and data.count(0) * 10 >= len(data) * 3:
        name = "utf-16-le" if data[1::2].count(0) > data[0::2].count(0) else "utf-16-be"
        try:
            data.decode(name)
            return EncodingGuess(name, "high", "no byte order mark, but 30%+ NUL bytes (UTF-16)")
        except UnicodeDecodeError:
            pass
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if all(b < 0x80 for b in data):
            return EncodingGuess(
                "utf-8", "certain", "pure ASCII, valid in UTF-8 and every legacy encoding"
            )
        return EncodingGuess("utf-8", "high", "valid UTF-8 containing multi-byte sequences")

    counts = Counter(b for b in data if b >= 0x80)
    high_total = sum(counts.values())
    in_runs, total = _high_byte_runs(data)
    if in_runs >= 6 and in_runs * 10 >= total * 6:
        raise SubtitleError(
            "this file looks like a non-Latin legacy encoding (long runs of non-ASCII bytes: "
            "Cyrillic, Greek, ...) and I cannot tell which one. Retry with encoding= set, for "
            "example 'cp1251' or 'iso-8859-5' (Cyrillic), 'cp1253' or 'iso-8859-7' (Greek), "
            "'cp1255' (Hebrew), 'cp1256' (Arabic)."
        )
    mid_word_latin2 = 0
    for i in range(1, len(data) - 1):
        if (
            data[i] in _LATIN2_LETTER_BYTES
            and _is_letter_like(data[i - 1])
            and _is_letter_like(data[i + 1])
        ):
            mid_word_latin2 += 1
    if mid_word_latin2 >= 2:
        try:
            data.decode("iso-8859-2")
            return EncodingGuess(
                "iso-8859-2",
                "medium",
                f"{mid_word_latin2} bytes in 0xA1-0xBF sit between two letters, which only "
                "happens with Central European letters (Polish, Czech, ...)",
            )
        except UnicodeDecodeError:
            pass
    turkish = sum(counts[b] for b in _TURKISH_BYTES)
    if turkish:
        try:
            data.decode("cp1254")
            return EncodingGuess(
                "cp1254",
                "high" if turkish >= 3 else "medium",
                f"{turkish} bytes are the Turkish letters G-breve, dotted I, S-cedilla, "
                "g-breve, dotless i, s-cedilla (rare Icelandic letters in cp1252); iso-8859-9 "
                "would decode the same unless bytes 0x80-0x9F are present",
            )
        except UnicodeDecodeError:
            pass
    if any(counts[b] for b in _CP1252_UNDEFINED):
        raise SubtitleError(
            "this file is not UTF-8 and contains bytes that no common Western encoding "
            "defines, so I cannot guess its encoding. Retry with encoding= set (for example "
            "'cp1250', 'cp1251', 'cp1254', 'iso-8859-2')."
        )
    return EncodingGuess(
        "cp1252",
        "medium" if high_total else "certain",
        "not UTF-8, no Turkish-only or Central European evidence: assuming Western European "
        "cp1252 (a Turkish file without its six special letters decodes identically)",
    )


def check_codec(name: str) -> str:
    try:
        return codecs.lookup(name).name
    except LookupError:
        raise SubtitleError(
            f"unknown encoding {name!r}; use a Python codec name such as 'utf-8', 'utf-8-sig', "
            "'utf-16', 'cp1254', 'cp1252', 'iso-8859-9'"
        ) from None


def decode(data: bytes, encoding: str | None) -> tuple[str, EncodingGuess]:
    """Decode with an explicit encoding, or detect one. Never substitutes characters."""
    if encoding is None:
        guess = detect_encoding(data)
    else:
        check_codec(encoding)
        has_bom = data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
        guess = EncodingGuess(encoding, "user-specified", "explicit encoding= override", has_bom)
    try:
        return data.decode(guess.name), guess
    except UnicodeDecodeError as e:
        raise SubtitleError(
            f"cannot decode the file as {guess.name} (byte 0x{data[e.start]:02x} at offset "
            f"{e.start}). Pass the right encoding=, for example 'cp1254' (Turkish), "
            "'cp1252' or 'iso-8859-2'."
        ) from e


def encode(text: str, encoding: str) -> bytes:
    check_codec(encoding)
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as e:
        raise SubtitleError(
            f"the character {text[e.start]!r} cannot be written as {encoding}. "
            "Use output_encoding='utf-8' (the default), which can hold every character."
        ) from e
