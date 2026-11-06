"""Real-world-like messy subtitle files, written as explicit bytes (never relying on
the checkout's line-ending conversion)."""

from __future__ import annotations

# UTF-8 with BOM, mixed CRLF/LF, duplicate and out-of-order indices, an index separated from
# its timing by a blank line, a blank line inside a cue, dot milliseconds, a missing index,
# a zero-length cue, HTML/font tags, a very long line, an unreadably fast cue and hour 100.
MESSY_SRT = (
    "﻿1\r\n00:00:01,000 --> 00:00:03,000\r\nHello there.\r\n\r\n"
    "2\n00:00:03,500 --> 00:00:03,500\nZero duration cue\n\n"
    "2\r\n00:00:02,000 --> 00:00:04,000\r\n<i>Out of order and duplicate index</i>\r\n\r\n"
    "5\r\n\r\n00:00:06,000 --> 00:00:09,000\r\nIndex separated from timing\r\n\r\n"
    "by a blank line inside the cue.\r\n\r\n"
    "00:00:10.000 --> 00:00:12.500\nNo index and dot milliseconds\n\n"
    "7\n00:00:12,000 --> 00:00:14,000\n"
    '<font color="#ff0000">This is a very long line that definitely needs rewrapping '
    "because it is far too long</font>\n\n"
    "8\n00:00:14,000 --> 00:00:14,500\nWay too fast for anyone to read this sentence completely\n\n"
    "9\n100:00:00,000 --> 100:00:02,000\nHour past ninety-nine\n"
).encode("utf-8")

TURKISH_TEXT_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nGünaydın çocuklar, şimdi gidelim.\n\n"
    "2\n00:00:03,500 --> 00:00:06,000\nİstanbul'da çok güzel bir gün.\n"
    "Ğazi'nin şarkısı öyle...\n\n"
    "3\n00:00:07,000 --> 00:00:08,000\nIŞIK, ışık, Üzüm, Öğretmen\n"
)
TURKISH_CP1254 = TURKISH_TEXT_SRT.encode("cp1254")

POLISH_TEXT_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nZażółć gęślą jaźń.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nWięcej światła, więcej życia.\n"
)
POLISH_ISO8859_2 = POLISH_TEXT_SRT.encode("iso-8859-2")

FRENCH_CP1252 = (
    "1\n00:00:01,000 --> 00:00:03,000\nOù êtes-vous ? J’ai déjà mangé.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nÇa va très bien, merci.\n"
).encode("cp1252")

CYRILLIC_CP1251 = (
    "1\n00:00:01,000 --> 00:00:03,000\nПривет, мир!\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nКак дела сегодня?\n"
).encode("cp1251")

UTF16_SRT = (
    "1\r\n00:00:01,000 --> 00:00:02,000\r\nÜnlü şarkı\r\n"
).encode("utf-16")

VTT_COMPLEX = (
    "WEBVTT - Example captions\n"
    "Kind: captions\n"
    "Language: en\n"
    "\n"
    "STYLE\n"
    "::cue { color: lime }\n"
    "\n"
    "REGION\n"
    "id:top\n"
    "width:40%\n"
    "\n"
    "NOTE This is a comment\n"
    "that spans two lines\n"
    "\n"
    "intro\n"
    "00:01.000 --> 00:03.500 align:start position:10%\n"
    "<v Alice>Welcome to the show.</v>\n"
    "Second line of the cue.\n"
    "\n"
    "2\n"
    "00:00:04.000 --> 00:00:06.250 region:top\n"
    "<c.yellow>Cue with</c> a <00:00:05.000>timestamp tag\n"
    "\n"
    "NOTE between cues\n"
    "\n"
    "01:00:00.000 --> 01:00:02.000\n"
    "An hour in, no identifier.\n"
).encode("utf-8")

ASS_SAMPLE = (
    "[Script Info]\n"
    "Title: Test\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour\n"
    "Style: Default,Arial,20,&H00FFFFFF\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:05.50,0:00:07.00,Default,,0,0,0,,{\\an8}{\\i1}Second in time{\\i0}, first in file, with comma\n"
    "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,this is a comment\n"
    "Dialogue: 0,0:00:01.25,0:00:03.00,Default,Bob,0,0,0,,Line one\\NLine two\n"
    "Dialogue: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}\n"
).encode("utf-8")

SIMPLE_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nFirst line\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nSecond line\nwith two rows\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\nThird\n"
).encode("utf-8")
