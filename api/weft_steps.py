"""The steps of a paper's description of its data, as plain functions over the evidentron commands.

Each step runs the commands as subprocesses with --json (the argv lists below are the one place they are written)
and reads the one JSON object each prints on stdout (Evidentron's contract: `command`, `ok`, `errors`, `warnings`,
`notes`, and keys of its own). No step reads the text a command prints, no step raises on a failed command, and
every failure is a sentence for the caller. `run` takes an argv and returns (exit code, output), so tests can
replace it; the output's `.stdout` holds the JSON object and its `.stderr` what the command printed for a person.

The commands run on the Python named by EVIDENTRON_PYTHON in the worker's environment (Evidentron's own virtual
environment), else on this one; it is read when a command is built, not when this module is imported.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

_config = yaml.safe_load((Path(__file__).parent / "config" / "common.yaml").read_text())
CDN_URL = _config["CDN_URL"].rstrip("/")

Run = Callable[[List[str]], Tuple[int, str]]

# How long each step may run before it is stopped, in seconds. explore first waits for the self-hosted model's
# slot, up to EXPLORE_SLOT_WAIT (Evidentron's own wait), and then runs for up to an hour.
EXPLORE_SLOT_WAIT = 3600
TIME_LIMITS = {"profile": 600, "explore": 3600 + EXPLORE_SLOT_WAIT, "check": 900, "confirm": 900, "load": 900,
               "export": 900, "publish": 900}
OTHER_TIME_LIMIT = 900          # loading the paper into the store, and linking its text to terms


def _python() -> str:
    return os.environ.get("EVIDENTRON_PYTHON") or sys.executable


def weft_command() -> List[str]:
    return [_python(), "-m", "evidentron.weft"]


def graph_command() -> List[str]:
    return [_python(), "-m", "evidentron.graph"]


def data_cli_command() -> List[str]:
    return [_python(), "-m", "evidentron.cli.data_cli"]


class Output(str):
    """What a command printed: the text is stdout and stderr together (for a log), `.stdout` the JSON object and
    `.stderr` what it printed for a person."""

    def __new__(cls, stdout: str = "", stderr: str = ""):
        out = super().__new__(cls, stdout + stderr)
        out.stdout, out.stderr = stdout, stderr
        return out


def _failed(sentence: str) -> Output:
    """The output of a command that never gave its own result: a result that is not ok, with the sentence."""
    return Output(json.dumps({"ok": False, "errors": [sentence], "warnings": [], "notes": []}))


def time_limit(argv: List[str]) -> Tuple[str, int]:
    """The step an argv runs, as a sentence names it, and how long it may run."""
    module, word = (list(argv) + ["", "", ""])[2:4]
    if module == "evidentron.graph":
        return ("term linking" if word == "concepts" else "store loading"), OTHER_TIME_LIMIT
    return word, TIME_LIMITS.get(word, OTHER_TIME_LIMIT)


def _duration(seconds: float) -> str:
    return f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"


def subprocess_runner(work: Path) -> Run:
    """Run an argv in `work` with the worker's environment, returning (exit code, Output). A step past its time
    limit is stopped, with every process it started, and gives a result with one sentence."""
    def run(argv: List[str]) -> Tuple[int, str]:
        step, limit = time_limit(argv)
        try:
            child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=work,
                                     env=os.environ, start_new_session=True)
        except OSError as e:
            return 1, _failed(f"The command could not be started: {e.strerror or e.__class__.__name__}.")
        try:
            stdout, stderr = child.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)       # the child leads its own group: its children go too
            except ProcessLookupError:
                pass
            child.communicate()
            return 1, _failed(f"The {step} step took longer than {_duration(limit)} and was stopped.")
        return child.returncode, Output(stdout or "", stderr or "")
    return run


NO_RESULT = "The command gave no result: "


def _no_result(stderr: str) -> str:
    """The sentence for a command that printed no JSON (it stopped before it could, as a crash on import does),
    with the last line it wrote on stderr."""
    said = [line.strip() for line in (stderr or "").split("\n") if line.strip()]
    if not said:
        return "The command gave no result and wrote nothing."
    return NO_RESULT + said[-1].rstrip(".") + "."


def outcome(code: int, output) -> dict:
    """The command's result: the JSON object it printed, with `ok` true only when its exit code is 0 too, and
    `errors` saying why whenever `ok` is false."""
    try:
        result = json.loads(getattr(output, "stdout", output) or "")
    except (TypeError, ValueError):
        result = None
    if not isinstance(result, dict):
        result = {"ok": False, "errors": [_no_result(getattr(output, "stderr", output))]}
    for key in ("errors", "warnings", "notes"):
        result[key] = [str(s) for s in result.get(key) or []]
    result["ok"] = result.get("ok") is True and code == 0
    if not result["ok"] and not result["errors"]:
        result["errors"] = [f"The command ended with exit code {code} without saying why."]
    return result


def _run(run: Run, argv: List[str]) -> dict:
    return outcome(*run(argv))


@dataclass
class DraftResult:
    weft: Path
    profiles: Path
    drafted_by: str                       # the backend
    reason: Optional[str]                 # why the second backend drafted, when it did
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)
    selfhosted: Optional[Path] = None
    model: Optional[str] = None


@dataclass
class CheckResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unreadable: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class ConfirmResult:
    version: str = ""
    errors: List[str] = field(default_factory=list)
    addresses: Dict[str, str] = field(default_factory=dict)     # metadata, fair2, croissant: publish's addresses
    terms: List[str] = field(default_factory=list)              # the ids of the terms the confirmed items use
    terms_url: Optional[str] = None                             # where publish put the terms, a page per id
    confirmed: bool = False               # the confirm command succeeded, whatever happened after it


@dataclass
class LinkResult:
    linked: Optional[int] = None          # how many links were kept; None when the linking failed
    labels: List[str] = field(default_factory=list)
    failure: Optional[str] = None


def _stem(doi: str) -> str:
    return doi.rsplit(".", 1)[-1]


def check(weft: Path, profiles: Path, data_dir: Path, run: Optional[Run] = None,
          db: Optional[Path] = None, submission: Optional[Path] = None) -> CheckResult:
    """Check a weft against the profiles and the data; with a submission, the weft as the submission would leave it
    (nothing is written). `unreadable` is the command's sentence when the weft cannot be read."""
    run = run or subprocess_runner(Path(weft).parent)
    argv = weft_command() + ["check", str(weft), "--profiles", str(profiles), "--data", str(data_dir), "--json"]
    if db:
        argv += ["--db", str(db)]
    if submission:
        argv += ["--submit", str(submission)]
    result = _run(run, argv)
    if result.get("unreadable"):
        return CheckResult(unreadable=result["unreadable"], notes=result["notes"])
    return CheckResult(errors=[] if result["ok"] else result["errors"], warnings=result["warnings"],
                       notes=result["notes"])


