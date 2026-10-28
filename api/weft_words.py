"""The two text helpers the steps and the comments share, so that a sentence a person reads says "description" and
never "weft" outside a command or a file name, whichever module wrote it. No em dashes."""

import re

# "weft" as a word, but not in a command (`@roboneuro weft check`, evidentron.weft) or a file name (x.weft.yaml).
_WEFT = re.compile(r"(?<!roboneuro )(?<![\w.`/-])weft(s?)(?=['\s,;:)!?]|\.(?!\w)|$)")


def plain(text: str) -> str:
    """A sentence from a step or a command, with "weft" written as "description", since the comment explains
    the description and never the word."""
    return _WEFT.sub(r"description\1", str(text))


def lower_first(text: str) -> str:
    """The text with its first letter lower-cased, unless its first word is an acronym or a name written with
    capitals inside it (NOAA, WIPO, iPhone): only when the rest of the first word has no capital letter."""
    words = text.split(maxsplit=1)
    if not words or any(c.isupper() for c in words[0][1:]):
        return text
    return text[:1].lower() + text[1:]
