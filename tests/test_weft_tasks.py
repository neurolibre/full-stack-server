"""The three weft tasks, with weft_steps, the GitHub client and the data folder faked: nothing touches the network."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

API = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API))
_here = os.getcwd()
os.chdir(API)                      # the modules read config/*.yaml relative to the api folder
try:
    from api import neurolibre_celery_tasks as tasks
    import screening_client
finally:
    os.chdir(_here)

from celery.exceptions import Ignore

DOI = "10.55458/neurolibre.00041"
REAL_ISDIR = os.path.isdir
SEEN = {}
FORK = "https://github.com/roboneurolibre/paper-repo"


class FakeTask:
    """Stands for BaseNeuroLibreTask: records what the task says on the issue."""
    last = None

    def __init__(self, celery_task, screening=None, payload=None):
        s = dict(screening)
        self.screening = SimpleNamespace(
            issue_id=s["issue_id"], review_repository="neurolibre/reviews",
            target_repo_url=s["target_repo_url"], github_client=FakeGitHub.current,
            fields=s.get("fields"), by=s.get("by"), submission=s.get("submission"))
        self.owner_name, self.repo_name = "someone", "paper-repo"
        self.started, self.comment, self.failed = [], None, None
        FakeTask.last = self

    def start(self, message=""):
        self.started.append(message)

    def succeed(self, message, collapsable=True, attachment_path=None):
        self.comment = message

    def fail(self, message, attachment_path=None):
        self.failed = message
        raise Ignore()


class FakeRepo:
    def __init__(self, files, collaborators, refuse_invite=False):
        self.files, self.collaborators, self.refuse_invite = files, collaborators, refuse_invite
        self.default_branch = "main"

    def get_contents(self, path):
        from github import UnknownObjectException
        if path not in self.files:
            raise UnknownObjectException(404, {"message": "Not Found"}, {})
        return SimpleNamespace(path=path, sha="sha")

    def add_to_collaborators(self, login, permission=None):
        if self.refuse_invite:
            raise RuntimeError("no rights")
        self.collaborators.append((login, permission))


class FakeGitHub:
    current = None

    def __init__(self, files=None, refuse_invite=False):
        self.files, self.collaborators = files if files is not None else {}, []
        self.large = set()             # files over 1 MB: the contents API gives no content for them
        self.writes = []
        self.refuse_invite = refuse_invite
        FakeGitHub.current = self

    def get_repo(self, name):
        return FakeRepo(self.files, self.collaborators, self.refuse_invite)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fake GitHub, a data folder that exists, a work root in tmp_path and recording steps."""
    gh = FakeGitHub()
    w = SimpleNamespace(gh=gh, calls=[], body={"data-archive": "10.5281/zenodo.1", "author": "ada"},
                        project="proj")
    monkeypatch.setattr(tasks, "BaseNeuroLibreTask", FakeTask)
    monkeypatch.setattr(tasks, "WEFTS_PATH", str(tmp_path / "wefts"))
    monkeypatch.setattr(tasks.os.path, "isdir", lambda p: p == "/DATA_NFS/proj" or REAL_ISDIR(p))
    monkeypatch.setattr(tasks, "gh_get_project_name", lambda client, url: w.project)
    monkeypatch.setattr(tasks, "gh_forkify_it", lambda url: FORK)
    monkeypatch.setattr(tasks, "gh_read_from_issue_body", lambda c, r, i, tag: w.body.get(tag))
    monkeypatch.setattr(tasks, "get_default_branch", lambda c, r: "main")

    def create(client, repo, path, content, commit_message=None, encoding="utf-8"):
        client.files[path] = content
        client.writes.append(("create", path, content))
        return {"status": True, "message": "Success"}

    def update(client, repo, path, content, message):
        client.files[path] = content
        client.writes.append(("update", path, content))
        return {"status": True, "message": "Success"}

    monkeypatch.setattr(tasks, "gh_create_file", create)
    monkeypatch.setattr(tasks, "gh_update_file_content", update)
    monkeypatch.setattr(tasks, "gh_get_file_content",
                        lambda c, repo, path: None if path in c.large else c.files.get(path))

    def invite(client, repo, login):
        try:
            client.get_repo(repo).add_to_collaborators(login, permission="push")
            return True
        except Exception:
            return False
    monkeypatch.setattr(tasks, "gh_invite_collaborator", invite)

    def runner(work):
        def run(argv):
            w.calls.append(argv)
            return w.load_result
        return run
    w.load_result = (0, "loaded")
    w.clones, w.commit = [], "abc1234def"
    monkeypatch.setattr(tasks, "REPOS_PATH", str(tmp_path / "repos"))
    monkeypatch.setattr(tasks.requests, "get", lambda url: SimpleNamespace(
        status_code=200 if w.commit else 404, text=(w.commit or "") + "\n"))
    monkeypatch.setattr(tasks, "gh_clone_repository", lambda url, path, depth=1, commit=None:
                        w.clones.append((url, commit)) or w.clone_error and (_ for _ in ()).throw(RuntimeError(w.clone_error)))
    w.clone_error = None
    monkeypatch.setattr(tasks.weft_steps, "subprocess_runner", runner)
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda doi, db, backend, run=None: (7, None))
    monkeypatch.setattr(tasks, "STORE_LOCK_PATH", str(tmp_path / "store.lock"))
    w.rebuilds = []
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", lambda: w.rebuilds.append(True) or {"projects": 3, "clashes": []})
    return w


