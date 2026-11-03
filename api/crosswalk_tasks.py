"""The crosswalk of a paper's data: draft, check and confirm, as celery tasks that report on the review issue.

The steps themselves are in crosswalk_steps and the texts in crosswalk_messages; the tasks add the places on this
server and the comment. The server is the place of record for the crosswalks: the only thing these tasks write on
GitHub is the comment on the review issue, and the paper's repository is only read, cloned at its published commit.

The celery module imports this one at its end, which registers the tasks on its app under the
celery module's names (neurolibre_celery_tasks.crosswalk_*_task), as the app's other production tasks are named.
"""

import contextlib
import logging
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import requests
import yaml
from task_outcome import TaskFailed
from github import GithubException, UnknownObjectException

import myst_cdn
import crosswalk_messages as say
import crosswalk_steps
from github_client import gh_clone_repository, gh_get_issue_comment, gh_get_project_name, gh_read_from_issue_body
from neurolibre_celery_tasks import (BaseNeuroLibreTask, DATA_NFS_PATH, DOI_PREFIX, DOI_SUFFIX, GH_ORGANIZATION,
                                     _published_commit, celery_app, handle_soft_timeout)

# The places on the server are crosswalk_steps' (EVIDENTRON_PATH, CROSSWALKS_PATH, GRAPH_DB_PATH, SITE_PATH, the
# store's lock).
EVIDENTRON_PATH = str(crosswalk_steps.EVIDENTRON_PATH)
GRAPH_DB_PATH = str(crosswalk_steps.GRAPH_DB_PATH)
CROSSWALKS_PATH = str(crosswalk_steps.CROSSWALKS_PATH)
REPOS_PATH = os.path.join(EVIDENTRON_PATH, "repos")
LLM_LOCK_PATH = os.path.join(EVIDENTRON_PATH, "llm.lock")
EVIDENTRON_CONFIG = os.path.join(EVIDENTRON_PATH, "evidentron.yaml")
EVIDENTRON_SITE_PATH = str(crosswalk_steps.SITE_PATH)
CDN_CURRENT_PATH = str(myst_cdn.CDN_CURRENT)
HARD_MARGIN = 600               # the hard time limit of a task: its soft limit, then this long to say why it stopped
BACKEND_KEYS = ("draft", "redraft", "terms")

