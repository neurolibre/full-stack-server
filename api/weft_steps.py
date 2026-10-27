"""The steps of a paper's weft, as plain functions over the evidentron commands.

Each step composes the commands as subprocesses (the argv lists below are the one place they are written),
and turns every failure into a sentence for the caller: no step raises on a failed command, and no
traceback is returned. `run` takes an argv and returns (return code, output), so tests can replace it.

The commands run on the Python named by EVIDENTRON_PYTHON in the worker's environment (Evidentron's own
virtual environment), else on this one; it is read when a command is built, not when this module is imported.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

CDN_URL = "https://cdn.evidencepub.io"
MAX_TERM_LINES = 10

Run = Callable[[List[str]], Tuple[int, str]]



def _python() -> str:
    return os.environ.get("EVIDENTRON_PYTHON") or sys.executable


def weft_command() -> List[str]:
    return [_python(), "-m", "evidentron.weft"]


def graph_command() -> List[str]:
    return [_python(), "-m", "evidentron.graph"]


def data_cli_command() -> List[str]:
    return [_python(), "-m", "evidentron.cli.data_cli"]


class Output(str):
    """A command's stdout and stderr together, with its stdout alone in `.stdout`, so that a warning on stderr
    is never taken for the sentence a command ends on."""

    def __new__(cls, text: str, stdout: str = ""):
        out = super().__new__(cls, text)
        out.stdout = stdout
        return out


@dataclass
class DraftResult:
    weft: Path
    profiles: Path
    drafted_by: str
    reason: Optional[str]
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)
    selfhosted: Optional[Path] = None


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
    confirmed: bool = False              # the confirm command succeeded, whatever happened after it


def subprocess_runner(work: Path) -> Run:
    """Run an argv in `work` with the worker's environment, returning (return code, stdout and stderr as an Output)."""
    def run(argv: List[str]) -> Tuple[int, str]:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, cwd=work, env=os.environ)
        except OSError as e:
            return 1, f"The command could not be started: {e.strerror or e.__class__.__name__}."
        return done.returncode, Output((done.stdout or "") + (done.stderr or ""), stdout=done.stdout or "")
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


def _why(output: str) -> str:
    """One sentence for a failed command: its last line, and for a crash the exception it ended on."""
    last = _last_line(output)
    return f"The command stopped with an error: {last}" if _crashed(output) else last


def _failures(output: str) -> List[str]:
    """The reasons a command refused: its `- ` lines when it lists them, else one sentence."""
    if _crashed(output):
        return [_why(output)]
    listed = [line[2:].strip() for line in output.splitlines() if line.startswith("- ")]
    return listed or [_why(output)]


def _sentence(text: str, lead: str) -> str:
    """The description as the end of "One row is ...": without the lead it would repeat, lower case at the start
    (see lower_first) and without a final full stop."""
    text = re.sub(rf"^one {lead} is\s+", "", str(text).strip(), flags=re.IGNORECASE).rstrip(".").rstrip()
    return lower_first(text)