def payload(**extra):
    return {"task_name": "x", "issue_id": 41, "target_repo_url": "https://github.com/someone/paper-repo", **extra}


def fake_draft(monkeypatch, problems=None, reason=None, drafted_by="openai-compatible", write=True):
    def draft(doi, data_dir, repo_dir, work, backends, lock, run=None):
        SEEN['repo_dir'] = str(repo_dir)
        weft, profiles = Path(work) / "00041.weft.yaml", Path(work) / "00041.profiles.json"
        if write:
            weft.write_text("paper: " + doi + "\n")
            profiles.write_text("{}")
        return tasks.weft_steps.DraftResult(weft=weft, profiles=profiles, drafted_by=drafted_by, reason=reason,
                                            problems=problems or [], warnings=["a unit is guessed"],
                                            summary=["p.tsv: 3 columns", "proposed new term foo: Foo"])
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)


def test_draft_comment_files_and_invite(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    t = FakeTask.last
    assert "p.tsv: 3 columns" in t.comment
    assert "proposed new term foo: Foo" in t.comment
    assert "a unit is guessed" in t.comment
    assert f"{FORK}/edit/main/weft/00041.weft.yaml" in t.comment
    assert "7 terms" in t.comment
    assert "@roboneuro weft check" in t.comment and "@roboneuro weft confirm" in t.comment
    assert [w[:2] for w in world.gh.writes] == [("create", "weft/00041.weft.yaml"), ("create", "weft/00041.profiles.json")]
    assert world.gh.collaborators == [("ada", "push")]
    assert "—" not in t.comment
    # the paper is loaded into the store first
    assert world.calls[0][3:] == ["/DATA/cdn/current", "--doi", DOI, "--db", "/DATA/evidentron/graph.db"]
    assert "drafted by the self-hosted model" in t.comment
    assert world.clones == [(f"https://github.com/roboneurolibre/paper-repo", "abc1234def")]
    assert SEEN['repo_dir'].endswith("/repos/00041")


def test_second_draft_updates_and_says_replaced(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert "replaced" not in FakeTask.last.comment
    tasks.weft_draft_task.run(payload())
    assert [w[0] for w in world.gh.writes[2:]] == ["update", "update"]
    assert "replaced the earlier draft" in FakeTask.last.comment


def test_redraft_reason_and_problems_are_said(world, monkeypatch):
    fake_draft(monkeypatch, problems=["column x has no term"], reason="The first draft was not valid",
               drafted_by="anthropic-api")
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert "drafted by Claude because the first draft was not valid" in c and "column x has no term" in c


def test_invite_failure_is_a_sentence(world, monkeypatch):
    world.gh.refuse_invite = True
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert "could not be invited" in FakeTask.last.comment
    assert "ada" in FakeTask.last.comment


def test_missing_data_folder_fails(world, monkeypatch):
    world.project = "gone"
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == f"The data of {DOI} is not at /DATA_NFS/gone, so there is nothing to describe."


def test_load_failure_is_one_sentence(world, monkeypatch):
    world.load_result = (1, "Traceback (most recent call last):\n  File x\nValueError: no such site")
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "Traceback" not in FakeTask.last.failed and "\n" not in FakeTask.last.failed
    assert "no such site" in FakeTask.last.failed


def test_draft_that_wrote_nothing_fails_with_the_problem(world, monkeypatch):
    fake_draft(monkeypatch, problems=["The data could not be profiled: bad"], write=False)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "could not be profiled" in FakeTask.last.failed
    assert world.gh.writes == []


def test_backends_default_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(tmp_path / "none.yaml"))
    assert tasks._weft_backends() == {"draft": "openai-compatible", "redraft": "anthropic-api", "terms": "anthropic-api"}
    f = tmp_path / "e.yaml"
    f.write_text("weft:\n  backend:\n    draft: a\n    redraft: b\n    terms: c\n")
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(f))
    assert tasks._weft_backends() == {"draft": "a", "redraft": "b", "terms": "c"}


