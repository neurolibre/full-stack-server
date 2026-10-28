"""A paper's myst.yml configured for production: the journal's settings, and live code pointed at the fork.

The file is edited in place with ruamel.yaml's round trip, so the author's comments, key order, quoting, indentation
and characters stay as they wrote them, and only the values production sets differ. Configuring a file that is
already configured gives the same text back, so a step run twice writes nothing the second time.

Live code is declared in three places (https://mystmd.org/guide/in-page-execution):
- `project.jupyter`, or its older name `project.thebe`: `true`, `'binder'`, `{binder: true}` or
  `{binder: {url, repo, ref, provider}}` run code in the page on a BinderHub; a Jupyter server (`server`) or
  JupyterLite (`lite`) does not use Binder and is left as it is.
- `project.binder`: the address of the launch button, a URL.
Each is changed only where the author declared it, so a paper with no live code stays without.

Whether anything changed is decided on the settings, not the bytes: a file whose settings production already has is
returned as it is, whatever its line endings or spacing. When something does change, the line endings, a byte order
mark and a leading `---` are kept. ruamel writes every list of a file in one style, so a file that writes its lists
in two styles gets the style most of them use.
"""

import io
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, List, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

# The two names of the in-page execution settings: `thebe` is the older one, and mystmd reads both.
JUPYTER_KEYS = ("jupyter", "thebe")


class MystConfigError(ValueError):
    """myst.yml cannot be configured; the message is one sentence for the issue."""


@dataclass
class Production:
    """What production sets in a paper's myst.yml."""
    fork: str                     # owner/repo of the fork the published paper is built from
    ref: str                      # the fork's branch Binder builds
    doi: str
    binderhub: str                # the production BinderHub, https://host
    journal: str
    subject: str
    content_license: str
    code_license: Optional[str]   # the licence GitHub detects for the code; None keeps the author's
    twitter: str
    home_url: str                 # the paper's page, the target of the Home button
    banner: Callable[[], str]     # a banner for an article without one

    @property
    def launch_url(self) -> str:
        return f"{self.binderhub}/v2/gh/{self.fork}/{self.ref}"


def configure(text: str, production: Production) -> str:
    """myst.yml's text with production's settings applied; the same text when there is nothing to change."""
    bom = text.startswith("\ufeff")
    body = text[1:] if bom else text
    crlf = "\r\n" in body
    body = body.replace("\r\n", "\n")
    yaml = _yaml(body)
    yaml.explicit_start = body.startswith("---")
    config = _load(yaml, body)
    project = config["project"]

    project["copyright"] = production.journal
    project["github"] = f"https://github.com/{production.fork}"
    project["open_access"] = True
    project["venue"] = production.journal
    project["subject"] = production.subject
    project["doi"] = production.doi
    _license(project, production)
    for key in JUPYTER_KEYS:
        if key in project:
            project[key] = _jupyter(project[key], production)
    if project.get("binder"):
        project["binder"] = production.launch_url

    site = config.get("site")
    if site is None:
        site = config["site"] = CommentedMap()
    if not isinstance(site, dict):
        raise MystConfigError("The `site` section of myst.yml is not a mapping of settings.")
    _options(site, production)
    _home_action(site, production)
    if site.get("template") == "article-theme" and "banner" not in project:
        project["banner"] = production.banner()

    out = io.StringIO()
    yaml.dump(config, out)
    new = out.getvalue()
    if YAML(typ="safe").load(new) == YAML(typ="safe").load(body):
        return text
    if not body.endswith("\n"):
        new = new.rstrip("\n")
    if crlf:
        new = new.replace("\n", "\r\n")
    return ("\ufeff" if bom else "") + new


def live_code(text: str) -> List[str]:
    """Sentences on what myst.yml declares for live code, for the person checking a repository before submission.
    A file that cannot be read raises MystConfigError."""
    project = _load(_yaml(text), text)["project"]
    said = []
    for key in JUPYTER_KEYS:
        if key in project:
            value = project[key]
            if _uses_binder(value):
                said.append(f"`project.{key}` runs code in the page on a BinderHub; production points it at the "
                            f"production BinderHub and the journal's copy of the repository.")
            elif value:
                said.append(f"`project.{key}` runs code in the page without Binder; production leaves it as it is.")
            else:
                said.append(f"`project.{key}` turns live code off.")
    if project.get("binder"):
        said.append("`project.binder` adds a launch button; production points it at the production BinderHub.")
    if not said:
        said.append("myst.yml declares no live code (`project.jupyter` or `project.binder`), so the published "
                    "paper will have no launch button.")
    return said


