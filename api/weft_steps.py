"""The steps of a paper's description of its data, as plain functions over the evidentron commands.

Each step runs the commands as subprocesses with --json (the argv lists below are the one place they are written)
and reads the one JSON object each prints on stdout (Evidentron's contract: `command`, `ok`, `errors`, `warnings`,
`notes`, and keys of its own). No step reads the text a command prints, no step raises on a failed command, and
every failure is a sentence for the caller. `run` takes an argv and returns (exit code, output), so tests can
replace it; the output's `.stdout` holds the JSON object and its `.stderr` what the command printed for a person.

The commands run on the Python named by EVIDENTRON_PYTHON in the worker's environment (Evidentron's own virtual
environment), else on this one; it is read when a command is built, not when this module is imported.
"""

import contextlib
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

import myst_cdn

_config = yaml.safe_load((Path(__file__).parent / "config" / "common.yaml").read_text())
CDN_URL = _config["CDN_URL"].rstrip("/")

# The server's places for Evidentron, in one place: the tasks and the editor's command read them from here.
EVIDENTRON_PATH = Path(_config["DATA_ROOT_PATH"]) / _config["EVIDENTRON_FOLDER"]
WEFTS_PATH = EVIDENTRON_PATH / "wefts"              # the folder of record: the confirmed wefts, flat
GRAPH_DB_PATH = EVIDENTRON_PATH / "graph.db"
SITE_PATH = myst_cdn.EVIDENTRON_SITE                # Evidentron's static site (a link to its newest build)
# Held while the store, the vocabulary or the site is written, so two writers never write them at once. A
# confirmation holds it longest (loading the paper, the check, the confirmation, the load, both exports, the
# publication and the CDN's rebuild); a writer waits at most STORE_LOCK_WAIT seconds for it.
STORE_LOCK_PATH = EVIDENTRON_PATH / "store.lock"
STORE_LOCK_WAIT = 1800

Run = Callable[[List[str]], Tuple[int, str]]

# How long each step may run before it is stopped, in seconds. explore with --lock first waits for the self-hosted
# model's slot, up to EXPLORE_SLOT_WAIT (Evidentron's own wait), and then runs for up to an hour.
EXPLORE_SLOT_WAIT = 3600
TIME_LIMITS = {"profile": 600, "explore": 3600, "check": 900, "confirm": 900, "load": 900, "export": 900,
               "publish": 900}
OTHER_TIME_LIMIT = 900          # loading the paper into the store, and linking its text to terms
CLONE_TIMEOUT = 600             # cloning the paper's repository
REBUILD_SECONDS = myst_cdn.LOCK_WAIT + 600          # the CDN's rebuild: its lock's wait, then the rebuild
# The celery limits of the tasks, from the steps they run at their own limits.
# draft: clone, two waits for the store, load, profile, explore with the slot wait, check, explore, check, terms.
DRAFT_SECONDS = (CLONE_TIMEOUT + 2 * STORE_LOCK_WAIT + OTHER_TIME_LIMIT + TIME_LIMITS["profile"]
                 + TIME_LIMITS["explore"] + EXPLORE_SLOT_WAIT + TIME_LIMITS["check"] + TIME_LIMITS["explore"]
                 + TIME_LIMITS["check"] + OTHER_TIME_LIMIT)
# confirm: a wait for the store, load, check, confirm, load, two exports, publish, the CDN's rebuild.
CONFIRM_SECONDS = (STORE_LOCK_WAIT + OTHER_TIME_LIMIT + TIME_LIMITS["check"] + TIME_LIMITS["confirm"]
                   + TIME_LIMITS["load"] + 2 * TIME_LIMITS["export"] + TIME_LIMITS["publish"] + REBUILD_SECONDS)


def store_lock():
    """Hold STORE_LOCK_PATH, waiting at most STORE_LOCK_WAIT seconds; TimeoutError past it."""
    return myst_cdn.held(Path(STORE_LOCK_PATH), STORE_LOCK_WAIT, "another task kept the paper store busy")


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
    """The output of a command that never gave its own result: a result that is not ok and did not run."""
    return Output(json.dumps({"ok": False, "ran": False, "errors": [sentence], "warnings": [], "notes": []}))


def time_limit(argv: List[str]) -> Tuple[str, int]:
    """The step an argv runs, as a sentence names it, and how long it may run."""
    module, word = (list(argv) + ["", "", ""])[2:4]
    if module == "evidentron.graph":
        return ("term linking" if word == "concepts" else "store loading"), OTHER_TIME_LIMIT
    limit = TIME_LIMITS.get(word, OTHER_TIME_LIMIT)
    return word, limit + (EXPLORE_SLOT_WAIT if word == "explore" and "--lock" in argv else 0)


def _kill(child) -> None:
    """Kill the child and every process it started (it leads its own group), and reap it."""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


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
            _kill(child)
            child.communicate()
            return 1, _failed(f"The {step} step took longer than {myst_cdn.duration(limit)} and was stopped.")
        except BaseException:
            # celery's soft time limit, a signal, anything: the step must not go on writing after the task has let
            # go of the store's lock, nor keep the model's slot.
            _kill(child)
            raise
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


USAGE_ERROR = 2                 # the exit code of a command line the command did not understand: it did not run


