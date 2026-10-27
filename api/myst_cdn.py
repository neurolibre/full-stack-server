"""Combine the published MyST site builds into one multi-project CDN.

`myst build --html` also writes _build/site: config.json, content/<page>.json
and public/. This module merges those site builds, one per published paper,
into the layout `myst build` makes for a site with several projects, which
is also how Curvenote serves:

    config.json                          every paper, with slug and doi
    content/neurolibre-00021/<page>.json pages, under the project slug
    content/10.55458/neurolibre.00021    relative symlink to the folder above
    public/                              figures of every paper

The slug has no dots or slashes because MyST themes route on them. The DOI
path is a symlink so that a reader can also address a paper by its DOI.
Figure files are named by content hash, so the papers can share public/. A
name that two papers use for different bytes is reported and the first one
is kept.

Everything lives under /DATA/cdn. The static exports in /DATA/10.55458 are
neither read nor changed.

    /DATA/cdn/sites/10.55458/neurolibre.00041.v2/  one site build per paper and version
    /DATA/cdn/builds/<time>/                       combined CDNs: served one and the one before
    /DATA/cdn/current                              symlink to the served build; the web server's root

For each paper, the highest version under sites/ goes into the CDN. `myst
sync` adds a site build there by hard-linking the one it copied from preview
into /DATA/myst (same filesystem, so no extra disk space), and the combined
builds hard-link their figures from sites/ the same way.

A new CDN is built next to the served one and switched in by replacing the
current symlink, so readers never see a half-written or missing CDN.

Usage (on the preprint server, from api/):
    python myst_cdn.py rebuild
    python myst_cdn.py add neurolibre.00041 v2 /DATA/myst/roboneurolibre/woven/<commit>/_build/site
"""

import argparse
import contextlib
import fcntl
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

_config = yaml.safe_load((Path(__file__).parent / "config" / "common.yaml").read_text())
DOI_PREFIX = _config["DOI_PREFIX"]
CDN_ROOT = Path(_config["DATA_ROOT_PATH"]) / _config["CDN_FOLDER"]
# The link to the served CDN, under CDN_ROOT.
CDN_CURRENT = CDN_ROOT / "current"
# Where `evidentron-data publish --out` writes Evidentron's static site.
EVIDENTRON_SITE = Path(_config["DATA_ROOT_PATH"]) / _config["EVIDENTRON_FOLDER"] / "site"

SITE_ID = "neurolibre"

# The default of `extras` in publish, add_and_publish and rebuild: Evidentron's site, read when called.
# Passing extras=None builds without it.
EVIDENTRON = object()

# How long a rebuild waits for another one to finish before it gives up, in seconds.
LOCK_WAIT = 1800


def project_slug(name: str) -> str:
    """neurolibre.00021 -> neurolibre-00021. MyST's slugToUrl turns dots into
    slashes, which would split the project over two URL levels."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")


def _with_project_prefix(node: Any, slug: str, pages: set) -> Any:
    """Rewrite a single-project page's internal URLs as a multi-project MyST
    build writes them (myst-cli process/mdast.ts): url /<slug>/<page> and
    dataUrl /<slug>/<page>.json. Figure URLs into public/ are left as they are."""
    if isinstance(node, list):
        return [_with_project_prefix(n, slug, pages) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("url", "dataUrl") and isinstance(value, str) and value.startswith("/") \
                and not value.startswith("//"):
            path, sep, frag = value.partition("#")
            name = path[1:]
            if key == "dataUrl" and name.endswith(".json") and name[:-5] in pages:
                value = f"/{slug}/{name}{sep}{frag}"
            elif key == "url" and (name == "" or name in pages):
                value = f"/{slug}/{name}{sep}{frag}"
        out[key] = _with_project_prefix(value, slug, pages)
    return out


def _place(src, dst) -> None:
    """Hard-link when possible (no extra disk space), copy otherwise."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


_VERSIONED = re.compile(r"^(?P<name>.+)\.v(?P<version>\d+)$")