def _explore(doi, profiles, weft, backend, data_dir, repo_dir, lock, run) -> dict:
    argv = weft_command() + ["explore", str(profiles), "--backend", backend, "--data", str(data_dir),
                             "--doi", doi, "-o", str(weft), "--json"]
    if repo_dir:
        argv += ["--repo", str(repo_dir)]
    if lock:
        argv += ["--lock", str(lock)]
    return _run(run, argv)


def _why_not(explored: dict) -> List[str]:
    """Why explore did not give a draft: the problems of the draft it wrote, else its errors."""
    return [str(p) for p in explored.get("problems") or []] or explored["errors"]


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
    profiled = _run(run, weft_command() + ["profile", str(data_dir), "--doi", doi, "-o", str(profiles), "--json"])
    if not profiled["ok"]:
        result.problems = [f"The data could not be profiled: {profiled['errors'][0]}"]
        return result

    # The one place the earlier draft goes: the clone and the profile have worked, and explore writes the new one.
    _remove(weft)
    _remove(selfhosted)
    failure, first = None, None
    explored = _explore(doi, profiles, weft, backends["draft"], data_dir, repo_dir, lock, run)
    if not explored["ok"]:
        failure = _why_not(explored)[0]
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
        explored = _explore(doi, profiles, weft, backends["redraft"], data_dir, repo_dir, None, run)
        if not explored["ok"]:
            result.problems = _why_not(explored)
            return result
        final = check(weft, profiles, data_dir, run)
    drafted_by = explored.get("drafted_by") if isinstance(explored.get("drafted_by"), dict) else {}
    result.drafted_by = drafted_by.get("backend") or result.drafted_by
    result.model = drafted_by.get("model")
    result.problems = [final.unreadable] if final.unreadable else final.errors
    result.warnings = final.warnings
    result.summary = [str(line) for line in explored.get("summary") or []]
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
        return (f"The confirmed description could not be kept in the server's folder of descriptions, so it was "
                f"not published: {e.strerror or e.__class__.__name__}.")
    return None


