"""The pages mystmd reported problems on during a build.

mystmd marks each problem with a line "⛔️ <page> <what went wrong>": a notebook cell that raised, an image it cannot
find, and so on. A build with such problems can still write its whole site (myst-libre >= 0.4.6 then saves it), so the
build is reported as completed with problems on these pages, and the author is told which pages to look at.
"""

import html as html_lib
import re
from collections import namedtuple

Problem = namedtuple("Problem", "page problem")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_LINE = re.compile(r"⛔️\s+(?P<page>.+?\.(?:ipynb|md))\s+(?P<problem>.*)$")
# Where the build ran on the server says nothing to the author
_SERVER_PATH = re.compile(r"\s+in\s+/\S*$")


# The last line of a Python traceback: "ValueError: Invalid property ...". mystmd prints the notebook's traceback
# after its "An exception occurred" line, and this line says what actually went wrong.
_ERROR = re.compile(r"^(?:[A-Za-z_][\w.]*)?(?:Error|Exception|Interrupt|Exit)\b(?::\s.*)?$")
# The markers mystmd starts its own lines with; a traceback ends at the next one
_MARKERS = ("⛔️", "💿", "📖", "💾", "📚", "🌎", "✨", "🚀", "⚠️")


def _error_after(lines, start):
    """The last traceback line naming an error, between lines[start] and mystmd's next marker line."""
    error = None
    for line in lines[start:]:
        if line.startswith(_MARKERS):
            break
        if _ERROR.match(line):
            error = line
    return error


def problems(logs):
    """Each page mystmd reported a problem on, once, in the order they first appear, with its first problem. For a
    notebook that raised, the problem is the error its traceback ends with, when the traceback is in the log."""
    lines = [_ANSI.sub("", line).strip() for line in (logs or "").splitlines()]
    found, seen = [], set()
    for number, line in enumerate(lines):
        match = _LINE.search(line)
        if not match or match.group("page") in seen:
            continue
        seen.add(match.group("page"))
        problem = _SERVER_PATH.sub("", match.group("problem")).rstrip(" :")
        if problem.startswith("An exception occurred"):
            problem = _error_after(lines, number + 1) or problem
        found.append(Problem(match.group("page"), problem[:300]))
    return found


def _heading(found):
    return f"The site was built, but {len(found)} page{'s' if len(found) != 1 else ''} had problems"


def markdown(found, limit=20):
    """A GitHub warning listing the pages, or "" when there are none."""
    if not found:
        return ""
    lines = [f"> [!WARNING]", f"> {_heading(found)}. The build logs have the details:"]
    lines += [f"> - `{p.page}`: {p.problem}" for p in found[:limit]]
    if len(found) > limit:
        lines.append(f"> - and {len(found) - limit} more")
    return "\n".join(lines)


def html(found, limit=20):
    """An HTML list of the pages for the email, or "" when there are none."""
    if not found:
        return ""
    items = "".join(f"<li><code>{html_lib.escape(p.page)}</code>: {html_lib.escape(p.problem)}</li>"
                    for p in found[:limit])
    if len(found) > limit:
        items += f"<li>and {len(found) - limit} more</li>"
    return (f'<div class="info-box"><p><strong>⚠️ {html_lib.escape(_heading(found))}:</strong></p>'
            f"<ul>{items}</ul></div>")
