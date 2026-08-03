"""Translate a myst.yml `project` mapping into inara paper metadata.

A NeuroLibre submission declares its title, authors, and affiliations in
myst.yml for the living preprint. The publishing pipeline wants the same
information in the shape the paper.md front matter uses: affiliations numbered
by index, and each author's affiliations as a comma-joined string of those
indices.

This module is pure. Fetching files is the caller's job, which keeps the
mapping testable without a GitHub client. It mirrors
inara/data/filters/myst-frontmatter.lua; the two share the mapping documented
in the design spec.
"""

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

    if project.get("title") is not None:
        metadata["title"] = project["title"]
    if project.get("date") is not None:
        metadata["date"] = project["date"]
    if project.get("keywords") is not None:
        metadata["tags"] = project["keywords"]
    if project.get("bibliography") is not None:
        metadata["bibliography"] = project["bibliography"]

    affiliations = []
    index_of = {}
    for source in project.get("affiliations") or []:
        if not isinstance(source, dict):
            continue
        index = len(affiliations) + 1
        affiliations.append({"index": index, "name": _affiliation_name(source)})
        if source.get("id") is not None:
            index_of[str(source["id"])] = index

    authors = []
    for source in project.get("authors") or []:
        if not isinstance(source, dict):
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
