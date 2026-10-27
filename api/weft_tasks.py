"""The description of a paper's data: draft, check and confirm, as celery tasks that report on the review issue.

The steps themselves are in weft_steps and the texts in weft_messages; the tasks add the places on this server and
the comment. The server is the place of record for the descriptions: the only thing these tasks write on GitHub is
the comment on the review issue, and the paper's repository is only read, cloned at its published commit.

The celery module imports this one at its end, which registers the tasks on its app under their old names
(neurolibre_celery_tasks.weft_*_task), so a message queued before the move still reaches its task.
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
from celery.exceptions import Ignore
from github import GithubException, UnknownObjectException

import myst_cdn
import weft_messages as say
import weft_steps
from github_client import gh_clone_repository, gh_get_issue_comment, gh_get_project_name, gh_read_from_issue_body
from neurolibre_celery_tasks import (BaseNeuroLibreTask, DATA_NFS_PATH, DOI_PREFIX, DOI_SUFFIX, GH_ORGANIZATION,
                                     _published_commit, celery_app, handle_soft_timeout)

# The places on the server are weft_steps' (EVIDENTRON_PATH, WEFTS_PATH, GRAPH_DB_PATH, SITE_PATH, the store's lock).
EVIDENTRON_PATH = str(weft_steps.EVIDENTRON_PATH)
GRAPH_DB_PATH = str(weft_steps.GRAPH_DB_PATH)
WEFTS_PATH = str(weft_steps.WEFTS_PATH)
REPOS_PATH = os.path.join(EVIDENTRON_PATH, "repos")
LLM_LOCK_PATH = os.path.join(EVIDENTRON_PATH, "llm.lock")
EVIDENTRON_CONFIG = os.path.join(EVIDENTRON_PATH, "evidentron.yaml")
EVIDENTRON_SITE_PATH = str(weft_steps.SITE_PATH)
CDN_CURRENT_PATH = str(myst_cdn.CDN_CURRENT)
HARD_MARGIN = 600               # the hard time limit of a task: its soft limit, then this long to say why it stopped
BACKEND_KEYS = ("draft", "redraft", "terms")

# A fenced block whose info string is yaml or yml, closed by a fence as long as its opening one; the last one
# in a comment is its corrections.
YAML_BLOCK = re.compile(r"^[ \t]*(`{3,})[ \t]*(?:yaml|yml)[ \t]*\n(.*?)^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL | re.IGNORECASE)
MAX_DRAFT_IN_COMMENT = 60000


def _weft_backends(task):
    """The backends for drafting, redrafting and linking terms, from `weft: backend:` in the server's Evidentron
    config; a key it lacks fails the task with one sentence."""
    try:
        with open(EVIDENTRON_CONFIG) as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    weft = config.get("weft") if isinstance(config, dict) else None
    found = weft.get("backend") if isinstance(weft, dict) else None
    found = found if isinstance(found, dict) else {}
    for key in BACKEND_KEYS:
        if not found.get(key):
            task.fail(say.no_backend(key))
    return {key: str(found[key]) for key in BACKEND_KEYS}


def _weft_paper(task):
    """What every task needs: the paper's DOI, its stem, the data folder, the work folder and the file names."""
    issue_id = task.screening.issue_id
    doi = f"{DOI_PREFIX}/{DOI_SUFFIX}.{issue_id:05d}"
    stem = weft_steps._stem(doi)
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
    work = os.path.join(WEFTS_PATH, stem)
    os.makedirs(work, exist_ok=True)
    return SimpleNamespace(doi=doi, stem=stem, data_dir=data_dir, work=work,
                           weft_name=f"{stem}.weft.yaml", profiles_name=f"{stem}.profiles.json")


def _server_weft(task, paper):
    """The weft and its profiles from the server, in the work folder: a copy of the flat confirmed weft and its
    profiles when there is one (so a failed step never touches the confirmed copy), else the draft. Fails with a
    sentence when the weft or its profiles are missing."""
    weft = os.path.join(paper.work, paper.weft_name)
    profiles = os.path.join(paper.work, paper.profiles_name)
    confirmed = os.path.join(WEFTS_PATH, paper.weft_name)
    if os.path.isfile(confirmed):
        confirmed_profiles = os.path.join(WEFTS_PATH, paper.profiles_name)
        if not os.path.isfile(confirmed_profiles):
            task.fail(say.CONFIRMED_WITHOUT_PROFILES)
        shutil.copyfile(confirmed, weft)
        shutil.copyfile(confirmed_profiles, profiles)
    if not (os.path.isfile(weft) and os.path.isfile(profiles)):
        task.fail(say.NO_DRAFT)
    return weft, profiles


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
    except Ignore:
        raise
    except (GithubException, requests.RequestException) as e:
        task.fail(say.github_unreachable("the review issue", _github_reason(e)))
    except OSError as e:
        task.fail(f"The paper's files could not be written on the server: {e.strerror or e.__class__.__name__}.")