def current_sites(root: Path = CDN_ROOT, doi_prefix: str = DOI_PREFIX) -> Dict[str, Path]:
    """{neurolibre.000NN: its highest-version folder under root/sites/<doi_prefix>/}."""
    newest: Dict[str, Tuple[int, Path]] = {}
    folder = Path(root) / "sites" / doi_prefix
    for site in sorted(folder.iterdir()) if folder.exists() else []:
        match = _VERSIONED.match(site.name)
        if not match or not site.is_dir():
            continue
        version = int(match["version"])
        if match["name"] not in newest or version > newest[match["name"]][0]:
            newest[match["name"]] = (version, site)
    return {name: site for name, (_, site) in newest.items()}


def add_site(name: str, version: str, src: Path, root: Path = CDN_ROOT,
             doi_prefix: str = DOI_PREFIX) -> Path:
    """Put a copy of the site build src at root/sites/<doi_prefix>/<name>.<version>,
    hard-linked, replacing what was there."""
    if not re.fullmatch(r"v\d+", version):
        raise ValueError(f"version must look like v1, not {version!r}")
    src = Path(src)
    if not (src / "config.json").exists():
        raise FileNotFoundError(f"no site build at {src}")
    dst = Path(root) / "sites" / doi_prefix / f"{name}.{version}"
    tmp = dst.with_name(f".{dst.name}.new")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, copy_function=_place)
    if dst.exists():
        old = dst.with_name(f".{dst.name}.old")
        shutil.rmtree(old, ignore_errors=True)
        os.rename(dst, old)
        os.rename(tmp, dst)
        shutil.rmtree(old)
    else:
        os.rename(tmp, dst)
    return dst


def _place_extras(extras: Path, out: Path) -> None:
    """Link Evidentron's terms/, wefts/, dois/ and index.json into out at the same paths. extras is usually a link
    to Evidentron's newest build folder, which a publish can swap at any time: it is resolved once, so every file
    comes from the same build."""
    extras = Path(os.path.realpath(extras))
    for entry in ("terms", "wefts", "dois", "index.json"):
        src = extras / entry
        files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
        for f in files:
            _place(f, out / f.relative_to(extras))


def build(sites: Dict[str, Path], out: Path, doi_prefix: str = DOI_PREFIX,
          extras: Optional[Path] = None) -> Dict[str, Any]:
    """Write the combined CDN of sites ({neurolibre.000NN: site build}) into out (a new folder).

    extras is Evidentron's static site. When it is a folder, its terms, exports and metadata are
    linked into out; when it is not there, the build goes on and the report says so."""
    out = Path(out)
    projects, clashes, version = [], [], 0
    for name in sorted(sites):
        site = Path(sites[name])
        config_file = site / "config.json"
        if not config_file.exists():
            raise FileNotFoundError(f"{name}: no site build at {site}")
        config = json.loads(config_file.read_text())
        version = max(version, config.get("version", 0))
        slug = project_slug(name)
        # One project per paper. A site build has exactly one.
        project = dict(config["projects"][0])
        project["slug"] = slug
        project.setdefault("doi", f"{doi_prefix}/{name}")
        # Paths on the build machine mean nothing to a reader.
        if "bibliography" in project:
            project["bibliography"] = [os.path.basename(b) for b in project["bibliography"]]
        projects.append(project)

        pages = {project.get("index", "index")} | {p["slug"] for p in project.get("pages", []) if p.get("slug")}
        for page in sorted((site / "content").glob("*.json")):
            data = _with_project_prefix(json.loads(page.read_text()), slug, pages)
            target = out / "content" / slug / page.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data))
        doi_dir = out / "content" / Path(*project["doi"].split("/"))
        doi_dir.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.path.relpath(out / "content" / slug, doi_dir.parent), doi_dir)

        public = site / "public"
        for f in sorted(p for p in public.rglob("*") if p.is_file()) if public.exists() else []:
            target = out / "public" / f.relative_to(public)
            if target.exists():
                if target.stat().st_size != f.stat().st_size or target.read_bytes() != f.read_bytes():
                    clashes.append(f"{f.relative_to(public)} ({name})")
                continue
            _place(f, target)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(
        {"version": version, "id": SITE_ID, "nav": [], "actions": [], "projects": projects}, indent=1))
    report: Dict[str, Any] = {"projects": len(projects), "clashes": clashes, "extras": False}
    if extras is not None:
        if Path(extras).is_dir():
            _place_extras(Path(extras), out)
            report["extras"] = True
        else:
            logging.warning("Evidentron's site was not at %s", extras)
            report["notes"] = ["Evidentron's site was not found, so the CDN carries no terms or exports yet."]
    return report