PREVIOUS = ".previous"            # the suffix of the confirmed weft a reset kept, beside where it was


def _previous(record: Optional[Path], name: str) -> Optional[Path]:
    """The weft the version is measured against: the confirmed one in the folder of record, else the one a reset
    kept."""
    if record is None:
        return None
    for path in (Path(record) / name, Path(record) / (name + PREVIOUS)):
        if path.is_file():
            return path
    return None


def _not_exported(doi: str, published: dict) -> Optional[str]:
    """None when publish exported the paper's documents, else publish's sentence about the paper."""
    if doi.lower() in {str(d).lower() for d in published.get("exported") or []}:
        return None
    mine = [str(s) for s in published.get("refused") or [] if doi.lower() in str(s).lower()]
    return mine[0] if mine else \
        f"The FAIR² and Croissant documents of {doi} were not written when the site was published."


def _addresses(doi: str, published: dict) -> Dict[str, str]:
    addresses = published.get("addresses") or {}
    found = next((v for k, v in addresses.items() if str(k).lower() == doi.lower()), None)
    return dict(found) if isinstance(found, dict) else {}


def confirm(weft: Path, profiles: Path, by: str, fields: Optional[List[str]], submission: Optional[Path],
            data_dir: Path, site: Path, db: Path, run: Optional[Run] = None,
            record: Optional[Path] = None) -> ConfirmResult:
    """Check (with the submission, when there is one), confirm, keep the confirmed weft in `record` (the flat
    folder publish reads), load, export both documents and publish the static files; stop at the first failure.
    The weft already in `record` is the previous confirmed one, so the version moves by what changed since it."""
    weft = Path(weft)
    given = run is not None
    run = run or subprocess_runner(weft.parent)
    checked = check(weft, profiles, data_dir, run, db, submission)
    if checked.unreadable:
        return ConfirmResult(errors=[checked.unreadable])
    if checked.errors:
        return ConfirmResult(errors=checked.errors)
    argv = weft_command() + ["confirm", str(weft), "--by", by, "--json"]
    if fields:
        argv += ["--fields", ",".join(fields)]
    if submission:
        argv += ["--submit", str(submission), "--profiles", str(profiles)]
    previous = _previous(record, weft.name)
    if previous is not None:
        argv += ["--previous", str(previous)]
    done = _run(run, argv)
    if not done["ok"]:
        return ConfirmResult(errors=done["errors"])
    result = ConfirmResult(version=str(done.get("version") or ""), confirmed=True,
                           terms=[str(t) for t in done.get("terms") or []])

    def stop(errors: List[str]) -> ConfirmResult:
        result.errors = errors
        return result

    if record:
        sentence = _record(weft, Path(profiles), Path(record))
        if sentence:
            return stop([sentence])
    loaded = _run(run, weft_command() + ["load", str(weft), "--profiles", str(profiles), "--db", str(db),
                                         "--data", str(data_dir), "--json"])
    if not loaded["ok"]:
        return stop(loaded["errors"])
    doi = loaded.get("paper")
    if not doi:
        return stop(["The confirmed description names no paper, so its addresses are unknown."])
    for fmt in ("fair2", "croissant"):
        exported = _run(run, weft_command() + ["export", str(weft), "--profiles", str(profiles), "--format", fmt,
                                               "--db", str(db), "--data", str(data_dir), "--json"])
        if not exported["ok"]:
            return stop(exported["errors"])
    # `publish` has no --db: it reads the default store and the weft folder from the config it finds. The worker's
    # ~/.evidentron is a link to /DATA/evidentron (the provisioning sets that up), so the default store and
    # ~/.evidentron/config.yaml are the server's; running it in the store's folder also finds ./evidentron.yaml.
    publish_run = run if given else subprocess_runner(Path(db).parent)
    published = _run(publish_run, data_cli_command() + ["publish", "--out", str(site), "--json"])
    if not published["ok"]:
        return stop(published["errors"])
    missing = _not_exported(str(doi), published)
    if missing:
        return stop([missing])
    result.addresses = _addresses(str(doi), published)
    result.terms_url = published.get("terms_url")
    return result