def seed_fork(world):
    world.gh.files["weft/00041.weft.yaml"] = "paper: x\n"
    world.gh.files["weft/00041.profiles.json"] = "{}"


def test_check_unreadable(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(unreadable="The weft is not valid YAML."))
    tasks.weft_check_task.run(payload())
    assert FakeTask.last.comment == "The weft is not valid YAML."


def test_check_errors_then_warnings(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(errors=["E1"], warnings=["W1"]))
    tasks.weft_check_task.run(payload())
    c = FakeTask.last.comment
    assert c.index("E1") < c.index("W1")


def test_check_clean_and_no_draft(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload())
    assert "no errors" in FakeTask.last.comment
    world.gh.files.clear()
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload())
    assert "weft draft" in FakeTask.last.failed


def test_confirm_refused_without_data_archive(world, monkeypatch):
    seed_fork(world)
    world.body["data-archive"] = None
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(by="ada", fields=""))
    assert "data archive" in FakeTask.last.failed


def test_confirm_with_errors_writes_nothing(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(errors=["bad field", "bad link"]))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(by="ada", fields=""))
    assert "bad field" in FakeTask.last.failed and "bad link" in FakeTask.last.failed
    assert world.gh.writes == []


def test_confirm_success(world, monkeypatch):
    seed_fork(world)
    seen = {}

    def confirm(weft, profiles, by, fields, submission, data_dir, site, db, run=None, record=None):
        seen.update(by=by, fields=fields, submission=submission, data_dir=str(data_dir), site=str(site), db=str(db),
                    record=str(record), profiles=str(profiles))
        Path(weft).write_text("paper: x\nversion: 1.0.0\n")
        return tasks.weft_steps.ConfirmResult(version="1.0.0", addresses=tasks.weft_steps.addresses_of(DOI, ["age"]))
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)
    tasks.weft_confirm_task.run(payload(by="ada", fields="a, b", submission="files: []\n"))
    c = FakeTask.last.comment
    assert "1.0.0" in c
    for part in ("/wefts/%s/fair2.json" % DOI, "/wefts/%s/croissant.json" % DOI, "/dois/%s.json" % DOI, "/terms/age/"):
        assert part in c
    assert world.gh.writes == [("update", "weft/00041.weft.yaml", "paper: x\nversion: 1.0.0\n")]
    assert seen["fields"] == ["a", "b"] and seen["by"] == "ada"
    assert seen["data_dir"] == "/DATA_NFS/proj" and seen["site"] == "/DATA/evidentron/site"
    assert seen["db"] == "/DATA/evidentron/graph.db"
    assert Path(seen["submission"]).read_text() == "files: []\n"
    assert seen["record"] == tasks.WEFTS_PATH
    assert world.rebuilds == [True]


def test_confirm_without_by_or_submission(world, monkeypatch):
    seed_fork(world)
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(fields=""))
    assert "login" in FakeTask.last.failed
    seen = {}
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda weft, profiles, by, fields, submission, *a, **k:
                        seen.update(fields=fields, submission=submission) or tasks.weft_steps.ConfirmResult(version="1"))
    tasks.weft_confirm_task.run(payload(by="ada"))
    assert seen == {"fields": None, "submission": None}


def test_extra_payload_survives_to_dict_and_from_dict(monkeypatch):
    class G:
        def __init__(self, *a, **k): pass
    monkeypatch.setattr(screening_client, "Github", G)
    monkeypatch.setattr(screening_client.ScreeningClient, "_init_responder", lambda self: None)
    client = screening_client.ScreeningClient("t", issue_id=3, fields="a, b", by="ada", submission="x: 1\n")
    d = client.to_dict()
    assert (d["fields"], d["by"], d["submission"]) == ("a, b", "ada", "x: 1\n")
    again = screening_client.ScreeningClient.from_dict(d)
    assert (again.fields, again.by, again.submission) == ("a, b", "ada", "x: 1\n")


