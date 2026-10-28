"""The production settings of a paper's myst.yml (api/myst_production.py), on made-up papers of several fields."""

import textwrap

import pytest
from ruamel.yaml import YAML

import myst_production as mp

PRODUCTION = mp.Production(
    fork="journal-org/river-sediment", ref="main", doi="10.99999/journal.00077",
    binderhub="https://binder.journal.example", journal="Journal", subject="Living Preprint",
    content_license="CC-BY-4.0", code_license="Apache-2.0", twitter="journal",
    home_url="https://doi.org/10.99999/journal.00077", banner=lambda: "https://example.org/banner.jpg")

# A hydrology paper written the way current MyST writes live code: under project.jupyter.binder, with comments,
# lists indented under their key, quotes and characters outside ASCII.
HYDROLOGY = textwrap.dedent("""\
    # See docs at: https://mystmd.org/guide/frontmatter
    version: 1
    project:
      # keep identical to the title in paper.md
      title: 'Sediment transport in braided rivers'
      authors:
        - name: Ana Núñez
          roles:
            - "Writing – original draft"
      keywords:
        - bedload
      github: https://github.com/someone/river-sediment
      jupyter:
        binder:
          url: https://preview-binder.journal.example
          ref: main
          repo: someone/river-sediment
      doi: 10.99999/journal.xxxxx  # placeholder
      license:
        content: CC-BY-4.0
        code: MIT
      abbreviations:
        µm: micrometre
      toc:
        - file: paper.md
    site:
      template: article-theme
    """)


def load(text):
    return YAML(typ="safe").load(text)


def test_current_myst_live_code_is_pointed_at_production_and_the_fork():
    project = load(mp.configure(HYDROLOGY, PRODUCTION))["project"]
    assert project["jupyter"]["binder"] == {"url": "https://binder.journal.example",
                                            "repo": "journal-org/river-sediment", "ref": "main"}


def test_comments_order_quotes_and_characters_are_kept():
    out = mp.configure(HYDROLOGY, PRODUCTION)
    assert "# See docs at: https://mystmd.org/guide/frontmatter" in out
    assert "  # keep identical to the title in paper.md" in out
    assert "  title: 'Sediment transport in braided rivers'" in out
    assert '        - "Writing – original draft"' in out
    assert "    µm: micrometre" in out
    assert out.index("version:") < out.index("project:") < out.index("site:")
    assert out.index("title:") < out.index("authors:") < out.index("abbreviations:")


def test_only_the_values_production_sets_change():
    out = mp.configure(HYDROLOGY, PRODUCTION)
    changed = [line for line in out.splitlines() if line not in HYDROLOGY.splitlines()]
    assert all(any(word in line for word in ("url:", "repo:", "github:", "doi:", "code:", "copyright:",
                                              "open_access:", "venue:", "subject:", "banner:", "options:",
                                              "favicon:", "twitter:", "logo:", "logo_text:", "actions:",
                                              "title: 🏠", "- title:"))
               for line in changed), changed


def test_configuring_twice_gives_the_same_text():
    once = mp.configure(HYDROLOGY, PRODUCTION)
    assert mp.configure(once, PRODUCTION) == once
    assert load(once)["site"]["actions"] == [{"title": "🏠 Home", "url": PRODUCTION.home_url}]


@pytest.mark.parametrize("value", ["true", "'binder'", "\n    binder: true"])
def test_the_short_forms_of_binder_live_code_become_a_full_binder_setting(value):
    text = f"project:\n  title: Glacier mass balance\n  jupyter: {value}\nsite:\n  template: book-theme\n"
    project = load(mp.configure(text, PRODUCTION))["project"]
    assert project["jupyter"] == {"binder": {"url": "https://binder.journal.example",
                                             "repo": "journal-org/river-sediment", "ref": "main"}}


def test_the_older_thebe_name_is_configured_too_and_a_provider_becomes_github():
    text = textwrap.dedent("""\
        project:
          title: Bird song dialects
          thebe:
            binder:
              url: https://mybinder.org
              repo: https://gitlab.com/someone/song
              provider: gitlab
        """)
    binder = load(mp.configure(text, PRODUCTION))["project"]["thebe"]["binder"]
    assert binder == {"url": "https://binder.journal.example", "repo": "journal-org/river-sediment",
                      "provider": "github", "ref": "main"}


def test_live_code_without_binder_and_live_code_turned_off_are_left_alone():
    text = "project:\n  title: Ledger auditing\n  jupyter:\n    lite: true\n  thebe: false\n"
    project = load(mp.configure(text, PRODUCTION))["project"]
    assert project["jupyter"] == {"lite": True}
    assert project["thebe"] is False


