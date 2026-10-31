"""The pages mystmd reported problems on (its ⛔️ lines), so a build that wrote its site can be reported as completed
with problems on those pages, not as a failure. The lines are from qmrlab/mooc's build of 2026-10-31."""

import build_problems

LOG = """📖 Built 2 T1 mapping.md in 1.55 s.
💿 Executing notebook (4 B1 Mapping/03-Filtering/Notebooks/Figure-4-3-1.ipynb) [no execution cache found]
\x1b[31m⛔️ 4 B1 Mapping/03-Filtering/Notebooks/Figure-4-3-1.ipynb An exception occurred during code execution, halting further execution:\x1b[39m
\x1b[0;31mValueError\x1b[0m: Invalid property specified for object of type plotly.graph_objs.heatmap.ColorBar: 'titlefont'
⛔️ 4 B1 Mapping/03-Filtering/Notebooks/Figure-4-3-1.ipynb An exception occurred during code execution, halting further execution:
⛔️ 5 B0 mapping.md Cannot find image "./5 B0 Mapping/1 B0 inhomogeneities/videos/KSpace.mp4" in /DATA/myst/qmrlab/mooc/latest
⛔️ 4 B1 Mapping/04-Bloch-Siegert/Notebooks/Figure-4-4-1.ipynb An exception occurred during code execution, halting further execution:
📚 Built 113 pages for project in 2.13 min.
"""


def test_each_page_is_listed_once_in_order():
    found = build_problems.problems(LOG)
    assert [p.page for p in found] == [
        "4 B1 Mapping/03-Filtering/Notebooks/Figure-4-3-1.ipynb",
        "5 B0 mapping.md",
        "4 B1 Mapping/04-Bloch-Siegert/Notebooks/Figure-4-4-1.ipynb",
    ]


def test_the_problem_is_said_without_colour_codes_or_server_paths():
    found = {p.page: p.problem for p in build_problems.problems(LOG)}
    assert found["4 B1 Mapping/03-Filtering/Notebooks/Figure-4-3-1.ipynb"] == \
        "ValueError: Invalid property specified for object of type plotly.graph_objs.heatmap.ColorBar: 'titlefont'"
    assert found["4 B1 Mapping/04-Bloch-Siegert/Notebooks/Figure-4-4-1.ipynb"] == \
        "An exception occurred during code execution, halting further execution"     # no traceback followed
    assert found["5 B0 mapping.md"] == 'Cannot find image "./5 B0 Mapping/1 B0 inhomogeneities/videos/KSpace.mp4"'


def test_a_clean_build_has_no_problems():
    assert build_problems.problems("📖 Built 2 T1 mapping.md in 1.55 s.\n📚 Built 113 pages\n") == []


def test_the_markdown_summary_lists_pages_and_stops_at_a_limit():
    text = build_problems.markdown(build_problems.problems(LOG), limit=2)
    assert "3 pages had problems" in text
    assert "`5 B0 mapping.md`" in text
    assert "and 1 more" in text


def test_the_html_summary_escapes_what_the_log_says():
    found = [build_problems.Problem("a <b>.md", 'Cannot find image "<x>"')]
    html = build_problems.html(found)
    assert "<b>.md" not in html and "&lt;b&gt;.md" in html


def test_no_problems_means_no_summary():
    assert build_problems.markdown([]) == "" and build_problems.html([]) == ""
