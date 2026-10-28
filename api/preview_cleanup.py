"""Cleaning up after a paper's technical screening on the preview server.

Every successful MyST build of a repository is kept as a full copy in a folder named after its commit, beside an
archive of it, a working clone (`latest/`) the builds reuse, and `latest.txt` naming the newest; and every build
pulls the paper's image from the registry into this host's Docker. Nothing removed them, so they piled up.

The cleanup keeps the build `latest.txt` names (production start checks it, and it is the one the reviewers saw)
and removes the other builds, their archives and the working clone, then the images pulled for the repository.
Everything it removes is made again by the next build: a build with no working clone clones the repository, and a
build pulls its image. The working clone holds the execution cache, so the next build of the repository executes
the notebooks again. The paper's data is not touched, since production copies it from this server, and neither
are the logs.
"""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from myst_libre.utils.naming import BinderHubNaming

LATEST_MARKER = "latest.txt"
WORKING_CLONE = "latest"
COMMIT = re.compile(r"[0-9a-f]{7,40}")


@dataclass
class Report:
    removed: List[str] = field(default_factory=list)    # folder entries, or one tag per image
    freed: int = 0                                       # bytes
    notes: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def clean_builds(myst_root, owner: str, repo: str, keep: Iterable[str] = ()) -> Report:
    """Remove every build of owner/repo but the one latest.txt names and those in `keep` (commits), with the
    archives and the working clone."""
    root = Path(myst_root).resolve()
    folder = (root / owner / repo).resolve()
    if folder.parent.parent != root:
        raise ValueError(f"{owner}/{repo} is not a repository name.")
    report = Report()
    if not folder.is_dir():
        report.notes.append(f"There are no MyST builds of {owner}/{repo} on the preview server.")
        return report
    marker = folder / LATEST_MARKER
    if not marker.is_file():
        report.problems.append(f"No successful build is recorded for {owner}/{repo} (no {LATEST_MARKER}), so its "
                               f"builds were left as they are.")
        return report
    kept = marker.read_text().strip()
    if not (folder / kept).is_dir():
        report.problems.append(f"{LATEST_MARKER} names the build {kept}, which is not on the server, so the builds "
                               f"of {owner}/{repo} were left as they are.")
        return report

    keep = [c for c in keep if c and c != kept]
    for commit in keep:
        if not (folder / commit).is_dir():
            report.notes.append(f"There is no build of commit {commit} to keep.")
    kept_names = {LATEST_MARKER} | {name for c in [kept, *keep] for name in (c, f"{c}.tar.gz")}

    others = 0
    for entry in sorted(folder.iterdir()):
        name = entry.name
        if name in kept_names:
            continue
        is_build = entry.is_dir() and not entry.is_symlink() and (name == WORKING_CLONE or COMMIT.fullmatch(name))
        is_archive = entry.is_file() and name.endswith(".tar.gz") and COMMIT.fullmatch(name[:-len(".tar.gz")])
        if not (is_build or is_archive):
            others += 1
            continue
        size = _size(entry)
        try:
            if is_build:
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as e:
            report.problems.append(f"{name} could not be removed: {e.strerror or e}.")
            continue
        report.removed.append(name)
        report.freed += size
    if others:
        report.notes.append(f"{others} other {'entry' if others == 1 else 'entries'} in the folder "
                            f"{'is' if others == 1 else 'are'} not builds and "
                            f"{'was' if others == 1 else 'were'} left as they are.")
    return report


def remove_images(docker_client, owner: str, repo: str) -> Report:
    """Remove the images of owner/repo that builds pulled into this host's Docker. An image is recognised by the
    name BinderHub gives a repository's images, whatever registry it came from and whatever its tag."""
    name = f"{owner}/{repo}"
    exact = BinderHubNaming.build_exact_image_name(name).lower()
    pattern = re.compile(BinderHubNaming.build_search_pattern(name).lower())
    report = Report()
    for image in docker_client.images.list():
        tags = [t for t in (image.tags or []) if _image_name(t) == exact or pattern.match(_image_name(t))]
        if not tags:
            continue
        try:
            docker_client.images.remove(image.id, force=False)
        except Exception:
            report.problems.append(f"The image {tags[0]} could not be removed, most likely because a container "
                                   f"still uses it.")
            continue
        report.removed.append(tags[0])
        report.freed += (getattr(image, "attrs", None) or {}).get("Size", 0)
    return report


def _image_name(tag: str) -> str:
    """The image's own name, without the registry, the project and the tag: `host:5000/project/name:tag` → name."""
    last = tag.rsplit("/", 1)[-1]
    return last.split(":", 1)[0].lower()


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def readable(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def kept_build(myst_root, owner: str, repo: str):
    """The commit of the build latest.txt names when that build is on the server, else None."""
    folder = Path(myst_root) / owner / repo
    marker = folder / LATEST_MARKER
    if not marker.is_file():
        return None
    commit = marker.read_text().strip()
    return commit if (folder / commit).is_dir() else None
