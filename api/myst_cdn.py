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
import fcntl
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

_config = yaml.safe_load((Path(__file__).parent / "config" / "common.yaml").read_text())
DOI_PREFIX = _config["DOI_PREFIX"]
CDN_ROOT = Path(_config["DATA_ROOT_PATH"]) / _config["CDN_FOLDER"]

SITE_ID = "neurolibre"


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


def build(sites: Dict[str, Path], out: Path, doi_prefix: str = DOI_PREFIX) -> Dict[str, Any]:
    """Write the combined CDN of sites ({neurolibre.000NN: site build}) into out (a new folder)."""
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
    return {"projects": len(projects), "clashes": clashes}


def publish(root: Path = CDN_ROOT) -> Dict[str, Any]:
    """Build a new CDN of current_sites(root) in root/builds/<timestamp> and point root/current at it.

    The new build and the one it replaced are kept; older ones are removed.
    """
    root = Path(root)
    link = root / "current"
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
        report = build(current_sites(root), out)
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


def add_and_publish(name: str, version: str, src: Path, root: Path = CDN_ROOT) -> Dict[str, Any]:
    """Add one paper's site build and rebuild the CDN, one caller at a time."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        add_site(name, version, src, root)
        return publish(root)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=CDN_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild", help="rebuild the CDN from the site builds under sites/")
    add = commands.add_parser("add", help="add one paper's site build, then rebuild")
    add.add_argument("name", help="DOI suffix, for example neurolibre.00041")
    add.add_argument("version", help="for example v2")
    add.add_argument("site", type=Path, help="the site build (a _build/site folder)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "add":
        report = add_and_publish(args.name, args.version, args.site, args.root)
    else:
        with open(args.root / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            report = publish(args.root)
    logging.info("%d papers in %s", report["projects"], report["build"])
    for clash in report["clashes"]:
        logging.warning("figure name used with different bytes, first kept: %s", clash)


if __name__ == "__main__":
    sys.exit(main())