def differences(before: Optional[str], after: str) -> List[str]:
    """The settings that differ between two myst.yml texts, one line each, as `key`: before → after. A text that
    is missing or cannot be read counts as having no settings."""
    old, new = _flat(before), _flat(after)
    return [f"`{key}`: {_show(old.get(key, ABSENT))} → {_show(new.get(key, ABSENT))}"
            for key in sorted(set(old) | set(new)) if old.get(key, ABSENT) != new.get(key, ABSENT)]


ABSENT = object()       # a setting the file does not have, told apart from one written with no value


def setting(text: Optional[str], key: str):
    """The value of one setting, named by its dotted path (`project.banner`), or None when the file is missing,
    cannot be read or does not have it."""
    value = _flat(text).get(key, ABSENT)
    return None if value is ABSENT else value


def _license(project, production):
    license_ = project.get("license")
    if not isinstance(license_, dict):
        license_ = project["license"] = CommentedMap()
    license_["content"] = production.content_license
    if production.code_license:
        license_["code"] = production.code_license


def _uses_binder(value) -> bool:
    if value is True or value == "binder":
        return True
    return isinstance(value, dict) and bool(value.get("binder"))


def _jupyter(value, production):
    """The in-page execution settings with Binder pointed at production; settings without Binder are kept."""
    if not _uses_binder(value):
        return value
    if not isinstance(value, dict):
        value = CommentedMap()
    binder = value.get("binder")
    if not isinstance(binder, dict):
        binder = value["binder"] = CommentedMap()
    binder["url"] = production.binderhub
    binder["repo"] = production.fork
    binder["ref"] = production.ref
    if "provider" in binder:
        binder["provider"] = "github"
    return value


def _options(site, production):
    options = site.get("options")
    if options is None:
        options = site["options"] = CommentedMap()
    if not isinstance(options, dict):
        raise MystConfigError("The `site.options` section of myst.yml is not a mapping of settings.")
    for key, value in (("favicon", "favicon.ico"), ("twitter", production.twitter), ("logo", "logo.png"),
                       ("logo_text", production.journal)):
        options.setdefault(key, value)


def _home_action(site, production):
    actions = site.get("actions")
    if actions is None:
        actions = site["actions"] = []
    if not isinstance(actions, list):
        raise MystConfigError("The `site.actions` section of myst.yml is not a list.")
    if any(isinstance(a, dict) and a.get("url") == production.home_url for a in actions):
        return
    actions.append(CommentedMap(title="🏠" if actions else "🏠 Home", url=production.home_url))


def _yaml(text: str) -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096                  # never rewrap a long line
    mapping, sequence, offset = _indents(text)
    yaml.indent(mapping=mapping, sequence=sequence, offset=offset)
    return yaml


def _indents(text: str):
    """The mapping indent, and the indent and dash offset of lists, that most of the file uses (ruamel needs them
    given; it does not read them from the file). Lines inside a block of text (`key: |` or `key: >`) are not
    settings and are skipped."""
    lines, block = [], None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block is not None and indent > block:
            continue
        block = indent if BLOCK_TEXT.search(line) else None
        lines.append((indent, line.strip()))
    mappings, lists = Counter(), Counter()
    for (a_indent, a), (b_indent, b) in zip(lines, lines[1:]):
        if not a.endswith(":"):
            continue
        if b.startswith("- "):
            lists[b_indent - a_indent] += 1
        elif b_indent > a_indent:
            mappings[b_indent - a_indent] += 1
    mapping = mappings.most_common(1)[0][0] if mappings else 2
    offset = lists.most_common(1)[0][0] if lists else mapping
    return mapping, offset + 2, offset


# A key or list item whose value is a block of text: `key: |`, `key: >-`, `- |2`.
BLOCK_TEXT = re.compile(r"(:|^-)\s+[|>][-+0-9]*\s*(#.*)?$")


def _load(yaml: YAML, text: str):
    try:
        config = yaml.load(text)
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(e, "problem", None) or "it is not valid YAML"
        raise MystConfigError(f"myst.yml could not be read{where}: {problem}.") from None
    if not isinstance(config, dict):
        raise MystConfigError("myst.yml is not a mapping of settings.")
    if not isinstance(config.get("project"), dict):
        raise MystConfigError("myst.yml has no `project` section.")
    return config


def _flat(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        config = YAML(typ="safe").load(text)
    except YAMLError:
        return {}

    def walk(value, prefix):
        if isinstance(value, dict) and value:
            for k, v in value.items():
                yield from walk(v, f"{prefix}.{k}" if prefix else str(k))
        else:
            yield prefix, value

    return dict(walk(config, "")) if isinstance(config, dict) else {}


def _show(value) -> str:
    if value is ABSENT:
        return "(absent)"
    return "empty" if value is None else f"`{value}`"
