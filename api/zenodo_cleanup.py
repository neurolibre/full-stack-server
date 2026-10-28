"""What publishing a paper's reproducibility assets leaves on the preprint server, removed once it is not needed.

The Zenodo tasks write one archive per asset (the book, the repository, the Docker image, the MyST build) into
the paper's archive folder, and the Docker task pulls the paper's image to save it. Nothing removed them, so every
published paper kept gigabytes on the server.

- The image is removed once `docker save` has written it into its archive: the archive is what Zenodo gets, and
  the registry still has the image.
- The archives are removed once all of the paper's Zenodo records are published, not when they are uploaded: until
  then an upload may be redone from them. The upload and publish records (the JSON files in the deposit folder) are
  kept; they are how the server knows what Zenodo has.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tar")


@dataclass
class Report:
    removed: List[str] = field(default_factory=list)
    freed: int = 0
    problems: List[str] = field(default_factory=list)


def remove_archives(folder) -> Report:
    """Remove the archives in a paper's archive folder, and the folder when nothing else is in it."""
    folder = Path(folder)
    report = Report()
    if not folder.is_dir():
        return report
    for entry in sorted(folder.iterdir()):
        if not (entry.is_file() and entry.name.endswith(ARCHIVE_SUFFIXES)):
            continue
        size = entry.stat().st_size
        try:
            entry.unlink()
        except OSError as e:
            report.problems.append(f"{entry.name} could not be removed: {e.strerror or e}.")
            continue
        report.removed.append(entry.name)
        report.freed += size
    if not any(folder.iterdir()):
        folder.rmdir()
    return report


def archives_sentence(report: Report) -> Optional[str]:
    """One sentence on the archives removed, for the issue; None when there were none."""
    if not report.removed:
        return None
    count = len(report.removed)
    what = "archive was" if count == 1 else f"{count} archives were"
    return (f"The {what} removed from the server ({readable(report.freed)}); Zenodo keeps the published copies."
            + "".join(f" {problem}" for problem in report.problems))


def remove_image(image: str, run=subprocess.run) -> Optional[str]:
    """Remove a Docker image from this host; None when it is removed, else one sentence on why it was not."""
    try:
        result = run(["docker", "image", "rm", image], capture_output=True, text=True)
    except FileNotFoundError:
        return f"The image {image} could not be removed from the server: docker was not found."
    if result.returncode == 0:
        return None
    reason = (result.stderr or result.stdout or "docker gave no reason").strip().splitlines()[-1].rstrip(".")
    return f"The image {image} could not be removed from the server: {reason}."


def readable(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