def test_a_paper_without_live_code_gets_none():
    project = load(mp.configure("project:\n  title: Medieval tax rolls\n", PRODUCTION))["project"]
    assert "jupyter" not in project and "thebe" not in project and "binder" not in project


def test_the_launch_button_points_at_the_production_binderhub_and_the_fork():
    text = "project:\n  title: Soil respiration\n  binder: https://mybinder.org/v2/gh/someone/soil/HEAD\n"
    assert load(mp.configure(text, PRODUCTION))["project"]["binder"] == \
        "https://binder.journal.example/v2/gh/journal-org/river-sediment/main"


def test_the_content_licence_is_the_journals_and_the_code_licence_is_kept_when_github_detects_none():
    no_code_license = mp.Production(**{**PRODUCTION.__dict__, "code_license": None})
    project = load(mp.configure(HYDROLOGY, no_code_license))["project"]
    assert project["license"] == {"content": "CC-BY-4.0", "code": "MIT"}
    project = load(mp.configure("project:\n  title: Coral bleaching\n  license: CC0-1.0\n", PRODUCTION))["project"]
    assert project["license"] == {"content": "CC-BY-4.0", "code": "Apache-2.0"}


def test_the_authors_site_options_are_kept_and_the_missing_ones_filled():
    text = "project:\n  title: Exoplanet transits\nsite:\n  options:\n    logo: my-logo.svg\n"
    options = load(mp.configure(text, PRODUCTION))["site"]["options"]
    assert options == {"logo": "my-logo.svg", "favicon": "favicon.ico", "twitter": "journal",
                       "logo_text": "Journal"}


def test_existing_actions_get_one_home_button():
    text = ("project:\n  title: Urban heat islands\nsite:\n  actions:\n"
            "    - title: Data\n      url: https://example.org/data\n")
    once = mp.configure(text, PRODUCTION)
    actions = load(mp.configure(once, PRODUCTION))["site"]["actions"]
    assert actions == [{"title": "Data", "url": "https://example.org/data"},
                       {"title": "🏠", "url": PRODUCTION.home_url}]


def test_a_banner_is_added_only_to_an_article_without_one():
    assert load(mp.configure(HYDROLOGY, PRODUCTION))["project"]["banner"] == "https://example.org/banner.jpg"
    book = "project:\n  title: Grammar of Old Norse\nsite:\n  template: book-theme\n"
    assert "banner" not in load(mp.configure(book, PRODUCTION))["project"]


def test_lists_written_at_the_level_of_their_key_stay_there():
    text = "project:\n  title: Pollen records\n  keywords:\n  - holocene\n  - lakes\n"
    assert "  keywords:\n  - holocene\n  - lakes\n" in mp.configure(text, PRODUCTION)


@pytest.mark.parametrize("text, sentence", [
    ("project: [unclosed\n", "myst.yml could not be read at line"),
    ("- just\n- a list\n", "myst.yml is not a mapping of settings."),
    ("site:\n  template: article-theme\n", "myst.yml has no `project` section."),
    ("project:\n  title: Tides\nsite: article-theme\n", "The `site` section of myst.yml is not a mapping"),
])
def test_a_file_that_cannot_be_configured_is_one_sentence(text, sentence):
    with pytest.raises(mp.MystConfigError) as raised:
        mp.configure(text, PRODUCTION)
    message = str(raised.value)
    assert message.startswith(sentence) and message.endswith(".") and "\n" not in message


def test_live_code_says_what_the_file_declares():
    assert "runs code in the page on a BinderHub" in mp.live_code(HYDROLOGY)[0]
    assert mp.live_code("project:\n  title: Census of moths\n") == [
        "myst.yml declares no live code (`project.jupyter` or `project.binder`), so the published paper will "
        "have no launch button."]
    assert "without Binder" in mp.live_code("project:\n  jupyter:\n    server:\n      url: http://x\n")[0]


def test_differences_list_each_changed_setting():
    after = mp.configure(HYDROLOGY, PRODUCTION)
    lines = mp.differences(HYDROLOGY, after)
    assert "`project.jupyter.binder.repo`: `someone/river-sediment` → `journal-org/river-sediment`" in lines
    assert "`project.copyright`: (absent) → `Journal`" in lines
    assert mp.differences(after, after) == []
    assert "`project.title`: (absent) → `Sediment transport in braided rivers`" in mp.differences(None, after)