# A fenced block whose info string is yaml or yml, closed by a fence as long as its opening one; the last one
# in a comment is its corrections.
YAML_BLOCK = re.compile(r"^[ \t]*(`{3,})[ \t]*(?:yaml|yml)[ \t]*\n(.*?)^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL | re.IGNORECASE)
MAX_DRAFT_IN_COMMENT = 60000


def _crosswalk_backends(task):
    """The backends for drafting, redrafting and linking terms, from `crosswalk: backend:` in the server's Evidentron
    config; a key it lacks fails the task with one sentence."""
    try:
        with open(EVIDENTRON_CONFIG) as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    crosswalk = config.get("crosswalk") if isinstance(config, dict) else None
    found = crosswalk.get("backend") if isinstance(crosswalk, dict) else None
    found = found if isinstance(found, dict) else {}
    for key in BACKEND_KEYS:
        if not found.get(key):
            task.fail(say.no_backend(key))
    return {key: str(found[key]) for key in BACKEND_KEYS}


def _crosswalk_paper(task):
    """What every task needs: the paper's DOI, its stem, the data folder, the work folder and the file names."""
    issue_id = task.screening.issue_id
    doi = f"{DOI_PREFIX}/{DOI_SUFFIX}.{issue_id:05d}"
    stem = crosswalk_steps._stem(doi)
    try:
        project_name = gh_get_project_name(task.screening.github_client, task.screening.target_repo_url)
    except (GithubException, requests.RequestException) as e:
        task.fail(say.data_folder_unknown(_github_reason(e)))
    except Exception as e:
        reason = str(e).strip()
        task.fail(say.data_folder_unknown(reason if reason and "\n" not in reason else None))
    data_dir = os.path.join(DATA_NFS_PATH, project_name)
    if not os.path.isdir(data_dir):
        task.fail(say.data_missing(project_name))
    work = os.path.join(CROSSWALKS_PATH, stem)
    os.makedirs(work, exist_ok=True)
    return SimpleNamespace(doi=doi, stem=stem, data_dir=data_dir, work=work,
                           crosswalk_name=f"{stem}.crosswalk.yaml", profiles_name=f"{stem}.profiles.json")


def _server_crosswalk(task, paper):
    """The crosswalk and its profiles from the server, in the work folder: a copy of the flat confirmed crosswalk and
    its profiles when there is one (so a failed step never touches the confirmed copy), else the draft. Fails with a
    sentence when the crosswalk or its profiles are missing."""
    crosswalk = os.path.join(paper.work, paper.crosswalk_name)
    profiles = os.path.join(paper.work, paper.profiles_name)
    confirmed = os.path.join(CROSSWALKS_PATH, paper.crosswalk_name)
    if os.path.isfile(confirmed):
        confirmed_profiles = os.path.join(CROSSWALKS_PATH, paper.profiles_name)
        if not os.path.isfile(confirmed_profiles):
            task.fail(say.CONFIRMED_WITHOUT_PROFILES)
        shutil.copyfile(confirmed, crosswalk)
        shutil.copyfile(confirmed_profiles, profiles)
    if not (os.path.isfile(crosswalk) and os.path.isfile(profiles)):
        task.fail(say.NO_DRAFT)
    return crosswalk, profiles


def _github_reason(e):
    if isinstance(e, GithubException):
        data = e.data if isinstance(e.data, dict) else {}
        return f"{e.status} {data.get('message') or ''}".strip()
    return e.__class__.__name__


@contextlib.contextmanager
def _errors_as_sentences(task):
    """A GitHub, network or local file error inside becomes one sentence on the issue; the task's own failures
    pass through."""
    try:
        yield
    except TaskFailed:
        raise
    except (GithubException, requests.RequestException) as e:
        task.fail(say.github_unreachable("the review issue", _github_reason(e)))
    except OSError as e:
        task.fail(f"The paper's files could not be written on the server: {e.strerror or e.__class__.__name__}.")


@contextlib.contextmanager
def _store_lock(task):
    """Hold the store's lock (crosswalk_steps.store_lock) while the store, the vocabulary or the site is written;
    another task waits its turn. A wait beyond crosswalk_steps.STORE_LOCK_WAIT fails the task with one sentence."""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(crosswalk_steps.store_lock())
        except TimeoutError:
            task.fail(say.store_busy(myst_cdn.duration(crosswalk_steps.STORE_LOCK_WAIT)))
        yield


def _load_paper(task, paper, run):
    """Load the paper from the local CDN into the store, so the store has what the issue has now (the data
    archive DOI minted after a draft among it). Call it with the store lock held."""
    sentence = crosswalk_steps.load_paper(paper.doi, Path(CDN_CURRENT_PATH), Path(GRAPH_DB_PATH), run)
    if sentence:
        task.fail(say.not_loaded(sentence))


def _published_clone(task, paper):
    """Clone the paper's repository at the commit published on the preview server into REPOS_PATH/<stem>, only to
    read it; fail with a sentence if it cannot be. A paper published before the preview server kept a record of its
    build has none there, and is cloned at the commit its deposit's repository archive was made from. Nothing on the
    server changes before the clone has worked."""
    fork_name = f"{GH_ORGANIZATION}/{task.repo_name}"
    try:
        commit = _published_commit(GH_ORGANIZATION, task.repo_name)
    except requests.RequestException as e:
        task.fail(f"The published commit of {fork_name} could not be read from the preview server: "
                  f"{e.__class__.__name__}.")
    if not commit:
        commit = crosswalk_steps.archived_commit(paper.doi)
    if not commit:
        task.fail(say.NO_PUBLISHED_BUILD)
    target = os.path.join(REPOS_PATH, paper.stem)
    shutil.rmtree(target, ignore_errors=True)
    try:
        gh_clone_repository(f"https://github.com/{fork_name}", target, depth=None, commit=commit,
                            timeout=crosswalk_steps.CLONE_TIMEOUT)
    except Exception as e:
        lines = [line.strip() for line in str(e).strip().split("\n") if line.strip()]
        why = say.end(lines[-1] if lines else e.__class__.__name__)
        task.fail(f"The repository {fork_name} could not be cloned at {commit[:7]}: {why}.")
    return target


def _issue_author(task):
    for tag in ("author", "author-handle"):
        login = gh_read_from_issue_body(task.screening.github_client, task.screening.review_repository,
                                        task.screening.issue_id, tag)
        if login:
            return login.strip().lstrip("@")
    return None


# NeuroLibre's editors team, as RoboNeuro names it (roboneuro config: teams: editors). Its members may run the
# crosswalk commands on any paper, and so may confirm on any paper as an issue's editor does.
EDITORS_ORGANIZATION, EDITORS_TEAM = "neurolibre", 3408147


def _neurolibre_editor(task, login) -> bool:
    """Whether login is a member of NeuroLibre's editors team. Anything that stops GitHub from saying so counts as
    no, so a confirmation is refused rather than recorded under someone it should not be."""
    try:
        client = task.screening.github_client
        team = client.get_organization(EDITORS_ORGANIZATION).get_team(EDITORS_TEAM)
        return bool(team.has_in_members(client.get_user(login)))
    except Exception:                                  # a missing token scope, GitHub unreachable, an unknown login
        logging.exception(f"Whether @{login} is in NeuroLibre's editors team could not be read")
        return False


def _may_confirm(task, login) -> bool:
    """The paper's author, the issue's editor, or a member of NeuroLibre's editors team: the command's sender when
    RoboNeuro found them in it (it checks with a token that can see the team, which the server's may not), else as
    GitHub tells the server."""
    names = {name.lower() for name in (_issue_author(task), _issue_editor(task)) if name}
    vouched = str(getattr(task.screening, "editor", None) or "").lower()
    return login.lower() in names or (vouched and login.lower() == vouched) or _neurolibre_editor(task, login)


def _issue_editor(task):
    login = gh_read_from_issue_body(task.screening.github_client, task.screening.review_repository,
                                    task.screening.issue_id, "editor")
    return login.strip().lstrip("@") if login else None


def _yaml_block(body):
    found = YAML_BLOCK.findall((body or "").replace("\r\n", "\n"))
    return found[-1][1] if found else None


def _corrections(task):
    """The corrections a command brings: (login, yaml text or None, comment id) from the comment `at` names when
    given, else from the command's own comment; (None, None, None) when the payload names no comment. A comment
    without a yaml block means the crosswalk as it stands. Fails with a sentence when the comment cannot be
    read, or when its yaml block is not valid YAML."""
    s = task.screening
    at, own = getattr(s, "at", None), getattr(s, "command_comment_id", None)
    source = at if at is not None else own
    if source is None:
        return None, None, None
    try:
        login, body = gh_get_issue_comment(s.github_client, s.review_repository, s.issue_id, source)
    except UnknownObjectException:
        task.fail(say.comment_not_found(source))
    except (GithubException, requests.RequestException) as e:
        task.fail(say.github_unreachable(f"comment {source}", _github_reason(e)))
    block = _yaml_block(body)
    if block is not None:
        try:
            yaml.safe_load(block)
        except yaml.YAMLError as e:
            mark, problem = getattr(e, "problem_mark", None), getattr(e, "problem", None)
            where = f"line {mark.line + 1}, column {mark.column + 1}" if mark is not None else "it is not valid YAML"
            task.fail(say.unreadable_block(source, f"{where}: {problem}" if problem and mark is not None else where))
    return login, block, source


def _write_submission(paper, text):
    """The yaml block as a file in the work folder, or None when there is no block."""
    if text is None:
        return None
    path = os.path.join(paper.work, "submission.yaml")
    with open(path, "w") as f:
        f.write(text)
    return Path(path)


def draft(task):
    task.start("Drafting the crosswalk of the paper's data")
    paper = _crosswalk_paper(task)
    if os.path.isfile(os.path.join(CROSSWALKS_PATH, paper.crosswalk_name)):
        task.fail(say.ALREADY_CONFIRMED)
    backends = _crosswalk_backends(task)
    replaced = os.path.isfile(os.path.join(paper.work, paper.crosswalk_name))
    repo_dir = _published_clone(task, paper)          # the earlier draft stays until crosswalk_steps.draft replaces it
    run = crosswalk_steps.subprocess_runner(Path(paper.work))
    with _store_lock(task):
        _load_paper(task, paper, run)

    result = crosswalk_steps.draft(paper.doi, Path(paper.data_dir), Path(repo_dir), Path(paper.work), backends,
                              Path(LLM_LOCK_PATH), run)
    if not (result.crosswalk.exists() and result.profiles.exists()) or (result.problems and not result.summary):
        task.fail(say.not_drafted(result.problems))
    u = result.usage
    logging.info(f"The data of {paper.doi} was drafted by {result.drafted_by}"
                 + (f" ({result.model})" if result.model else "")
                 + (f", because {result.reason}" if result.reason else "")
                 + (f", in {u.get('turns', 0)} turns: {u.get('prompt_tokens', 0)} input tokens, "
                    f"{u.get('cached_tokens', 0)} of them from the cache, and {u.get('answer_tokens', 0)} output "
                    f"tokens (the turns are in {paper.stem}.explore.log of the work folder)" if u else ""))

    with _store_lock(task):
        linked = crosswalk_steps.link_terms(paper.doi, Path(GRAPH_DB_PATH), backends["terms"], run)
    terms = say.terms_sentence(linked.linked, linked.labels, linked.failure)
    text = result.crosswalk.read_text()

    def comment(inside):
        return say.draft_comment(result.summary, terms, say.example(text), inside, result.problems,
                                 result.warnings, replaced=replaced, check_failure=result.check_failure,
                                 shared=result.shared)
    message = comment(say.draft_block(text))
    if len(message) > MAX_DRAFT_IN_COMMENT:
        message = comment(say.too_long(paper.crosswalk_name))
    task.succeed(message, collapsable=False)
    return f"Drafted the crosswalk of {paper.doi} with {result.drafted_by}"


def check(task):
    task.start("Checking the crosswalk of the paper's data")
    login, block, source = _corrections(task)         # anyone's block may be checked
    paper = _crosswalk_paper(task)
    crosswalk, profiles = _server_crosswalk(task, paper)
    submission = _write_submission(paper, block)
    result = crosswalk_steps.check(Path(crosswalk), Path(profiles), Path(paper.data_dir), db=Path(GRAPH_DB_PATH),
                              submission=submission)
    corrections = source if submission else None
    if result.failure:
        task.succeed(say.check_not_run_comment(result.failure, getattr(task.screening, "at", None)),
                     collapsable=False)
        return f"The crosswalk of {paper.doi} could not be checked"
    if result.unreadable:
        task.succeed(say.check_unreadable(corrections, result.unreadable), collapsable=False)
        return f"The crosswalk of {paper.doi} could not be read"
    at = getattr(task.screening, "at", None)
    confirm_at = source if (submission or at is not None) else None
    refused = None
    if confirm_at is not None and login:
        # A confirmation at that comment is recorded under its author, who must be one who may confirm.
        if not _may_confirm(task, login):
            confirm_at, refused = None, login
    task.succeed(say.check_comment(corrections, result.errors, result.warnings, confirm_at, result.notes,
                                   not_confirmable_by=refused), collapsable=False)
    return f"Checked the crosswalk of {paper.doi}"


def confirm(task):
    task.start("Confirming the crosswalk of the paper's data")
    screening = task.screening
    archive = gh_read_from_issue_body(screening.github_client, screening.review_repository, screening.issue_id, "data-archive")
    if not archive or archive.strip().upper() == "N/A":
        task.fail(say.NO_ARCHIVE_DOI)
    fields = [f.strip() for f in (getattr(screening, "fields", None) or "").split(",") if f.strip()] or None
    by, block, source = _corrections(task)
    if not by:
        task.fail(say.NO_COMMAND_COMMENT)
    if not _may_confirm(task, by):
        task.fail(say.not_allowed(_issue_author(task), _issue_editor(task), source, by, corrections=block is not None))
    paper = _crosswalk_paper(task)
    crosswalk, profiles = _server_crosswalk(task, paper)
    submission = _write_submission(paper, block)
    with _store_lock(task):
        # The store gets the paper as it is now, the data archive DOI minted after the draft among it.
        _load_paper(task, paper, crosswalk_steps.subprocess_runner(Path(paper.work)))
        result = crosswalk_steps.confirm(Path(crosswalk), Path(profiles), by, fields, submission,
                                    Path(paper.data_dir), Path(EVIDENTRON_SITE_PATH), Path(GRAPH_DB_PATH),
                                    record=Path(CROSSWALKS_PATH))
        reply = source if (submission or getattr(screening, "at", None) is not None) else None
        if result.errors and result.confirmed:
            task.fail(say.confirmed_not_published(result.version, result.errors, again_at=reply))
        if result.errors:
            task.fail(say.not_confirmed(result.errors))
        try:
            report = myst_cdn.rebuild()
            note = " ".join(report.get("notes", [])) or None
            # Every walk shows its paper's kin and data from the CDN, so all of them are woven again with this one
            import sakiori_tasks
            note = " ".join(x for x in (note, sakiori_tasks.queue_collection()) if x)
        except Exception as e:
            logging.exception("MyST CDN rebuild after a confirmation failed")
            note = say.cdn_not_rebuilt(str(e) if isinstance(e, TimeoutError) else e.__class__.__name__)
    task.succeed(say.confirm_comment(by, reply, result.version, result.addresses,
                                     say.term_links(result.terms, result.terms_url), note,
                                     proposed=say.proposed_terms(block, result.terms)), collapsable=False)
    return f"Confirmed the crosswalk of {paper.doi}"


# The tasks, named as in the celery module. Their soft time limits are the sums of the steps they run
# at their own limits (crosswalk_steps.DRAFT_SECONDS, CHECK_SECONDS, CONFIRM_SECONDS).

@celery_app.task(bind=True, name="neurolibre_celery_tasks.crosswalk_draft_task",
                 soft_time_limit=crosswalk_steps.DRAFT_SECONDS, time_limit=crosswalk_steps.DRAFT_SECONDS + HARD_MARGIN)
@handle_soft_timeout
def crosswalk_draft_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return draft(task)


@celery_app.task(bind=True, name="neurolibre_celery_tasks.crosswalk_check_task",
                 soft_time_limit=crosswalk_steps.CHECK_SECONDS, time_limit=crosswalk_steps.CHECK_SECONDS + HARD_MARGIN)
@handle_soft_timeout
def crosswalk_check_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return check(task)


@celery_app.task(bind=True, name="neurolibre_celery_tasks.crosswalk_confirm_task",
                 soft_time_limit=crosswalk_steps.CONFIRM_SECONDS,
                 time_limit=crosswalk_steps.CONFIRM_SECONDS + HARD_MARGIN)
@handle_soft_timeout
def crosswalk_confirm_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return confirm(task)
