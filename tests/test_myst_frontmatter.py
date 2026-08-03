import pytest

from api.myst_frontmatter import myst_project_metadata
from api.myst_frontmatter import merge_paper_metadata
from api.myst_frontmatter import first_affiliations

FRONT_MATTER_PAPER = """---
title: Front Matter Title
authors:
  - name: Ada Lovelace
    affiliation: "1"
affiliations:
  - name: Analytical Engine Institute
    index: 1
---

Body.
"""

AUTHORS_ONLY_PAPER = """---
authors:
  - name: Ada Lovelace
    affiliation: "1"
---

Body.
"""

MYST_YML = """project:
  title: Myst Title
  date: "02 February 2022"
  keywords:
    - myst keyword
  authors:
    - name: Grace Hopper
      affiliations: society
  affiliations:
    - id: society
      institution: Royal Society
"""

MALFORMED_MYST_YML = 'project:\n  title: "unterminated\n   authors: [ {\n'


def test_front_matter_wins_over_myst_yml():
    data = merge_paper_metadata(
        {"title": "Front Matter Title",
         "authors": [{"name": "Ada Lovelace", "affiliation": "1"}],
         "affiliations": [{"index": 1, "name": "Analytical Engine Institute"}]},
        MYST_YML,
    )
    assert data["title"] == "Front Matter Title"
    assert [a["name"] for a in data["authors"]] == ["Ada Lovelace"]
    assert data["affiliations"] == [{"index": 1, "name": "Analytical Engine Institute"}]


def test_myst_yml_fills_a_paper_with_no_front_matter():
    data = merge_paper_metadata(None, MYST_YML)
    assert data["title"] == "Myst Title"
    assert data["authors"][0]["name"] == "Grace Hopper"
    assert data["authors"][0]["affiliation"] == "1"
    assert data["affiliations"] == [{"index": 1, "name": "Royal Society"}]


def test_scalar_fields_fill_individually():
    data = merge_paper_metadata({"title": "Kept"}, MYST_YML)
    assert data["title"] == "Kept"
    assert data["date"] == "02 February 2022"
    assert data["tags"] == ["myst keyword"]


def test_authors_and_affiliations_are_filled_as_a_pair():
    # The front matter has authors but no affiliations, so both must come from
    # myst.yml rather than pairing index 1 with the wrong institution.
    data = merge_paper_metadata(
        {"authors": [{"name": "Ada Lovelace", "affiliation": "1"}]}, MYST_YML
    )
    assert [a["name"] for a in data["authors"]] == ["Grace Hopper"]
    assert data["affiliations"] == [{"index": 1, "name": "Royal Society"}]


def test_returns_none_when_neither_source_has_authors():
    assert merge_paper_metadata(None, None) is None
    assert merge_paper_metadata({"title": "Only a title"}, None) is None


def test_tolerates_a_malformed_myst_yml():
    data = merge_paper_metadata(
        {"title": "Front Matter Title",
         "authors": [{"name": "Ada Lovelace", "affiliation": "1"}],
         "affiliations": [{"index": 1, "name": "Analytical Engine Institute"}]},
        MALFORMED_MYST_YML,
    )
    assert data["title"] == "Front Matter Title"
    assert [a["name"] for a in data["authors"]] == ["Ada Lovelace"]


def test_tolerates_a_myst_yml_with_no_project_key():
    data = merge_paper_metadata(
        {"authors": [{"name": "Ada Lovelace"}]}, "site:\n  title: Not a project\n"
    )
    assert [a["name"] for a in data["authors"]] == ["Ada Lovelace"]


def test_does_not_mutate_the_caller_s_front_matter():
    front_matter = {"authors": [{"name": "Ada Lovelace", "affiliation": "1"}]}
    merge_paper_metadata(front_matter, MYST_YML)
    assert front_matter == {"authors": [{"name": "Ada Lovelace", "affiliation": "1"}]}


def test_composes_affiliation_name_from_parts_in_order():
    project = {
        "affiliations": [
            {
                "id": "full",
                "department": "Département de génie physique",
                "institution": "École Polytechnique de Montréal",
                "address": "2500 Chemin de Polytechnique",
                "city": "Montreal",
                "region": "Quebec",
                "postal_code": "H3T 1J4",
                "country": "Canada",
            }
        ]
    }
    assert myst_project_metadata(project)["affiliations"] == [
        {
            "index": 1,
            "name": (
                "Département de génie physique, "
                "École Polytechnique de Montréal, "
                "2500 Chemin de Polytechnique, "
                "Montreal, Quebec, H3T 1J4, Canada"
            ),
        }
    ]


