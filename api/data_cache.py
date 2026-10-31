"""Caching a paper's data on the preview server (the preview_download_data task).

binder/data_requirement.json lists one dataset ({src, dst, projectName}) or several ({key: {src, dst, projectName}}).
repo2data downloads each to <data root>/<projectName> and leaves a record of the entry it downloaded there; it skips a
dataset whose record matches its entry. So a dataset can be:

- present: its folder holds a record of exactly this entry. repo2data skips it.
- missing: its folder does not exist. repo2data downloads it.
- conflicting: its folder exists but holds something else (another paper's data under the same name, an older
  version, or files put there by hand). Downloading would replace it, so that needs is_overwrite.

On the BinderHub share each dataset goes to the same place relative to the share as it has relative to the data root,
so notebooks find it at the same path on Binder as in a preview build.
"""

import json
import os
import re
from collections import namedtuple

# Letters (either case), digits, - and _, in segments separated by single slashes: no dots, so no "..", and no
# leading slash. (The task used to allow lower case only, which refused qmrlab/mooc's "qmrlab-mooc/02-T1-IR".)
PROJECT_NAME = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")

Plan = namedtuple("Plan", "present missing conflicting")


def datasets(manifest):
    """(record file name, entry) for each dataset the manifest lists. A manifest without any is refused."""
    if isinstance(manifest, dict) and "projectName" in manifest:
        found = [("repo2data_cache_record.json", manifest)]
    elif isinstance(manifest, dict):
        found = [(f"{key}_repo2data_cache_record.json", entry) for key, entry in manifest.items()
                 if isinstance(entry, dict) and "projectName" in entry]
    else:
        found = []
    if not found:
        raise ValueError("No dataset with a projectName in data_requirement.json")
    bad = [entry["projectName"] for _, entry in found if not PROJECT_NAME.match(str(entry["projectName"]))]
    if bad:
        raise ValueError(f"Project name(s) {', '.join(map(str, bad))} are not valid")
    return found


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def plan(data_root, manifest):
    """Which of the manifest's datasets are present, missing, or conflicting under data_root (by projectName)."""
    present, missing, conflicting = [], [], []
    for record_name, entry in datasets(manifest):
        project = entry["projectName"]
        folder = os.path.join(str(data_root), project)
        if not os.path.exists(folder):
            missing.append(project)
        elif _read(os.path.join(folder, record_name)) == entry:
            present.append(project)
        else:
            conflicting.append(project)
    return Plan(present, missing, conflicting)


def nfs_targets(data_root, nfs_root, paths):
    """(source, destination folder) for copying each dataset path to the BinderHub share with rsync (no trailing
    slash on the source, so rsync puts the folder itself inside the destination). A path outside data_root is
    refused."""
    root = os.path.realpath(str(data_root))
    targets = []
    for path in paths:
        real = os.path.realpath(str(path))
        if os.path.commonpath([root, real]) != root or real == root:
            raise ValueError(f"Not under the data folder: {path}")
        parent = os.path.dirname(os.path.relpath(real, root))
        targets.append((str(path), os.path.join(str(nfs_root), parent) if parent else str(nfs_root)))
    return targets
