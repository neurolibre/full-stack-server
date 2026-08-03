import pytest

from api.myst_frontmatter import myst_project_metadata


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