def test_honours_name_and_state_aliases():
    project = {
        "affiliations": [{"id": "a", "name": "Harvard University", "state": "Massachusetts"}]
    }
    assert myst_project_metadata(project)["affiliations"] == [
        {"index": 1, "name": "Harvard University, Massachusetts"}
    ]


def test_resolves_affiliation_ids_to_indices():
    project = {
        "authors": [
            {"name": "Ada Lovelace", "affiliations": ["engine", "society"]},
            {"name": "Grace Hopper", "affiliations": "society; engine"},
        ],
        "affiliations": [
            {"id": "engine", "institution": "Analytical Engine Institute"},
            {"id": "society", "institution": "Royal Society"},
        ],
    }
    result = myst_project_metadata(project)
    assert [a["affiliation"] for a in result["authors"]] == ["1,2", "2,1"]


def test_appends_undeclared_affiliation_id_as_literal_name():
    project = {
        "authors": [{"name": "Grace Hopper", "affiliations": "Yale University"}],
        "affiliations": [{"id": "engine", "institution": "Analytical Engine Institute"}],
    }
    result = myst_project_metadata(project)
    assert result["authors"][0]["affiliation"] == "2"
    assert result["affiliations"][1] == {"index": 2, "name": "Yale University"}


def test_maps_author_fields():
    project = {
        "authors": [
            {
                "name": "Ada Lovelace",
                "email": "ada@example.org",
                "orcid": "0000-0002-1825-0097",
                "corresponding": True,
                "equal_contributor": True,
            }
        ]
    }
    author = myst_project_metadata(project)["authors"][0]
    assert author["email"] == "ada@example.org"
    assert author["orcid"] == "0000-0002-1825-0097"
    assert author["corresponding"] is True
    assert author["equal-contrib"] is True


def test_maps_scalar_fields():
    project = {
        "title": "T",
        "date": "03 March 2023",
        "keywords": ["photon counting"],
        "bibliography": ["content/paper.bib"],
    }
    result = myst_project_metadata(project)
    assert result["title"] == "T"
    assert result["date"] == "03 March 2023"
    assert result["tags"] == ["photon counting"]
    assert result["bibliography"] == ["content/paper.bib"]


def test_does_not_map_doi_license_or_venue():
    project = {"doi": "10.55458/neurolibre.xxxxx", "license": {"content": "CC-BY-4.0"}, "venue": "Neurolibre"}
    assert myst_project_metadata(project) == {}


def test_author_without_affiliations_gets_no_affiliation_key():
    project = {"authors": [{"name": "Ada Lovelace"}]}
    assert "affiliation" not in myst_project_metadata(project)["authors"][0]


@pytest.mark.parametrize("project", [None, {}, "not a mapping", []])
def test_tolerates_junk_input(project):
    assert myst_project_metadata(project) == {}


AFFILIATIONS = [
    {"index": 1, "name": "Analytical Engine Institute"},
    {"index": 2, "name": "Royal Society"},
]


def test_first_affiliations_resolves_a_single_index():
    authors = [{"name": "Ada Lovelace", "affiliation": "1"}]
    assert first_affiliations(authors, AFFILIATIONS) == ["Analytical Engine Institute"]


def test_first_affiliations_takes_the_first_of_a_comma_string():
    authors = [{"name": "Grace Hopper", "affiliation": "2,1"}]
    assert first_affiliations(authors, AFFILIATIONS) == ["Royal Society"]


def test_first_affiliations_accepts_an_int():
    authors = [{"name": "Ada Lovelace", "affiliation": 2}]
    assert first_affiliations(authors, AFFILIATIONS) == ["Royal Society"]


def test_first_affiliations_is_none_when_the_key_is_absent():
    authors = [{"name": "The Analytical Collaboration"}]
    assert first_affiliations(authors, AFFILIATIONS) == [None]


def test_first_affiliations_is_none_for_an_empty_string():
    authors = [{"name": "The Analytical Collaboration", "affiliation": ""}]
    assert first_affiliations(authors, AFFILIATIONS) == [None]


def test_first_affiliations_is_none_for_an_undeclared_index():
    authors = [{"name": "Ada Lovelace", "affiliation": "9"}]
    assert first_affiliations(authors, AFFILIATIONS) == [None]
