"""The one exception type for failures an agent can act on."""

from __future__ import annotations


class SubtitleError(Exception):
    """A problem with the user's input or request, worded so the caller can fix it."""
