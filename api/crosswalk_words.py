"""The text helper the steps and the comments share, so a sentence from a command reads the same whichever module
places it inside another. No em dashes."""


def lower_first(text: str) -> str:
    """The text with its first letter lower-cased, unless its first word is an acronym or a name written with
    capitals inside it (NOAA, WIPO, iPhone): only when the rest of the first word has no capital letter."""
    words = text.split(maxsplit=1)
    if not words or any(c.isupper() for c in words[0][1:]):
        return text
    return text[:1].lower() + text[1:]