def link_terms(doi: str, db: Path, backend: str, run: Optional[Run] = None) -> LinkResult:
    """Link the paper to the vocabulary's terms its text is about: how many links were kept and the terms' labels
    (the id, as code, for a term without one), or why the linking failed."""
    run = run or subprocess_runner(Path(db).parent)
    result = _run(run, graph_command() + ["concepts", "--paper", doi, "--backend", backend, "--db", str(db), "--json"])
    if not result["ok"]:
        failure = result["errors"][0]
        prefix = f"{doi}: "                 # the command names the paper in front of its sentence about it
        return LinkResult(failure=failure[len(prefix):] if failure.lower().startswith(prefix.lower()) else failure)
    labels = [str(t.get("label") or f"`{t.get('id')}`") for t in result.get("terms") or [] if isinstance(t, dict)]
    return LinkResult(linked=int(result.get("linked") or 0), labels=labels)


def load_paper(doi: str, site: Path, db: Path, run: Optional[Run] = None) -> Optional[str]:
    """Load the paper from a MyST site (the local CDN) into the store; the sentence when it could not be, else
    None. This command of Evidentron's has no --json, so it is read by its exit code, and a failure by the last
    line it wrote on stderr."""
    run = run or subprocess_runner(Path(db).parent)
    code, output = run(graph_command() + [str(site), "--doi", doi, "--db", str(db)])
    if code == 0:
        return None
    why = outcome(code, output)["errors"][0]      # a time limit or a failed start is already a sentence
    why = why[len(NO_RESULT):] if why.startswith(NO_RESULT) else why[:1].lower() + why[1:]
    return f"The paper could not be loaded into the store: {why}"


def reset(doi: str, wefts: Path, db: Path, site: Path) -> List[str]:
    """Start a paper's description over: remove its confirmed weft and profiles from the folder of record (wefts),
    keeping the weft as <stem>.weft.yaml.previous so the next confirmation measures its version against it, and
    remove its documents from Evidentron's site (site/wefts/<doi>/). Returns what was done, one sentence each.

    The paper's rows stay in the store (db): Evidentron has no command to remove them, and the next load of the
    paper replaces them. The last sentence says so."""
    wefts, stem = Path(wefts), _stem(doi)
    weft, profiles = wefts / f"{stem}.weft.yaml", wefts / f"{stem}.profiles.json"
    done = []
    try:
        if weft.is_file():
            os.replace(weft, wefts / (weft.name + PREVIOUS))
            done.append(f"The confirmed description of {doi} was removed and kept as {weft.name + PREVIOUS}, so the "
                        "next confirmation measures its version against it.")
        else:
            done.append(f"There is no confirmed description of {doi} on the server.")
        if profiles.is_file():
            profiles.unlink()
            done.append(f"Its profiles, {profiles.name}, were removed.")
        documents = Path(site) / "wefts" / doi
        if documents.is_dir():
            shutil.rmtree(documents)
            done.append("Its FAIR² and Croissant documents were removed from Evidentron's site; the CDN drops them "
                        "at its next rebuild.")
    except OSError as e:
        done.append(f"The reset stopped: {e.strerror or e.__class__.__name__}.")
        return done
    done.append("The store keeps the paper's rows until the next load of the paper replaces them, since Evidentron "
                "has no command to remove them.")
    return done