def test_failed_second_draft_does_not_reuse_old_files(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    writes = len(world.gh.writes)

    def failing(doi, data_dir, repo_dir, work, backends, lock, run=None):
        return tasks.weft_steps.DraftResult(weft=Path(work) / "00041.weft.yaml", profiles=Path(work) / "00041.profiles.json",
                                            drafted_by="anthropic-api", reason="x", problems=["The data could not be profiled: bad"])
    monkeypatch.setattr(tasks.weft_steps, "draft", failing)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "could not be profiled" in FakeTask.last.failed
    assert len(world.gh.writes) == writes


def test_no_published_commit_means_no_draft(world, monkeypatch):
    world.commit = None
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "no published commit" in FakeTask.last.failed.lower() or "There is no published commit" in FakeTask.last.failed
    assert world.gh.writes == [] and world.clones == []


def test_clone_failure_is_one_sentence(world, monkeypatch):
    world.clone_error = "fatal: reference is not a tree\nmore"
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "could not be cloned" in FakeTask.last.failed and "\n" not in FakeTask.last.failed


def test_github_errors_become_a_sentence(world, monkeypatch):
    from github import GithubException
    seed_fork(world)

    def boom(*a, **k):
        raise GithubException(404, {"message": "Not Found", "documentation_url": "x"}, {})
    monkeypatch.setattr(tasks, "gh_get_file_content", boom)
    for task in (tasks.weft_check_task, tasks.weft_confirm_task):
        with pytest.raises(Ignore):
            task.run(payload(by="ada"))
        f = FakeTask.last.failed
        assert f == "The fork paper-repo could not be reached on GitHub: 404 Not Found"


def test_a_failed_term_linking_is_said_not_counted(world, monkeypatch):
    fake_draft(monkeypatch)
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda doi, db, backend, run=None: (None, "The model's answer was not JSON"))
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert "could not be linked to the vocabulary's terms: The model's answer was not JSON." in c
    assert "linked to 0 terms" not in c and "None" not in c


# C1: a paper sync rebuilds the CDN with Evidentron's site, through add_to_myst_cdn's real path.

def _site_build(folder):
    import json
    (folder / "content").mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"version": 2, "projects": [{"title": "t", "index": "index"}]}))
    (folder / "content" / "index.json").write_text(json.dumps({"slug": "index", "mdast": {}}))
    return folder


def _sync(tmp_path, monkeypatch, extras):
    data = tmp_path / "data"
    monkeypatch.setattr(tasks, "DATA_ROOT_PATH", str(data))
    _site_build(data / tasks.MYST_FOLDER / tasks.GH_ORGANIZATION / "paper-repo" / "abc1234def" / "_build" / "site")
    monkeypatch.setattr(tasks.myst_cdn, "CDN_ROOT", tmp_path / "cdn")
    monkeypatch.setattr(tasks.myst_cdn, "EVIDENTRON_SITE", extras)
    monkeypatch.setattr(tasks.requests, "get", lambda url: SimpleNamespace(status_code=200, text="abc1234def\n"))

    class Rsync:
        def __init__(self, *a, **k): pass
        def communicate(self): return (b"", None)
        def wait(self): return 0
    monkeypatch.setattr(tasks.subprocess, "Popen", Rsync)
    return tasks.add_to_myst_cdn(SimpleNamespace(repo_name="paper-repo"), "neurolibre.00041", "v1")


def test_a_paper_sync_carries_evidentrons_site(tmp_path, monkeypatch):
    extras = tmp_path / "evidentron-site"
    (extras / "terms").mkdir(parents=True)
    (extras / "terms" / "index.html").write_text("terms")
    (extras / "index.json").write_text("{}")
    note = _sync(tmp_path, monkeypatch, extras)
    assert "Added to the MyST CDN (1 papers)" in note
    assert (tmp_path / "cdn" / "current" / "terms" / "index.html").read_text() == "terms"
    assert (tmp_path / "cdn" / "current" / "index.json").exists()


def test_a_paper_sync_without_evidentrons_site_says_so(tmp_path, monkeypatch):
    note = _sync(tmp_path, monkeypatch, tmp_path / "nowhere")
    assert "Evidentron's site was not found, so the CDN carries no terms or exports yet." in note
    assert str(tmp_path) not in note