def _defaults(root, extras):
    return (Path(CDN_ROOT if root is None else root),
            EVIDENTRON_SITE if extras is EVIDENTRON else extras)


def publish(root: Optional[Path] = None, extras: Any = EVIDENTRON) -> Dict[str, Any]:
    """Build a new CDN of current_sites(root) in root/builds/<timestamp> and point root/current at it.

    root defaults to CDN_ROOT and extras to EVIDENTRON_SITE; extras=None builds without Evidentron's site.
    The new build and the one it replaced are kept; older ones are removed.
    """
    root, extras = _defaults(root, extras)
    link = root / CDN_CURRENT.name
    if link.exists() and not link.is_symlink():
        raise RuntimeError(f"{link} is a folder, not a symlink. Move it away before publishing.")
    builds = root / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = builds / stamp
    n = 1
    while out.exists():
        out = builds / f"{stamp}-{n}"
        n += 1
    try:
        report = build(current_sites(root), out, extras=extras)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    previous = os.path.realpath(link) if link.is_symlink() else None
    tmp_link = link.parent / f".{link.name}.new"
    if tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(os.path.relpath(out, link.parent), tmp_link)
    # rename() over a symlink is atomic: a reader sees the old CDN or the new one.
    os.replace(tmp_link, link)
    keep = {str(out.resolve()), previous}
    for old in builds.iterdir():
        if str(old.resolve()) not in keep:
            shutil.rmtree(old, ignore_errors=True)
    report["build"] = str(out)
    return report


def _duration(seconds: float) -> str:
    return f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"


@contextlib.contextmanager
def held(path: Path, wait: float, busy: str):
    """Hold an exclusive lock on the file at path, asking for it again every few seconds without blocking. Past
    `wait` seconds it raises TimeoutError with the sentence "<busy> for more than <wait>"."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as lock:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{busy} for more than {_duration(wait)}")
                time.sleep(min(5, wait / 10))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _rebuild_lock(root: Path):
    return held(root / ".lock", LOCK_WAIT, "another rebuild kept the CDN busy")


def add_and_publish(name: str, version: str, src: Path, root: Optional[Path] = None,
                    extras: Any = EVIDENTRON) -> Dict[str, Any]:
    """Add one paper's site build and rebuild the CDN, one caller at a time; TimeoutError when another caller
    holds the CDN longer than LOCK_WAIT."""
    root, extras = _defaults(root, extras)
    with _rebuild_lock(root):
        add_site(name, version, src, root)
        return publish(root, extras=extras)


def rebuild(root: Optional[Path] = None, extras: Any = EVIDENTRON) -> Dict[str, Any]:
    """Rebuild the CDN from the site builds already there, one caller at a time (as add_and_publish)."""
    root, extras = _defaults(root, extras)
    with _rebuild_lock(root):
        return publish(root, extras=extras)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--extras", type=Path, default=EVIDENTRON,
                        help="Evidentron's static site (evidentron-data publish --out)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild", help="rebuild the CDN from the site builds under sites/")
    add = commands.add_parser("add", help="add one paper's site build, then rebuild")
    add.add_argument("name", help="DOI suffix, for example neurolibre.00041")
    add.add_argument("version", help="for example v2")
    add.add_argument("site", type=Path, help="the site build (a _build/site folder)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "add":
        report = add_and_publish(args.name, args.version, args.site, args.root, args.extras)
    else:
        report = rebuild(args.root, args.extras)
    logging.info("%d papers in %s", report["projects"], report["build"])
    for note in report.get("notes", []):
        logging.warning("%s", note)
    for clash in report["clashes"]:
        logging.warning("figure name used with different bytes, first kept: %s", clash)


if __name__ == "__main__":
    sys.exit(main())