def outcome(code: int, output) -> dict:
    """The command's result: the JSON object it printed, with `ok` true only when its exit code is 0 too, and
    `errors` saying why whenever `ok` is false. `ran` is false when the command did not get to do its work: it
    printed no JSON, did not start, ran past its time or did not understand its command line."""
    try:
        result = json.loads(getattr(output, "stdout", output) or "")
    except (TypeError, ValueError):
        result = None
    if not isinstance(result, dict):
        result = {"ok": False, "ran": False, "errors": [_no_result(getattr(output, "stderr", output))]}
    result["ran"] = result.get("ran", True) is not False and code != USAGE_ERROR
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
    check_failure: Optional[str] = None   # the sentence when the check of the final draft could not be run


@dataclass
class CheckResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unreadable: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    failure: Optional[str] = None         # the sentence when the check could not be run at all


@dataclass
class ConfirmResult:
    version: str = ""
    errors: List[str] = field(default_factory=list)
    addresses: Dict[str, str] = field(default_factory=dict)     # metadata, fair2, croissant: publish's addresses
    terms: List[dict] = field(default_factory=list)             # {id, label} of the terms the confirmed items use
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
    if not result["ran"]:
        return CheckResult(failure=result["errors"][0])
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
        if first.failure or first.unreadable or first.errors:
            failure = first.failure or first.unreadable or first.errors[0]
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
    result.check_failure = final.failure
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
    mine = [str(r.get("sentence")) for r in published.get("refused") or []
            if isinstance(r, dict) and str(r.get("doi") or "").lower() == doi.lower() and r.get("sentence")]
    return mine[0] if mine else \
        f"The FAIR² and Croissant documents of {doi} were not written when the site was published."


def _lower_first(text: str) -> str:
    """The sentence with its first letter lower-cased when it starts with a plain word (as weft_messages does)."""
    words = text.split(maxsplit=1)
    if not words or any(c.isupper() for c in words[0][1:]):
        return text
    return text[:1].lower() + text[1:]


def publish(site: Path, db: Path, run: Optional[Run] = None) -> dict:
    """Publish Evidentron's static site (terms, documents, metadata) into a new build folder that `site` then links
    to; publish's result.

    `publish` has no --db: it reads the default store and the weft folder from the config it finds. The worker's
    ~/.evidentron is a link to /DATA/evidentron (the provisioning sets that up), so the default store and
    ~/.evidentron/config.yaml are the server's; running it in the store's folder also finds ./evidentron.yaml."""
    run = run or subprocess_runner(Path(db).parent)
    return _run(run, data_cli_command() + ["publish", "--out", str(site), "--json"])


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
    if checked.failure:
        return ConfirmResult(errors=["The check could not be run: " + _lower_first(checked.failure)])
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
                           terms=[{"id": str(t["id"]), "label": t.get("label")} for t in done.get("terms") or []
                                  if isinstance(t, dict) and t.get("id")])

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
    published = publish(site, db, run if given else None)
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
    """Load the paper from a MyST site (the local CDN) into the store; the command's sentence when it could not be,
    else None."""
    run = run or subprocess_runner(Path(db).parent)
    result = _run(run, graph_command() + [str(site), "--doi", doi, "--db", str(db), "--json"])
    if not result["ok"]:
        return result["errors"][0]
    papers = [p for p in result.get("papers") or [] if isinstance(p, dict)]
    if not any(p.get("loaded") and str(p.get("doi") or "").lower() == doi.lower() for p in papers):
        return f"The store has no row for {doi} after the load."
    return None


@dataclass
class ResetResult:
    said: List[str] = field(default_factory=list)     # what was done, one sentence each
    removed: bool = False                             # a confirmed description was there and was moved aside
    published: bool = False                           # Evidentron's site was published again without it


def reset(doi: str, wefts: Path, db: Path, site: Path, run: Optional[Run] = None) -> ResetResult:
    """Start a paper's description over: move its confirmed weft aside as <stem>.weft.yaml.previous (the next
    confirmation measures its version against it), remove its profiles and the work copy <wefts>/<stem>/ (so a later
    check or confirmation cannot act on it), and publish Evidentron's site again as a confirmation does, which
    builds it without the paper's documents and the terms only they used. The caller rebuilds the CDN.

    The paper's rows stay in the store (db): Evidentron has no command to remove them, and the next load of the
    paper replaces them. The last sentence says so."""
    wefts, stem = Path(wefts), _stem(doi)
    weft, profiles, work = wefts / f"{stem}.weft.yaml", wefts / f"{stem}.profiles.json", wefts / stem
    result = ResetResult()
    try:
        if weft.is_file():
            os.replace(weft, wefts / (weft.name + PREVIOUS))
            result.removed = True
            result.said.append(f"The confirmed description of {doi} was removed and kept as {weft.name + PREVIOUS}, "
                               "so the next confirmation measures its version against it.")
        else:
            result.said.append(f"There is no confirmed description of {doi} on the server.")
        if profiles.is_file():
            profiles.unlink()
            result.said.append(f"Its profiles, {profiles.name}, were removed.")
        if work.is_dir():
            shutil.rmtree(work)
            result.said.append(f"Its working copy, {stem}/, was removed.")
    except OSError as e:
        result.said.append(f"The reset stopped: {e.strerror or e.__class__.__name__}.")
        return result
    if result.removed:
        published = publish(site, db, run)
        if published["ok"]:
            result.published = True
            result.said.append("Evidentron's site was published again without its documents.")
        else:
            result.said.append("Evidentron's site could not be published again: "
                               + _lower_first(published["errors"][0]).rstrip(".") + ".")
            return result
    result.said.append("The store keeps the paper's rows until the next load of the paper replaces them, since "
                       "Evidentron has no command to remove them.")
    return result
