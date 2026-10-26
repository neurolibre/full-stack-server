"""The steps of a paper's weft, as plain functions over the evidentron commands.

Each step composes the commands as subprocesses (the argv lists below are the one place they are written),
and turns every failure into a sentence for the caller: no step raises on a failed command, and no
traceback is returned. `run` takes an argv and returns (return code, output), so tests can replace it.
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

CDN_URL = "https://cdn.evidencepub.io"

Run = Callable[[List[str]], Tuple[int, str]]

WEFT = [sys.executable, "-m", "evidentron.weft"]
GRAPH = [sys.executable, "-m", "evidentron.graph"]
DATA_CLI = [sys.executable, "-m", "evidentron.cli.data_cli"]


@dataclass
class DraftResult:
    weft: Path
    profiles: Path
    drafted_by: str
    reason: Optional[str]
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)


@dataclass
class CheckResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unreadable: Optional[str] = None


@dataclass
class ConfirmResult:
    version: str = ""
    errors: List[str] = field(default_factory=list)
    addresses: Dict[str, object] = field(default_factory=dict)


def subprocess_runner(work: Path) -> Run:
    """Run an argv in `work` with the worker's environment, returning (return code, stdout and stderr)."""
    def run(argv: List[str]) -> Tuple[int, str]:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, cwd=work, env=os.environ)
        except OSError as e:
            return 1, f"The command could not be started: {e}"
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    return run


def _stem(doi: str) -> str:
    return doi.rsplit(".", 1)[-1]


def _last_line(output: str, fallback: str = "The command failed without saying why.") -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _lines(output: str, severity: str) -> List[str]:
    prefix = severity + ": "
    return [line[len(prefix):].strip() for line in output.splitlines() if line.startswith(prefix)]


def _crashed(output: str) -> bool:
    return "Traceback (most recent call last)" in output


