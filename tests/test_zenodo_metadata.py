"""What reaches a Zenodo deposit, and what must not."""

import json

import pytest

from api.zenodo_metadata import ZENODO_CREATOR_FIELDS
from api.zenodo_metadata import zenodo_creators


def test_keeps_only_the_fields_zenodo_accepts():
    creators = zenodo_creators([
        {
            "name": "Ada Lovelace",
            "orcid": "0000-0001-0000-0000",
            "affiliation": "Analytical Engine Institute",
        }
    ])
    assert creators == [{
        "name": "Ada Lovelace",
        "orcid": "0000-0001-0000-0000",
        "affiliation": "Analytical Engine Institute",
    }]


def test_drops_the_author_email():
    # myst.yml authors routinely carry an email; paper.md front matter rarely
    # does. Zenodo rejects the key, and a public record must not publish it.
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "email": "ada@example.org"}
    ])
    assert creators == [{"name": "Ada Lovelace"}]


def test_drops_the_myst_only_author_keys():
    creators = zenodo_creators([{
        "name": "Ada Lovelace",
        "email": "ada@example.org",
        "corresponding": True,
        "equal-contrib": True,
        "github": "ada",
        "twitter": "ada",
        "url": "https://example.org",
        "numbering": {"heading_1": False},
    }])
    assert creators == [{"name": "Ada Lovelace"}]


def test_repairs_a_misspelled_orcid_key():
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "orchid": "0000-0001-0000-0000"}
    ])
    assert creators == [
        {"name": "Ada Lovelace", "orcid": "0000-0001-0000-0000"}
    ]


def test_repairs_a_plural_affiliation_key():
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "affiliations": "Royal Society"}
    ])
    assert creators == [
        {"name": "Ada Lovelace", "affiliation": "Royal Society"}
    ]


def test_an_exact_field_wins_over_a_repaired_one():
    # Deterministic regardless of dict order: the exact key is authoritative.
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "affiliations": "Wrong", "affiliation": "Right"}
    ])
    assert creators == [{"name": "Ada Lovelace", "affiliation": "Right"}]

    creators = zenodo_creators([
        {"name": "Ada Lovelace", "affiliation": "Right", "affiliations": "Wrong"}
    ])
    assert creators == [{"name": "Ada Lovelace", "affiliation": "Right"}]


def test_drops_blank_values():
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "orcid": None, "affiliation": ""}
    ])
    assert creators == [{"name": "Ada Lovelace"}]


def test_a_bare_string_author_becomes_a_named_creator():
    assert zenodo_creators(["Ada Lovelace"]) == [{"name": "Ada Lovelace"}]


def test_stringifies_a_non_string_scalar():
    creators = zenodo_creators([{"name": "Ada Lovelace", "affiliation": 1}])
    assert creators == [{"name": "Ada Lovelace", "affiliation": "1"}]


def test_skips_an_author_with_no_name():
    # Zenodo requires a name on every creator. Sending one without would fail
    # the whole deposit; dropping it loses one creator instead of all of them.
    creators = zenodo_creators([
        {"orcid": "0000-0001-0000-0000"},
        {"name": "Ada Lovelace"},
    ])
    assert creators == [{"name": "Ada Lovelace"}]


def test_warns_about_an_author_with_no_name(caplog):
    with caplog.at_level("WARNING"):
        zenodo_creators([{"orcid": "0000-0001-0000-0000"}])
    assert "no name" in caplog.text


def test_tolerates_junk_input():
    assert zenodo_creators(None) == []
    assert zenodo_creators([]) == []
    assert zenodo_creators("not a list") == []


def test_does_not_mutate_the_caller_s_authors():
    authors = [{"name": "Ada Lovelace", "email": "ada@example.org"}]
    zenodo_creators(authors)
    assert authors == [{"name": "Ada Lovelace", "email": "ada@example.org"}]


def test_result_is_json_serialisable():
    creators = zenodo_creators([
        {"name": "Ada Lovelace", "orcid": "0000-0001-0000-0000"}
    ])
    assert json.loads(json.dumps(creators)) == creators


def test_allowed_fields_are_the_zenodo_legacy_creator_schema():
    assert ZENODO_CREATOR_FIELDS == ("name", "affiliation", "orcid", "gnd")
