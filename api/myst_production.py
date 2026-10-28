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
"""

import io
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
    yaml = _yaml(text)
    config = _load(yaml, text)
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
    return text if _same(new, text) else new


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
    return [f"`{key}`: {_show(old.get(key))} → {_show(new.get(key))}"
            for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


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
    if not isinstance(actions, list):
        actions = site["actions"] = []
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
    """The mapping indent, and the indent and dash offset of lists, as the file writes them (ruamel needs them
    given; it does not read them from the file). The first nested key and the first list give them."""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    mapping = next((len(b) - len(b.lstrip()) - (len(a) - len(a.lstrip()))
                    for a, b in zip(lines, lines[1:])
                    if a.rstrip().endswith(":") and not b.lstrip().startswith("- ")
                    and len(b) - len(b.lstrip()) > len(a) - len(a.lstrip())), 2)
    for a, b in zip(lines, lines[1:]):
        if a.rstrip().endswith(":") and b.lstrip().startswith("- "):
            offset = (len(b) - len(b.lstrip())) - (len(a) - len(a.lstrip()))
            return mapping, offset + 2, offset
    return mapping, mapping + 2, mapping


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


def _same(a: str, b: str) -> bool:
    """Equal up to the final newline, which the round trip may add or drop."""
    return a.rstrip("\n") == b.rstrip("\n")


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
    return "(absent)" if value is None else f"`{value}`"