def _sentence(text: str) -> str:
    text = text.strip()
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _entries(weft) -> list:
    entries = weft.get("files") if isinstance(weft, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def summary_of(weft_path: Path) -> List[str]:
    """One line per table and per collection, then the terms the weft proposes."""
    try:
        weft = _load(Path(weft_path))
    except (OSError, yaml.YAMLError):
        return []
    lines = []
    for entry in _entries(weft):
        if "collection" in entry:
            count = entry.get("member_count") or len(entry.get("members") or [])
            line = f"{entry['collection']}: a collection of {count} files" if count else \
                f"{entry['collection']}: a collection of files"
            if entry.get("description"):
                line += f"; one member is {_sentence(str(entry['description'])).rstrip('.')}"
        elif "file" in entry:
            line = f"{entry['file']}: {len(entry.get('fields') or [])} columns"
            row = entry.get("row")
            text = row.get("description") if isinstance(row, dict) else row
            text = text or entry.get("row_description")
            if text:
                line += f"; one row is {_sentence(str(text)).rstrip('.')}"
        else:
            continue
        lines.append(line)
    new_terms = weft.get("new_terms") if isinstance(weft, dict) else None
    for term_id, info in (new_terms or {}).items():
        label = info.get("label") if isinstance(info, dict) else None
        lines.append(f"proposed new term {term_id}" + (f": {label}" if label else ""))
    return lines


def _terms_used(weft) -> List[str]:
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("term", "concept") and isinstance(value, str):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(_entries(weft))
    return sorted(found)


def check(weft: Path, profiles: Path, data_dir: Path, run: Optional[Run] = None,
          db: Optional[Path] = None) -> CheckResult:
    """Check a weft against the profiles; `unreadable` is the sentence when the file cannot be read."""
    run = run or subprocess_runner(Path(weft).parent)
    try:
        _load(Path(weft))
    except OSError as e:
        return CheckResult(unreadable=f"The weft could not be read: {e}")
    except yaml.YAMLError as e:
        problem = str(e).splitlines()[0] if str(e) else "it is not valid YAML"
        return CheckResult(unreadable=f"The weft is not valid YAML, so it could not be checked: {problem}.")
    argv = WEFT + ["check", str(weft), "--profiles", str(profiles), "--data", str(data_dir)]
    if db:
        argv += ["--db", str(db)]
    code, output = run(argv)
    if _crashed(output):
        return CheckResult(unreadable=f"The weft could not be checked: {_last_line(output)}")
    errors, warnings = _lines(output, "error"), _lines(output, "warning")
    if code != 0 and not errors:
        errors = [_last_line(output)]
    return CheckResult(errors=errors, warnings=warnings)


def _explore(doi, profiles, weft, backend, data_dir, repo_dir, lock, run) -> Tuple[int, str]:
    argv = WEFT + ["explore", str(profiles), "--backend", backend, "--data", str(data_dir),
                   "--doi", doi, "-o", str(weft)]
    if repo_dir:
        argv += ["--repo", str(repo_dir)]
    if lock:
        argv += ["--lock", str(lock)]
    return run(argv)


def draft(doi: str, data_dir: Path, repo_dir: Optional[Path], work: Path, backends: dict, lock: Path,
          run: Optional[Run] = None) -> DraftResult:
    """Profile the data, explore it on the draft backend and check; on a failure, explore again on the redraft backend."""
    run = run or subprocess_runner(work)
    weft = Path(work) / f"{_stem(doi)}.weft.yaml"
    profiles = Path(work) / f"{_stem(doi)}.profiles.json"
    result = DraftResult(weft=weft, profiles=profiles, drafted_by=backends["draft"], reason=None)
    code, output = run(WEFT + ["profile", str(data_dir), "--doi", doi, "-o", str(profiles)])
    if code != 0:
        result.problems = [f"The data could not be profiled: {_last_line(output)}"]
        return result

    failure = None
    code, output = _explore(doi, profiles, weft, backends["draft"], data_dir, repo_dir, lock, run)
    if code != 0:
        failure = _last_line(output)
    else:
        first = check(weft, profiles, data_dir, run)
        if first.unreadable or first.errors:
            failure = first.unreadable or first.errors[0]
    if failure is None:
        final = first
    else:
        result.drafted_by, result.reason = backends["redraft"], failure
        code, output = _explore(doi, profiles, weft, backends["redraft"], data_dir, repo_dir, None, run)
        if code != 0:
            result.problems = [_last_line(output)]
            return result
        final = check(weft, profiles, data_dir, run)
    result.problems = [final.unreadable] if final.unreadable else final.errors
    result.warnings = final.warnings
    result.summary = summary_of(weft)
    return result


def confirm(weft: Path, profiles: Path, by: str, fields: Optional[List[str]], submission: Optional[Path],
            data_dir: Path, site: Path, db: Path, run: Optional[Run] = None) -> ConfirmResult:
    """Check, confirm, load, export both documents and publish the static files; stop at the first failure."""
    weft, work = Path(weft), Path(weft).parent
    run = run or subprocess_runner(work)
    if not submission:           # with a submission, the confirm command applies it and checks the result
        checked = check(weft, profiles, data_dir, run, db)
        if checked.unreadable:
            return ConfirmResult(errors=[checked.unreadable])
        if checked.errors:
            return ConfirmResult(errors=checked.errors)
    argv = WEFT + ["confirm", str(weft), "--by", by]
    if fields:
        argv += ["--fields", ",".join(fields)]
    if submission:
        argv += ["--submit", str(submission), "--profiles", str(profiles)]
    code, output = run(argv)
    if code != 0:
        return ConfirmResult(errors=[_last_line(output)])
    found = re.search(r"version (?:is now|stays) (\S+?)[,.]?(?:\s|$)", output)
    steps = [
        WEFT + ["load", str(weft), "--profiles", str(profiles), "--db", str(db), "--data", str(data_dir)],
        *[WEFT + ["export", str(weft), "--profiles", str(profiles), "--format", fmt, "--db", str(db),
                  "--data", str(data_dir)] for fmt in ("fair2", "croissant")],
        DATA_CLI + ["publish", "--out", str(site)],
    ]
    for step in steps:
        code, output_step = run(step)
        if code != 0:
            return ConfirmResult(errors=[_last_line(output_step)])
    try:
        weft_data = _load(weft)
    except (OSError, yaml.YAMLError):
        weft_data = {}
    doi = weft_data.get("paper") if isinstance(weft_data, dict) else None
    version = found.group(1) if found else str((weft_data or {}).get("version") or "")
    if not doi:
        return ConfirmResult(version=version, errors=["The confirmed weft names no paper, so its addresses are unknown."])
    return ConfirmResult(version=version, addresses=addresses_of(doi, _terms_used(weft_data)))


def addresses_of(doi: str, term_ids: List[str]) -> Dict[str, object]:
    return {
        "fair2": f"{CDN_URL}/wefts/{doi}/fair2.json",
        "croissant": f"{CDN_URL}/wefts/{doi}/croissant.json",
        "metadata": f"{CDN_URL}/dois/{doi}.json",
        "terms": [f"{CDN_URL}/terms/{t}/" for t in term_ids],
    }


def link_terms(doi: str, db: Path, backend: str, run: Optional[Run] = None) -> int:
    """Link the paper to the vocabulary's terms its text is about; returns how many links were kept."""
    run = run or subprocess_runner(Path(db).parent)
    code, output = run(GRAPH + ["concepts", "--paper", doi, "--backend", backend, "--db", str(db)])
    found = re.search(r":\s*(\d+) terms", output)
    return int(found.group(1)) if found else 0