@contextlib.contextmanager
def _store_lock(task):
    """Hold the store's lock (weft_steps.store_lock) while the store, the vocabulary or the site is written; another
    task waits its turn. A wait beyond weft_steps.STORE_LOCK_WAIT fails the task with one sentence."""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(weft_steps.store_lock())
        except TimeoutError:
            task.fail(say.store_busy(myst_cdn.duration(weft_steps.STORE_LOCK_WAIT)))
        yield


def _load_paper(task, paper, run):
    """Load the paper from the local CDN into the store, so the store has what the issue has now (the data
    archive DOI minted after a draft among it). Call it with the store lock held."""
    sentence = weft_steps.load_paper(paper.doi, Path(CDN_CURRENT_PATH), Path(GRAPH_DB_PATH), run)
    if sentence:
        task.fail(say.not_loaded(sentence))


def _published_clone(task, paper):
    """Clone the paper's repository at the commit published on the preview server into REPOS_PATH/<stem>, only to
    read it; fail with a sentence if it cannot be. Nothing on the server changes before the clone has worked."""
    fork_name = f"{GH_ORGANIZATION}/{task.repo_name}"
    try:
        commit = _published_commit(GH_ORGANIZATION, task.repo_name)
    except requests.RequestException as e:
        task.fail(f"The published commit of {fork_name} could not be read from the preview server: "
                  f"{e.__class__.__name__}.")
    if not commit:
        task.fail(say.NO_PUBLISHED_BUILD)
    target = os.path.join(REPOS_PATH, paper.stem)
    shutil.rmtree(target, ignore_errors=True)
    try:
        gh_clone_repository(f"https://github.com/{fork_name}", target, depth=None, commit=commit,
                            timeout=weft_steps.CLONE_TIMEOUT)
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
    without a yaml block means the description as it stands. Fails with a sentence when the comment cannot be
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
    task.start("Describing the paper's data")
    paper = _weft_paper(task)
    if os.path.isfile(os.path.join(WEFTS_PATH, paper.weft_name)):
        task.fail(say.ALREADY_CONFIRMED)
    backends = _weft_backends(task)
    replaced = os.path.isfile(os.path.join(paper.work, paper.weft_name))
    repo_dir = _published_clone(task, paper)          # the earlier draft stays until weft_steps.draft replaces it
    run = weft_steps.subprocess_runner(Path(paper.work))
    with _store_lock(task):
        _load_paper(task, paper, run)

    result = weft_steps.draft(paper.doi, Path(paper.data_dir), Path(repo_dir), Path(paper.work), backends,
                              Path(LLM_LOCK_PATH), run)
    if not (result.weft.exists() and result.profiles.exists()) or (result.problems and not result.summary):
        task.fail(say.not_drafted(result.problems))
    logging.info(f"The data of {paper.doi} was drafted by {result.drafted_by}"
                 + (f" ({result.model})" if result.model else "")
                 + (f", because {result.reason}" if result.reason else ""))

    with _store_lock(task):
        linked = weft_steps.link_terms(paper.doi, Path(GRAPH_DB_PATH), backends["terms"], run)
    terms = say.terms_sentence(linked.linked, linked.labels, linked.failure)
    text = result.weft.read_text()

    def comment(inside):
        return say.draft_comment(result.summary, terms, say.example(text), inside, result.problems,
                                 result.warnings, replaced=replaced, check_failure=result.check_failure)
    message = comment(say.draft_block(text))
    if len(message) > MAX_DRAFT_IN_COMMENT:
        message = comment(say.too_long(paper.weft_name))
    task.succeed(message, collapsable=False)
    return f"Drafted the description of the data of {paper.doi} with {result.drafted_by}"


