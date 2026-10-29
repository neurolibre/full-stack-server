"""Sakiori (sakiori.evidencepub.io): each published paper's walk through the collection graph, built on this server.

The site and the crawl's scripts come from the woven repository and are put here by the deployment (cloodbrew's
app role), as Evidentron's wheel is. A build crawls the public CDN into a new folder, carries the notes and the
agent's answers over from the served build, writes the asked papers' notes (the self-hosted model) and the agent's
answer (Evidentron), and is then served by moving one link, as the CDN is:

    /DATA/sakiori/site/                 the built site (index.html, assets/), nginx's root
    /DATA/sakiori/crawl/                crawl.py, notes.py, moment.py, repository.py
    /DATA/sakiori/data/builds/<time>/   graph.json, walks/, notes/, moments/: one build each, the two latest kept
    /DATA/sakiori/data/current          link to the served build; nginx serves it at /data/

Only a failed crawl stops a build (the served one stays). A note or an answer that fails is said, and that paper
keeps the one it had. The notes need EVIDENTRON_LLM_URL (and EVIDENTRON_API_KEY) and the answers ANTHROPIC_API_KEY in
the worker's environment, as the weft steps do; the answers run on EVIDENTRON_PYTHON from Evidentron's folder, where
its evidentron.yaml is.

Usage (on the preprint server, from api/):
    python sakiori.py build [00052 ...]       # without numbers: every paper's notes and answers that are missing
"""

import argparse
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

_config = yaml.safe_load((Path(__file__).parent / "config" / "common.yaml").read_text())
SAKIORI_PATH = Path(_config["DATA_ROOT_PATH"]) / _config["SAKIORI_FOLDER"]
EVIDENTRON_PATH = Path(_config["DATA_ROOT_PATH"]) / _config["EVIDENTRON_FOLDER"]
SAKIORI_URL = _config["SAKIORI_URL"].rstrip("/")

KEEP = 2                                     # builds kept: the served one and the one before
CARRIED = ("notes", "moments")               # written per paper, so carried from build to build
# A build of every paper writes many notes and answers (the notes take three model calls a paper, an answer one agent
# turn); each is saved as it is written, so a step stopped at its limit loses nothing and the next build goes on.
TIME_LIMITS = {"crawl": 1200, "notes": 3 * 3600, "moment": 3 * 3600}
LOCK_WAIT = 7200                             # a build waits this long for another to finish
# The self-hosted model answers one request at a time, so the notes take its slot (the lock file the weft steps take,
# evidentron/frames/slot.py), waiting this long for it.
LLM_WAIT = 3600
BUILD_SECONDS = LOCK_WAIT + LLM_WAIT + sum(TIME_LIMITS.values()) + 120

Run = Callable[[List[str], str, Dict[str, str], int], Tuple[int, str]]


def _run(argv: List[str], cwd: str, env: Dict[str, str], timeout: int) -> Tuple[int, str]:
    """A step as a subprocess: its exit code and what it printed (the end of it is what a failure says)."""
    try:
        done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"stopped after {timeout // 60} minutes"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _why(output: str) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return lines[-1] if lines else "it said nothing"


@contextlib.contextmanager
def _locked(path: Path, wait: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        deadline = time.time() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise TimeoutError(f"another Sakiori build held the lock for {wait // 60} minutes")
                time.sleep(5)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def build(papers: List[str], root: Path = SAKIORI_PATH, evidentron: Path = EVIDENTRON_PATH, run: Run = _run,
          env: Optional[Dict[str, str]] = None, stamp: Optional[str] = None, llm_wait: int = LLM_WAIT) -> dict:
    """Build and serve Sakiori's data, with notes and answers written for `papers` (numbers like "00052"; none: every
    paper still missing them). Returns {"ok", "build", "said"}: whether a new build is served, where, and a sentence
    for each step that did not go as it should."""
    env = dict(os.environ if env is None else env)
    crawl, data = root / "crawl", root / "data"
    builds, current = data / "builds", data / "current"
    said: List[str] = []
    with _locked(root / "build.lock", LOCK_WAIT):
        new = builds / (stamp or time.strftime("%Y%m%d-%H%M%S"))
        new.mkdir(parents=True)
        code, out = run([sys.executable, str(crawl / "crawl.py"), "--out", str(new)], str(root), env,
                        TIME_LIMITS["crawl"])
        if code != 0 or not (new / "walks").is_dir():
            shutil.rmtree(new, ignore_errors=True)
            return {"ok": False, "build": None, "said": [f"The crawl of the CDN failed, so the served walks stay as they "
                                                         f"were: {_why(out)}"]}
        for folder in CARRIED:
            if (current / folder).is_dir():
                shutil.copytree(current / folder, new / folder)
        if env.get("EVIDENTRON_LLM_URL"):
            try:
                with _locked(evidentron / "llm.lock", llm_wait):
                    code, out = run([sys.executable, str(crawl / "notes.py"), "--data", str(new), *papers], str(root),
                                    env, TIME_LIMITS["notes"])
                if code != 0:
                    said.append(f"The notes were not written, so the papers keep the ones they had: {_why(out)}")
            except TimeoutError:
                said.append(f"The notes were not written: the self-hosted model was busy for {llm_wait // 60} minutes "
                            f"with another task. The papers keep the ones they had; the next build writes them.")
        else:
            said.append("The notes were not written: the worker has no EVIDENTRON_LLM_URL.")
        # A named paper is answered again (a new version, or a request); a full build only fills the gaps.
        again = ["--force"] if papers else []
        code, out = run([sys.executable, str(crawl / "moment.py"), "--data", str(new), *again, *papers], str(root),
                        {**env, "EVIDENTRON_DIR": str(evidentron)}, TIME_LIMITS["moment"])
        if code != 0:
            said.append(f"The agent's answer was not written, so the papers keep the ones they had: {_why(out)}")
        link = data / "current.next"
        with contextlib.suppress(FileNotFoundError):
            link.unlink()
        link.symlink_to(Path("builds") / new.name)
        os.replace(link, current)
        for old in sorted(p for p in builds.iterdir() if p.is_dir())[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "build": str(new), "said": said}


def address(number: str) -> str:
    return f"{SAKIORI_URL}/{number}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build")
    one.add_argument("papers", nargs="*")
    args = parser.parse_args()
    got = build(args.papers)
    for line in got["said"]:
        print(line)
    print(f"Served: {got['build']}" if got["ok"] else "Nothing new is served.")
    sys.exit(0 if got["ok"] else 1)


if __name__ == "__main__":
    main()
