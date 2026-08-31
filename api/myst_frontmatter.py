"""Translate a myst.yml `project` mapping into inara paper metadata.

A NeuroLibre submission declares its title, authors, and affiliations in
myst.yml for the living preprint. The publishing pipeline wants the same
information in the shape the paper.md front matter uses: affiliations numbered
by index, and each author's affiliations as a comma-joined string of those
indices.

This module is pure. Fetching files is the caller's job, which keeps the
mapping testable without a GitHub client. It mirrors
inara/data/filters/myst-frontmatter.lua; the two share the mapping documented
in the design spec. It also merges a parsed paper.md front matter with a
myst.yml, filling any gaps the front matter leaves.
"""

import logging

import yaml

# Parts of a myst.yml affiliation, joined into one name string. Department
# precedes institution to match the convention in existing NeuroLibre front
# matter.
NAME_PARTS = (
    "department",
    "institution",
    "address",
    "city",
    "region",
    "postal_code",
    "country",
)

# MyST accepts these aliases for two of the parts.
ALIASES = {"institution": "name", "region": "state"}


def _is_blank(value):
    """Is a value absent, or present but carrying nothing?

    A `paper.md` front matter of `title:` parses to `title: None`, not to a
    missing key. `""`, `[]` and `{}` say the same thing. All of them must count
    as absent or a key that was merely typed out defeats the myst.yml fallback.
    Mirrors `is_blank` in inara's myst-frontmatter.lua.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _as_list(value):
    """Normalise a myst.yml sequence to a list.

    `affiliations: harvard` is legal MyST. Without this, iterating the string
    would walk its characters. Mirrors `as_list` in myst-frontmatter.lua.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _affiliation_name(affiliation):
    """Join an affiliation's parts into a single display string."""
    parts = []
    for key in NAME_PARTS:
        value = affiliation.get(key)
        if value in (None, "") and key in ALIASES:
            value = affiliation.get(ALIASES[key])
        if value not in (None, ""):
            parts.append(str(value).strip())
    return ", ".join(parts)


def _affiliation_tokens(value):
    """Normalise an author's `affiliations` value to a list of tokens.

    MyST accepts a list, a single id, or several ids in one ';'-separated
    string.
    """
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(entry).strip() for entry in value if str(entry).strip()]
    return [token.strip() for token in str(value).split(";") if token.strip()]


def myst_project_metadata(project):
    """Return inara paper metadata derived from a myst.yml `project` mapping.

    Returns only the keys the project actually defines, so the caller can treat
    the result as a set of defaults to fill gaps with. Junk input yields an
    empty dict: this fallback must never be why a deposit fails.
    """
    if not isinstance(project, dict):
        return {}

    metadata = {}

    if not _is_blank(project.get("title")):
        metadata["title"] = project["title"]
    if not _is_blank(project.get("date")):
        # `date: 2024-01-15` -- unquoted ISO, the MyST-canonical form -- is
        # parsed by yaml.safe_load into a datetime.date. This value is carried
        # into a Celery task payload, which is serialized as JSON, so a date
        # object here is an HTTP 500 at enqueue time. Nothing downstream
        # consumes `date` structurally, so the string form is the right shape.
        date = project["date"]
        metadata["date"] = date if isinstance(date, str) else str(date)
    if not _is_blank(project.get("keywords")):
        metadata["tags"] = project["keywords"]
    if not _is_blank(project.get("bibliography")):
        metadata["bibliography"] = project["bibliography"]

    affiliations = []
    index_of = {}
    for source in _as_list(project.get("affiliations")):
        index = len(affiliations) + 1
        if not isinstance(source, dict):
            # MyST's validator accepts a bare string where an affiliation
            # mapping is expected. It becomes an affiliation named after that
            # string, with no id, and it still consumes its index position --
            # the Lua filter applies the same rule, so both sides agree on
            # every author's index.
            affiliations.append({"index": index, "name": str(source).strip()})
            continue
        affiliations.append({"index": index, "name": _affiliation_name(source)})
        if source.get("id") is not None:
            index_of[str(source["id"])] = index

    authors = []
    for source in _as_list(project.get("authors")):
        if not isinstance(source, dict):
            # Same MyST rule for authors: `authors: [Ada Lovelace]` is valid.
            # A bare string becomes a named author with no affiliations, still
            # holding its position in the list.
            authors.append({"name": str(source).strip()})
            continue
        author = {"name": source.get("name")}
        for target, key in (
            ("email", "email"),
            ("orcid", "orcid"),
            ("corresponding", "corresponding"),
            ("equal-contrib", "equal_contributor"),
        ):
            if source.get(key) is not None:
                author[target] = source[key]

        indices = []
        tokens = _affiliation_tokens(
            source.get("affiliations") or source.get("affiliation")
        )
        for token in tokens:
            index = index_of.get(token)
            if index is None:
                # MyST permits ad-hoc affiliations. Inventing an entry beats
                # dropping the author's affiliation.
                index = len(affiliations) + 1
                affiliations.append({"index": index, "name": token})
                index_of[token] = index
            indices.append(str(index))
        if indices:
            author["affiliation"] = ",".join(indices)

        authors.append(author)

    if authors:
        metadata["authors"] = authors
    if affiliations:
        metadata["affiliations"] = affiliations

    return metadata