def check(task):
    task.start("Checking the description of the paper's data")
    login, block, source = _corrections(task)         # anyone's block may be checked
    paper = _weft_paper(task)
    weft, profiles = _server_weft(task, paper)
    submission = _write_submission(paper, block)
    result = weft_steps.check(Path(weft), Path(profiles), Path(paper.data_dir), db=Path(GRAPH_DB_PATH),
                              submission=submission)
    corrections = source if submission else None
    if result.failure:
        task.succeed(say.check_unreadable(corrections, say.check_not_run(result.failure)), collapsable=False)
        return f"The description of the data of {paper.doi} could not be checked"
    if result.unreadable:
        task.succeed(say.check_unreadable(corrections, result.unreadable), collapsable=False)
        return f"The description of the data of {paper.doi} could not be read"
    at = getattr(task.screening, "at", None)
    confirm_at = source if (submission or at is not None) else None
    refused = None
    if confirm_at is not None and login:
        # A confirmation at that comment is recorded under its author, who must be the paper's author or the editor.
        if login.lower() not in {name.lower() for name in (_issue_author(task), _issue_editor(task)) if name}:
            confirm_at, refused = None, login
    task.succeed(say.check_comment(corrections, result.errors, result.warnings, confirm_at, result.notes,
                                   not_confirmable_by=refused), collapsable=False)
    return f"Checked the description of the data of {paper.doi}"


def confirm(task):
    task.start("Confirming the description of the paper's data")
    screening = task.screening
    archive = gh_read_from_issue_body(screening.github_client, screening.review_repository, screening.issue_id, "data-archive")
    if not archive or archive.strip().upper() == "N/A":
        task.fail(say.NO_ARCHIVE_DOI)
    fields = [f.strip() for f in (getattr(screening, "fields", None) or "").split(",") if f.strip()] or None
    by, block, source = _corrections(task)
    if not by:
        task.fail(say.NO_COMMAND_COMMENT)
    author, editor = _issue_author(task), _issue_editor(task)
    if by.lower() not in {name.lower() for name in (author, editor) if name}:
        task.fail(say.not_allowed(author, editor, source, by, corrections=block is not None))
    paper = _weft_paper(task)
    weft, profiles = _server_weft(task, paper)
    submission = _write_submission(paper, block)
    with _store_lock(task):
        # The store gets the paper as it is now, the data archive DOI minted after the draft among it.
        _load_paper(task, paper, weft_steps.subprocess_runner(Path(paper.work)))
        result = weft_steps.confirm(Path(weft), Path(profiles), by, fields, submission,
                                    Path(paper.data_dir), Path(EVIDENTRON_SITE_PATH), Path(GRAPH_DB_PATH),
                                    record=Path(WEFTS_PATH))
        reply = source if (submission or getattr(screening, "at", None) is not None) else None
        if result.errors and result.confirmed:
            task.fail(say.confirmed_not_published(result.version, result.errors, again_at=reply))
        if result.errors:
            task.fail(say.not_confirmed(result.errors))
        try:
            report = myst_cdn.rebuild()
            note = " ".join(report.get("notes", [])) or None
        except Exception as e:
            logging.exception("MyST CDN rebuild after a confirmation failed")
            note = say.cdn_not_rebuilt(str(e) if isinstance(e, TimeoutError) else e.__class__.__name__)
    task.succeed(say.confirm_comment(by, reply, result.version, result.addresses,
                                     say.term_links(result.terms, result.terms_url), note,
                                     proposed=say.proposed_terms(block, result.terms)), collapsable=False)
    return f"Confirmed the description of the data of {paper.doi}"


# The tasks, under the names they had in the celery module. Their soft time limits are the sums of the steps they run
# at their own limits (weft_steps.DRAFT_SECONDS, CONFIRM_SECONDS); the check runs one step.

@celery_app.task(bind=True, name="neurolibre_celery_tasks.weft_draft_task", soft_time_limit=weft_steps.DRAFT_SECONDS,
                 time_limit=weft_steps.DRAFT_SECONDS + HARD_MARGIN)
@handle_soft_timeout
def weft_draft_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return draft(task)


@celery_app.task(bind=True, name="neurolibre_celery_tasks.weft_check_task", soft_time_limit=5000, time_limit=6000)
@handle_soft_timeout
def weft_check_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return check(task)


@celery_app.task(bind=True, name="neurolibre_celery_tasks.weft_confirm_task",
                 soft_time_limit=weft_steps.CONFIRM_SECONDS, time_limit=weft_steps.CONFIRM_SECONDS + HARD_MARGIN)
@handle_soft_timeout
def weft_confirm_task(self, screening_dict):
    task = BaseNeuroLibreTask(self, screening_dict)
    with _errors_as_sentences(task):
        return confirm(task)