# R4: the data folder is under DATA_NFS_PATH from the config.

def test_the_data_folder_is_under_the_configured_nfs_path(world, monkeypatch):
    monkeypatch.setattr(tasks, "DATA_NFS_PATH", "/elsewhere")
    monkeypatch.setattr(tasks.os.path, "isdir", lambda p: p == "/elsewhere/proj" or REAL_ISDIR(p))
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed is None


# I3: profiles over 1 MB never come through the contents API.

def server_copy(world, tmp_path, where="work"):
    folder = tmp_path / "wefts" / ("00041" if where == "work" else "")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "00041.profiles.json").write_text('{"server": true}')
    world.gh.files["weft/00041.weft.yaml"] = "paper: x\n"
    world.gh.files["weft/00041.profiles.json"] = "big"
    world.gh.large.add("weft/00041.profiles.json")
    return folder / "00041.profiles.json"


@pytest.mark.parametrize("where", ["work", "flat"])
def test_check_reads_the_profiles_from_the_servers_copy(world, monkeypatch, tmp_path, where):
    server_copy(world, tmp_path, where)
    seen = {}
    monkeypatch.setattr(tasks.weft_steps, "check",
                        lambda weft, profiles, *a, **k: seen.update(p=Path(profiles).read_text()) or tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload())
    assert FakeTask.last.failed is None
    assert seen["p"] == '{"server": true}'


def test_confirm_reads_the_profiles_from_the_servers_copy(world, monkeypatch, tmp_path):
    server_copy(world, tmp_path)
    seen = {}

    def confirm(weft, profiles, *a, **k):
        seen.update(p=Path(profiles).read_text())
        return tasks.weft_steps.ConfirmResult(version="1.0.0", confirmed=True, addresses={})
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)
    tasks.weft_confirm_task.run(payload(by="ada"))
    assert seen["p"] == '{"server": true}'


def test_no_profiles_anywhere_is_a_sentence(world, tmp_path):
    world.gh.files["weft/00041.weft.yaml"] = "paper: x\n"
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload())
    assert "profiles" in FakeTask.last.failed and "weft draft" in FakeTask.last.failed
    assert str(tmp_path) not in FakeTask.last.failed


def test_a_second_draft_updates_profiles_over_1_mb(world, monkeypatch):
    fake_draft(monkeypatch)
    world.gh.files["weft/00041.profiles.json"] = "big"
    world.gh.large.add("weft/00041.profiles.json")
    tasks.weft_draft_task.run(payload())
    assert ("update", "weft/00041.profiles.json") in [w[:2] for w in world.gh.writes]


# I6: a draft never replaces confirmed work.

def test_a_draft_over_a_confirmed_weft_is_refused(world, monkeypatch):
    fake_draft(monkeypatch)
    world.gh.files["weft/00041.weft.yaml"] = "paper: x\nfiles:\n- file: a.tsv\n  fields:\n  - {name: a, status: confirmed}\n"
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    f = FakeTask.last.failed
    assert "confirmed" in f and "weft/00041.weft.yaml" in f and "\n" not in f
    assert world.gh.writes == [] and world.calls == []


def test_a_draft_over_an_unconfirmed_weft_goes_on(world, monkeypatch):
    fake_draft(monkeypatch)
    world.gh.files["weft/00041.weft.yaml"] = "paper: x\nfiles:\n- file: a.tsv\n  status: proposed\n"
    tasks.weft_draft_task.run(payload())
    assert "replaced the earlier draft" in FakeTask.last.comment


def test_a_draft_clears_the_old_self_hosted_draft(world, monkeypatch, tmp_path):
    old = tmp_path / "wefts" / "00041" / "00041.selfhosted.weft.yaml"
    old.parent.mkdir(parents=True)
    old.write_text("old")
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert not old.exists()


# R2 and I7: the store, the vocabulary and the site are written by one task at a time.

def _busy(path):
    import fcntl
    with open(path, "a") as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(other, fcntl.LOCK_UN)
    return False