def merge_paper_metadata(front_matter, myst_text):
    """Paper metadata from paper.md, with myst.yml filling any gaps.

    `front_matter` is the already-parsed paper.md front matter, or None for a
    paper that has none. `myst_text` is the raw contents of myst.yml, or None.
    Parsing myst.yml happens here rather than in the caller so that a malformed
    file is tolerated in one place.

    Returns None when neither source names any author — the same signal the
    deposit path already treats as "cannot extract metadata".
    """
    metadata = dict(front_matter) if isinstance(front_matter, dict) else {}

    if myst_text:
        try:
            project = (yaml.safe_load(myst_text) or {}).get("project")
        except yaml.YAMLError as error:
            logging.warning(f"Could not parse myst.yml: {error}")
            project = None
        except AttributeError:
            # yaml.safe_load returned something that is not a mapping.
            project = None
        fallback = myst_project_metadata(project)

        # Authors and affiliations are filled as a pair. An affiliation index
        # only means something relative to the list that defines it, so mixing
        # the two sources would silently attach authors to the wrong
        # institutions.
        #
        # A key that is present but empty counts as absent -- see `_is_blank`.
        if _is_blank(metadata.get("authors")) or _is_blank(metadata.get("affiliations")):
            if fallback.get("authors"):
                if not _is_blank(metadata.get("authors")):
                    # The front matter named authors but no affiliations, so its
                    # author list is discarded rather than merged. Announce it:
                    # a stale myst.yml silently outranking a current paper.md is
                    # otherwise indistinguishable from a correct fallback.
                    logging.warning(
                        "paper.md names authors but no affiliations; replacing "
                        "its author list with the one from myst.yml, because an "
                        "affiliation index only means something relative to the "
                        "list that defines it."
                    )
                metadata["authors"] = fallback["authors"]
                metadata["affiliations"] = fallback.get("affiliations", [])
        for key in ("title", "date", "tags", "bibliography"):
            if _is_blank(metadata.get(key)) and key in fallback:
                metadata[key] = fallback[key]

    if not metadata.get("authors"):
        return None
    return metadata


def first_affiliations(authors, affiliations):
    """Resolve each author's first affiliation to a display name.

    `authors` is a list of author dicts as produced by `merge_paper_metadata`
    (or a hand-written paper.md front matter); each may carry an `affiliation`
    value that is an int, a comma-separated string of indices, an empty
    string, or absent entirely. `affiliations` is the corresponding list of
    `{"index": ..., "name": ...}` mappings.

    Returns a list the same length as `authors`. An element is `None` when the
    author has no affiliation, or names an index the affiliation list does not
    define -- both are legitimate, not errors: myst.yml permits an author with
    no affiliation (see `test_author_without_affiliations_gets_no_affiliation_key`),
    and a caller should not have the deposit fail just because one author
    lacks one.

    An empty `affiliations` list is legitimate too -- a myst.yml project may name
    authors and no institutions at all -- and resolves every author to `None`.
    """
    # Built with `.get`, not subscripting: a hand-written paper.md may omit
    # `index` or `name` on one entry, and that entry alone should be unusable
    # rather than raising and failing the deposit.
    mapping = {}
    for affiliation in affiliations or []:
        if not isinstance(affiliation, dict):
            continue
        index = affiliation.get("index")
        name = affiliation.get("name")
        if index is None or name is None:
            logging.warning(
                f"Ignoring an affiliation entry missing 'index' or 'name': "
                f"{affiliation!r}."
            )
            continue
        mapping[str(index).strip()] = name

    resolved = []
    for author in authors:
        if not isinstance(author, dict):
            # `authors: [Ada Lovelace]` is legal in both sources; a bare string
            # names no affiliation.
            resolved.append(None)
            continue
        affiliation = author.get("affiliation")
        if not affiliation:
            resolved.append(None)
            continue
        if isinstance(affiliation, int):
            affiliation_index = affiliation
        else:
            # `affiliation: "1, 2"` is as common as `"1,2"` in front matter.
            affiliation_index = str(affiliation).split(",")[0].strip()
        name = mapping.get(str(affiliation_index).strip())
        if name is None:
            # A typo'd index used to crash loudly; now it silently records a
            # creator with no institution. Say so, so it is diagnosable.
            logging.warning(
                f"Affiliation index {affiliation_index!r} for author "
                f"{author.get('name')!r} is not defined by the affiliation "
                f"list; recording no affiliation for this author."
            )
        resolved.append(name)

    return resolved
