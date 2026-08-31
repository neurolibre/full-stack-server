"""Reduce paper metadata to the shape a Zenodo deposit accepts.

Paper metadata reaches the deposit path from two sources -- the paper.md front
matter and, filling its gaps, the myst.yml project (see `myst_frontmatter`).
Both describe authors more richly than Zenodo's legacy deposit schema allows a
creator to be: myst.yml authors routinely carry `email`, `github`, `twitter`,
`url` and `corresponding`, none of which Zenodo's `creators` accepts. Sending
them risks a validation error on the deposit, and an email address in
particular would be published on a public record.

So the deposit boundary decides what a creator is, rather than trusting
whatever the submission happened to declare. This module is pure; the caller
fetches and merges.
"""

import logging

# The legacy Zenodo deposit schema for one entry of `metadata.creators`.
# Ordered, because a misspelled key is repaired by scanning this sequence and
# the first match wins -- iterating a set here made the repair depend on hash
# order, so the same author could map differently between runs.
ZENODO_CREATOR_FIELDS = ("name", "affiliation", "orcid", "gnd")

# Misspellings seen in submissions that substring matching cannot repair.
CREATOR_FIELD_TYPOS = {"orchid": "orcid"}


def _is_blank(value):
    """Is a value absent, or present but carrying nothing?

    Mirrors `myst_frontmatter._is_blank`: a key that was typed out but left
    empty must not reach Zenodo as an empty creator field.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _canonical_creator_field(key):
    """Which Zenodo creator field does an author key mean, if any?

    Returns None for a key with no Zenodo counterpart -- `email` and friends --
    which is how they get dropped.
    """
    lowered = str(key).strip().lower()
    if lowered in ZENODO_CREATOR_FIELDS:
        return lowered
    if lowered in CREATOR_FIELD_TYPOS:
        return CREATOR_FIELD_TYPOS[lowered]
    for field in ZENODO_CREATOR_FIELDS:
        # Catches `affiliations`, `author name`, and similar near misses.
        if field in lowered:
            return field
    return None


def zenodo_creators(authors):
    """Return `authors` as Zenodo creators, carrying only accepted fields.

    `authors` is a list of author mappings as produced by `merge_paper_metadata`
    (or written by hand in a paper.md front matter), with affiliations already
    resolved to display names by `first_affiliations`. A bare string is accepted
    where a mapping is expected, matching what MyST permits.

    An author with no name is dropped, with a warning: Zenodo requires a name
    on every creator, so including one would fail the entire deposit rather
    than lose the one entry. Junk input yields an empty list -- this function
    must never itself be the reason a deposit fails.

    The caller's authors are left untouched.
    """
    if not isinstance(authors, (list, tuple)):
        return []

    creators = []
    for author in authors:
        if not isinstance(author, dict):
            if _is_blank(author):
                continue
            creators.append({"name": str(author).strip()})
            continue

        creator = {}
        repaired = {}
        for key, value in author.items():
            if _is_blank(value):
                continue
            field = _canonical_creator_field(key)
            if field is None:
                continue
            target = creator if str(key).strip().lower() == field else repaired
            target.setdefault(field, value)

        # An exactly-named key is authoritative; a repaired one only fills a
        # field the author did not spell correctly anywhere.
        for field, value in repaired.items():
            creator.setdefault(field, value)

        if not creator.get("name"):
            logging.warning(
                f"Skipping an author with no name in the Zenodo creator list: "
                f"{author!r}."
            )
            continue

        creators.append({
            field: value if isinstance(value, str) else str(value)
            for field, value in creator.items()
        })

    return creators