def lower_first(text: str) -> str:
    """The text with its first letter lower-cased, unless its first word is an acronym or a name written with
    capitals inside it (NOAA, WIPO, iPhone): only when the rest of the first word has no capital letter."""
    words = text.split(maxsplit=1)
    if not words or any(c.isupper() for c in words[0][1:]):
        return text
    return text[:1].lower() + text[1:]


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _unreadable(weft: Path) -> Optional[str]:
    """The sentence when the weft cannot be read as YAML, else None."""
    try:
        _load(Path(weft))
    except OSError as e:
        return f"The weft could not be read: {e}"
    except yaml.YAMLError as e:
        mark, problem = getattr(e, "problem_mark", None), getattr(e, "problem", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
        problem = problem or (str(e).splitlines() or ["it is not valid YAML"])[0]
        return f"The weft is not valid YAML, so it could not be checked: {where}{problem}."
    except ValueError:
        return "The weft could not be read as text, so it could not be checked."
    return None


def _entries(weft) -> list:
    entries = weft.get("files") if isinstance(weft, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def summary_of(weft_path: Path) -> List[str]:
    """One sentence per table, per file that is not a table and per collection, then the terms the weft
    proposes: "`a.csv`: a table with 9 columns. One row is one plot in one year." The file name is in backticks,
    and each description is written as the end of the sentence, ending with one full stop."""
    try:
        weft = _load(Path(weft_path))
    except (OSError, ValueError, yaml.YAMLError):
        return []
    lines = []
    for entry in _entries(weft):
        if "collection" in entry:
            count = entry.get("member_count") or len(entry.get("members") or [])
            line = f"`{entry['collection']}`: " + (f"a collection of {count} files" if count else "a collection of files")
            if entry.get("description"):
                line += f". One member is {_sentence(entry['description'], 'member')}"
        elif "file" in entry or "file_pattern" in entry:
            name = entry.get("file") or entry.get("file_pattern")
            fields = entry.get("fields") or []
            if fields:
                line = f"`{name}`: a table with {len(fields)} column" + ("" if len(fields) == 1 else "s")
                row = entry.get("row")
                text = row.get("description") if isinstance(row, dict) else row
                text = text or entry.get("row_description")
                if text:
                    line += f". One row is {_sentence(text, 'row')}"
            else:
                kind = entry.get("kind")
                line = f"`{name}`: " + ("not a table" if kind in (None, "binary") else f"a {kind} file")
                if entry.get("description"):
                    line += f"; {_sentence(entry['description'], 'file')}"
        else:
            continue
        lines.append(line + ".")
    new_terms = weft.get("new_terms") if isinstance(weft, dict) else None
    if not isinstance(new_terms, dict):
        new_terms = {}
    for term_id, info in list(new_terms.items())[:MAX_TERM_LINES]:
        label = info.get("label") if isinstance(info, dict) else None
        label = str(label).strip().rstrip(".") if label else None
        lines.append(f"Proposed new term `{term_id}`" + (f": {label}." if label else "."))
    if len(new_terms) > MAX_TERM_LINES:
        lines.append(f"{len(new_terms) - MAX_TERM_LINES} more proposed terms.")
    return lines


def _terms_used(weft) -> List[str]:
    """The terms of the weft's confirmed fields, variables and parts, and the row and entry terms of the entries
    that hold one: what `publish` publishes, so that every address listed is there."""
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

    for entry in _entries(weft):
        confirmed = [item for name in ("fields", "variables", "parts") for item in entry.get(name) or []
                     if isinstance(item, dict) and item.get("status") == "confirmed"]
        if not confirmed:
            continue
        walk(confirmed)
        for owner in (entry, entry.get("row")):
            if isinstance(owner, dict):
                found.update(owner[k] for k in ("term", "concept") if isinstance(owner.get(k), str))
    return sorted(found)


def check(weft: Path, profiles: Path, data_dir: Path, run: Optional[Run] = None,
          db: Optional[Path] = None, submission: Optional[Path] = None, by: Optional[str] = None) -> CheckResult:
    """Check a weft against the profiles; `unreadable` is the sentence when the file cannot be read.
    With a submission, the weft is checked with it applied (see _check_submission)."""
    run = run or subprocess_runner(Path(weft).parent)
    sentence = _unreadable(weft)
    if sentence:
        return CheckResult(unreadable=sentence)
    if submission:
        return _check_submission(Path(weft), Path(profiles), Path(submission), by or "check", run)
    argv = weft_command() + ["check", str(weft), "--profiles", str(profiles), "--data", str(data_dir)]
    if db:
        argv += ["--db", str(db)]
    code, output = run(argv)
    if _crashed(output):
        return CheckResult(unreadable=f"The weft could not be checked: {_last_line(output)}")
    errors, warnings = _lines(output, "error"), _lines(output, "warning")
    if code != 0 and not errors:
        errors = [_last_line(output)]
    return CheckResult(errors=errors, warnings=warnings)


def _check_submission(weft: Path, profiles: Path, submission: Path, by: str, run: Run) -> CheckResult:
    """`weft check` takes no submission, and applying one is Evidentron's work: so `weft confirm --submit` runs
    on a copy of the weft in a scratch folder, which is then removed, and only what it says is kept. Its
    errors are its `- ` lines (or its last line), and the notes the merge made are the warnings."""
    with tempfile.TemporaryDirectory(prefix="weft-check-") as scratch:
        copy = Path(scratch) / weft.name
        shutil.copyfile(weft, copy)
        code, output = run(weft_command() + ["confirm", str(copy), "--by", by, "--submit", str(submission),
                                             "--profiles", str(profiles)])
    if _crashed(output):
        return CheckResult(unreadable=f"The weft could not be checked: {_last_line(output)}")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    listed = [i for i, line in enumerate(lines) if line.startswith("- ")]
    # Before the listed errors comes the line that introduces them; without a list, the last line is the outcome.
    end = listed[0] - 1 if listed else len(lines) - 1
    notes = [line for line in lines[:max(end, 0)] if not line.startswith("- ")]
    if code != 0:
        return CheckResult(errors=_failures(output), warnings=notes)
    return CheckResult(warnings=notes)


def _explore(doi, profiles, weft, backend, data_dir, repo_dir, lock, run) -> Tuple[int, str]:
    argv = weft_command() + ["explore", str(profiles), "--backend", backend, "--data", str(data_dir),
                   "--doi", doi, "-o", str(weft)]
    if repo_dir:
        argv += ["--repo", str(repo_dir)]
    if lock:
        argv += ["--lock", str(lock)]
    return run(argv)


PROBLEM = "# PROBLEM: "
DID_NOT_START = "The exploration did not start:"


def _problems_at_top(weft: Path) -> List[str]:
    """The `# PROBLEM:` lines explore writes at the top of a weft it did not accept."""
    try:
        lines = Path(weft).read_text().splitlines()
    except (OSError, ValueError):
        return []
    found = []
    for line in lines:
        if not line.startswith(PROBLEM):
            break
        found.append(line[len(PROBLEM):].strip())
    return found


def _explore_failure(weft: Path, output: str) -> List[str]:
    """Why explore failed, never the path it names in its last line: the problems at the top of the weft it
    wrote, else its sentence for a slot that stayed busy, else the exception of a crash, else its last line on
    stdout."""
    problems = _problems_at_top(weft)
    if problems:
        return problems
    started = [line.strip() for line in output.splitlines() if line.strip().startswith(DID_NOT_START)]
    if started:
        return [started[-1]]
    if _crashed(output):
        return [_why(output)]
    return [_last_line(getattr(output, "stdout", None) or output)]


def _remove(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def draft(doi: str, data_dir: Path, repo_dir: Optional[Path], work: Path, backends: dict, lock: Path,
          run: Optional[Run] = None) -> DraftResult:
    """Profile the data, explore it on the draft backend and check; on a failure, explore again on the redraft
    backend, keeping the first draft beside the second as <stem>.selfhosted.weft.yaml."""
    run = run or subprocess_runner(work)
    weft = Path(work) / f"{_stem(doi)}.weft.yaml"
    profiles = Path(work) / f"{_stem(doi)}.profiles.json"
    selfhosted = Path(work) / f"{_stem(doi)}.selfhosted.weft.yaml"
    result = DraftResult(weft=weft, profiles=profiles, drafted_by=backends["draft"], reason=None)
    code, output = run(weft_command() + ["profile", str(data_dir), "--doi", doi, "-o", str(profiles)])
    if code != 0:
        result.problems = [f"The data could not be profiled: {_why(output)}"]
        return result

    failure = None
    _remove(weft)
    _remove(selfhosted)
    code, output = _explore(doi, profiles, weft, backends["draft"], data_dir, repo_dir, lock, run)
    if code != 0:
        failure = _explore_failure(weft, output)[0]
    else:
        first = check(weft, profiles, data_dir, run)
        if first.unreadable or first.errors:
            failure = first.unreadable or first.errors[0]
    if failure is None:
        final = first
    else:
        result.drafted_by, result.reason = backends["redraft"], failure
        if weft.exists():
            os.replace(weft, selfhosted)
            result.selfhosted = selfhosted
        code, output = _explore(doi, profiles, weft, backends["redraft"], data_dir, repo_dir, None, run)
        if code != 0:
            result.problems = _explore_failure(weft, output)
            return result
        final = check(weft, profiles, data_dir, run)
    result.problems = [final.unreadable] if final.unreadable else final.errors
    result.warnings = final.warnings
    result.summary = summary_of(weft)
    return result


def _record(weft: Path, profiles: Path, record: Path) -> Optional[str]:
    """Copy the confirmed weft and its profiles to the folder of record, flat, as publish reads them; the
    sentence when they could not be copied, else None."""
    try:
        Path(record).mkdir(parents=True, exist_ok=True)
        for src in (weft, profiles):
            tmp = Path(record) / f".{Path(src).name}.new"
            shutil.copyfile(src, tmp)
            os.replace(tmp, Path(record) / Path(src).name)
    except OSError as e:
        return (f"The confirmed weft could not be kept in the server's folder of wefts, so it was not published: "
                f"{e.strerror or e.__class__.__name__}.")
    return None


def _not_exported(doi: str, site: Path, since: float, output: str) -> Optional[str]:
    """None when publish wrote both documents of the paper during this run, else publish's own sentence."""
    folder = Path(site) / "wefts" / doi
    fresh = all((folder / f"{fmt}.json").is_file() and (folder / f"{fmt}.json").stat().st_mtime >= since
                for fmt in ("fair2", "croissant"))
    if fresh:
        return None
    refused = [line[2:].strip() for line in output.splitlines() if line.startswith("- ")]
    mine = [line for line in refused if doi.lower() in line.lower()]
    if mine:
        return mine[0]
    return f"The FAIR² and Croissant documents of {doi} were not written when the site was published."


def confirm(weft: Path, profiles: Path, by: str, fields: Optional[List[str]], submission: Optional[Path],
            data_dir: Path, site: Path, db: Path, run: Optional[Run] = None,
            record: Optional[Path] = None) -> ConfirmResult:
    """Check, confirm, keep the confirmed weft in `record` (the flat folder publish reads), load, export both
    documents and publish the static files; stop at the first failure. The weft already in `record` is the
    previous confirmed one, so the version moves by what changed since it."""
    weft, work = Path(weft), Path(weft).parent
    given = run is not None
    run = run or subprocess_runner(work)
    sentence = _unreadable(weft)
    if sentence:
        return ConfirmResult(errors=[sentence])
    if not submission:           # with a submission, the confirm command applies it and checks the result
        checked = check(weft, profiles, data_dir, run, db)
        if checked.unreadable:
            return ConfirmResult(errors=[checked.unreadable])
        if checked.errors:
            return ConfirmResult(errors=checked.errors)
    argv = weft_command() + ["confirm", str(weft), "--by", by]
    if fields:
        argv += ["--fields", ",".join(fields)]
    if submission:
        argv += ["--submit", str(submission), "--profiles", str(profiles)]
    previous = Path(record) / weft.name if record else None
    if previous is not None and previous.is_file():
        argv += ["--previous", str(previous)]
    code, output = run(argv)
    if code != 0:
        return ConfirmResult(errors=_failures(output))
    found = re.search(r"version (?:is now|stays) (\S+?)[,.]?(?:\s|$)", output)
    try:
        weft_data = _load(weft)
    except (OSError, ValueError, yaml.YAMLError):
        weft_data = {}
    weft_data = weft_data if isinstance(weft_data, dict) else {}
    doi = weft_data.get("paper")
    version = found.group(1) if found else str(weft_data.get("version") or "")
    if not doi:
        return ConfirmResult(version=version, confirmed=True,
                             errors=["The confirmed weft names no paper, so its addresses are unknown."])
    if record:
        sentence = _record(weft, Path(profiles), Path(record))
        if sentence:
            return ConfirmResult(version=version, confirmed=True, errors=[sentence])
    publish = data_cli_command() + ["publish", "--out", str(site)]
    steps = [
        weft_command() + ["load", str(weft), "--profiles", str(profiles), "--db", str(db), "--data", str(data_dir)],
        *[weft_command() + ["export", str(weft), "--profiles", str(profiles), "--format", fmt, "--db", str(db),
                            "--data", str(data_dir)] for fmt in ("fair2", "croissant")],
        publish,
    ]
    # `publish` has no --db: it reads the default store and the weft folder from the config it finds. The
    # worker's ~/.evidentron is a link to /DATA/evidentron (the provisioning sets that up), so the default
    # store and ~/.evidentron/config.yaml are the server's; running it in the store's folder also finds
    # ./evidentron.yaml there.
    publish_run = run if given else subprocess_runner(Path(db).parent)
    since = time.time() - 1                  # a file publish writes from now on is this run's
    for step in steps:
        code, output_step = (publish_run if step is publish else run)(step)
        if code != 0:
            return ConfirmResult(version=version, confirmed=True, errors=_failures(output_step))
    missing = _not_exported(doi, Path(site), since, output_step)
    if missing:
        return ConfirmResult(version=version, confirmed=True, errors=[missing])
    return ConfirmResult(version=version, confirmed=True, addresses=addresses_of(doi, _terms_used(weft_data)))


def addresses_of(doi: str, term_ids: List[str]) -> Dict[str, object]:
    return {
        "fair2": f"{CDN_URL}/wefts/{doi}/fair2.json",
        "croissant": f"{CDN_URL}/wefts/{doi}/croissant.json",
        "metadata": f"{CDN_URL}/dois/{doi}.json",
        "terms": [f"{CDN_URL}/terms/{t}/" for t in term_ids],
    }


def link_terms(doi: str, db: Path, backend: str, run: Optional[Run] = None) -> Tuple[Optional[int], Optional[str]]:
    """Link the paper to the vocabulary's terms its text is about. Returns (how many links were kept, None), or
    (None, why) when the linking failed: the command says "<doi>: <reason>" for a paper it could not link."""
    run = run or subprocess_runner(Path(db).parent)
    code, output = run(graph_command() + ["concepts", "--paper", doi, "--backend", backend, "--db", str(db)])
    found = re.search(r":\s*(\d+) terms", output)
    if found and code == 0:
        return int(found.group(1)), None
    if _crashed(output):
        return None, _why(output)
    last = _last_line(getattr(output, "stdout", None) or output)
    return None, re.sub(r"^\S+:\s+", "", last) if last.lower().startswith(doi.lower() + ":") else last