def test_draft_holds_the_store_lock_for_the_load_and_the_linking(world, monkeypatch):
    held = {}

    def runner(work):
        return lambda argv: held.setdefault("load", _busy(tasks.STORE_LOCK_PATH)) and (0, "") or (0, "")
    monkeypatch.setattr(tasks.weft_steps, "subprocess_runner", runner)

    def draft(*a, **k):
        held["draft"] = _busy(tasks.STORE_LOCK_PATH)
        return fake_result(*a)
    fake_draft(monkeypatch)
    fake_result = tasks.weft_steps.draft
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)
    monkeypatch.setattr(tasks.weft_steps, "link_terms",
                        lambda *a, **k: held.setdefault("link", _busy(tasks.STORE_LOCK_PATH)) and (3, None) or (3, None))
    tasks.weft_draft_task.run(payload())
    assert held == {"load": True, "draft": False, "link": True}


def test_confirm_holds_the_store_lock_and_reloads_the_paper_first(world, monkeypatch):
    seed_fork(world)
    held = {}

    def confirm(*a, **k):
        held["confirm"] = _busy(tasks.STORE_LOCK_PATH)
        return tasks.weft_steps.ConfirmResult(version="1.0.0", confirmed=True, addresses={})
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", lambda: held.setdefault("rebuild", _busy(tasks.STORE_LOCK_PATH)) and {})
    tasks.weft_confirm_task.run(payload(by="ada"))
    assert held == {"confirm": True, "rebuild": True}
    assert world.calls[0][1:] == ["-m", "evidentron.graph", "/DATA/cdn/current", "--doi", DOI,
                                  "--db", "/DATA/evidentron/graph.db"]
    assert "--identifier" not in world.calls[0]


def test_a_store_kept_busy_is_one_sentence(world, monkeypatch):
    import fcntl
    seed_fork(world)
    monkeypatch.setattr(tasks, "STORE_LOCK_WAIT", 0.2)
    with open(tasks.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with pytest.raises(Ignore):
            tasks.weft_confirm_task.run(payload(by="ada"))
    f = FakeTask.last.failed
    assert "busy" in f and "\n" not in f and "store.lock" not in f


def test_a_failed_reload_at_confirm_is_one_sentence(world, monkeypatch):
    seed_fork(world)
    world.load_result = (1, "Traceback (most recent call last):\n  File x\nValueError: no such site")
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(by="ada"))
    assert "no such site" in FakeTask.last.failed and "Traceback" not in FakeTask.last.failed


# C3: the confirmed paper's files reach the served CDN.

def test_a_failed_cdn_rebuild_is_a_sentence_in_the_comment(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.0.0", confirmed=True, addresses=tasks.weft_steps.addresses_of(DOI, [])))

    def boom():
        raise RuntimeError("/DATA/cdn/current is a folder, not a symlink")
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", boom)
    tasks.weft_confirm_task.run(payload(by="ada"))
    c = FakeTask.last.comment
    assert "confirmed by ada at version 1.0.0" in c
    assert "The CDN could not be rebuilt" in c and "/DATA" not in c.replace("/DATA/cdn", "") 
    assert "/DATA/cdn/current" not in c


# Confirmed but not published, and a failed write-back.

def test_confirmed_but_not_published_says_so(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.2.0", confirmed=True, errors=["No weft of x is in the server's folder of wefts."]))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(by="ada"))
    f = FakeTask.last.failed
    assert "confirmed at version 1.2.0" in f and "not published" in f and "No weft of x" in f
    assert world.rebuilds == []


def test_a_failed_write_back_says_the_weft_is_confirmed_and_asking_again_is_safe(world, monkeypatch):
    seed_fork(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.0.0", confirmed=True, addresses={}))
    monkeypatch.setattr(tasks, "gh_update_file_content", lambda *a, **k: {"status": False, "message": "409 conflict"})
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(by="ada"))
    f = FakeTask.last.failed
    assert "already confirmed and published" in f and "@roboneuro weft confirm" in f and "safe" in f


# I8: a local file error is one sentence.

def test_a_local_file_error_is_one_sentence(world, monkeypatch):
    seed_fork(world)

    def full(*a, **k):
        raise OSError(28, "No space left on device", "/DATA/evidentron/wefts/00041")
    monkeypatch.setattr(tasks.os, "makedirs", full)
    for task in (tasks.weft_draft_task, tasks.weft_check_task, tasks.weft_confirm_task):
        fake_draft(monkeypatch)
        with pytest.raises(Ignore):
            task.run(payload(by="ada"))
        f = FakeTask.last.failed
        assert "No space left on device" in f and "/DATA" not in f and "\n" not in f
