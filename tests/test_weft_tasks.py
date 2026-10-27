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


class FakeTask:
    """Stands for BaseNeuroLibreTask: records what the task says on the issue."""
    last = None

    def __init__(self, celery_task, screening=None, payload=None):
        s = dict(screening)
        self.screening = SimpleNamespace(
            issue_id=s["issue_id"], review_repository="neurolibre/reviews",
            target_repo_url=s["target_repo_url"], github_client=FakeGitHub.current,
            fields=s.get("fields"), at=s.get("at"), command_comment_id=s.get("command_comment_id"))
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
    def __init__(self, files):
        self.files = files
        self.default_branch = "main"

    def get_contents(self, path):
        from github import UnknownObjectException
        if path not in self.files:
            raise UnknownObjectException(404, {"message": "Not Found"}, {})
        return SimpleNamespace(path=path, sha="sha")


class FakeGitHub:
    current = None

    def __init__(self, files=None):
        self.files = files if files is not None else {}
        self.writes = []               # files created or updated through gh_create_file and gh_update_file_content
        self.opened = []               # repositories opened through the client
        FakeGitHub.current = self

    def get_repo(self, name):
        self.opened.append(name)
        return FakeRepo(self.files)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fake GitHub, a data folder that exists, a work root in tmp_path and recording steps."""
    gh = FakeGitHub()
    w = SimpleNamespace(gh=gh, calls=[], body={"data-archive": "10.5281/zenodo.1", "author": "ada", "editor": "@eddie"},
                        project="proj", comments={100: ("ada", "@roboneuro weft confirm")},
                        work=tmp_path / "wefts" / "00041", flat=tmp_path / "wefts")
    monkeypatch.setattr(tasks, "BaseNeuroLibreTask", FakeTask)
    monkeypatch.setattr(tasks, "WEFTS_PATH", str(tmp_path / "wefts"))
    monkeypatch.setattr(tasks.os.path, "isdir", lambda p: p == "/DATA_NFS/proj" or REAL_ISDIR(p))
    monkeypatch.setattr(tasks, "gh_get_project_name", lambda client, url: w.project)
    monkeypatch.setattr(tasks, "gh_read_from_issue_body", lambda c, r, i, tag: w.body.get(tag))

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

    def comment(client, repo, issue_id, comment_id):
        assert (repo, issue_id) == ("neurolibre/reviews", 41)
        from github import UnknownObjectException
        if comment_id not in w.comments:
            raise UnknownObjectException(404, {"message": "Not Found"}, {})
        return w.comments[comment_id]
    monkeypatch.setattr(tasks, "gh_get_issue_comment", comment)

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


def test_draft_comment_shows_the_draft_and_invites_nobody(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    t = FakeTask.last
    assert t.comment.startswith("### Description of this paper's data: draft\n\n")
    assert "- p.tsv: 3 columns" in t.comment
    assert "- proposed new term foo: Foo" in t.comment
    assert "<details><summary>Warnings from the check</summary>\n\n- a unit is guessed\n\n</details>" in t.comment
    assert "/edit/" not in t.comment and "invited" not in t.comment
    assert f"<details><summary>The draft</summary>\n\n```yaml\npaper: {DOI}\n```\n\n</details>" in t.comment
    assert "The paper's text was linked to 7 shared terms." in t.comment     # the store cannot be read here
    assert "`@roboneuro weft check at <comment id>`" in t.comment
    assert "`@roboneuro weft confirm at <comment id>`" in t.comment
    assert "3. If the draft is right as it is, reply saying so." in t.comment
    assert world.gh.writes == [] and world.gh.files == {}
    assert "fork" not in t.comment
    assert (world.work / "00041.weft.yaml").read_text() == f"paper: {DOI}\n"
    assert (world.work / "00041.profiles.json").exists()
    assert "—" not in t.comment
    # the paper is loaded into the store first
    assert world.calls[0][3:] == ["/DATA/cdn/current", "--doi", DOI, "--db", "/DATA/evidentron/graph.db"]
    assert "A first draft was made with a language model from the files themselves." in t.comment
    assert "openai-compatible" not in t.comment and "self-hosted" not in t.comment
    assert world.clones == [(f"https://github.com/roboneurolibre/paper-repo", "abc1234def")]
    assert SEEN['repo_dir'].endswith("/repos/00041")


def test_second_draft_updates_and_says_replaced(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert "replaced" not in FakeTask.last.comment
    tasks.weft_draft_task.run(payload())
    assert world.gh.writes == []
    assert "It replaces the earlier draft on the server." in FakeTask.last.comment


def test_redraft_reason_and_problems_are_said(world, monkeypatch):
    fake_draft(monkeypatch, problems=["column x has no term"], reason="The first draft was not valid",
               drafted_by="anthropic-api")
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert "Claude" not in c and "first draft was not valid" not in c
    assert "<details><summary>Problems the check found</summary>\n\n- column x has no term\n\n</details>" in c
    assert "2. Reply to this issue. Fix the problems listed below, then put the entries to change" in c
    assert c.index("Problems the check found") > c.index("<summary>The draft</summary>")


def test_a_draft_too_long_for_a_comment_names_the_file(world, monkeypatch):
    fake_draft(monkeypatch)
    real = tasks.weft_steps.draft

    def long_draft(*a, **k):
        result = real(*a, **k)
        result.weft.write_text("paper: x\n" + "# padding\n" * 7000)
        return result
    monkeypatch.setattr(tasks.weft_steps, "draft", long_draft)
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert ("<details><summary>The draft</summary>\n\nThe draft is too long for a comment; ask an editor for the "
            "file `00041.weft.yaml` on the server.\n\n</details>") in c
    assert "# padding" not in c and len(c) < tasks.MAX_DRAFT_IN_COMMENT and "fork" not in c


def test_missing_data_folder_fails(world, monkeypatch):
    world.project = "gone"
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The paper's data is not at its place on the server (gone), so there is nothing "
                                    "to describe. Ask an editor to check the data transfer.")


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
    assert FakeTask.last.failed == "The description could not be drafted: the data could not be profiled: bad."
    assert world.gh.writes == []


def test_backends_default_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(tmp_path / "none.yaml"))
    assert tasks._weft_backends() == {"draft": "openai-compatible", "redraft": "anthropic-api", "terms": "anthropic-api"}
    f = tmp_path / "e.yaml"
    f.write_text("weft:\n  backend:\n    draft: a\n    redraft: b\n    terms: c\n")
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(f))
    assert tasks._weft_backends() == {"draft": "a", "redraft": "b", "terms": "c"}


def seed_server(world, weft="paper: x\n", profiles="{}", where="work"):
    """A weft and its profiles on the server: the draft in the work folder, or the flat confirmed copy."""
    folder = world.work if where == "work" else world.flat
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "00041.weft.yaml").write_text(weft)
    if profiles is not None:
        (folder / "00041.profiles.json").write_text(profiles)


def test_check_unreadable(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(unreadable="The weft is not valid YAML."))
    tasks.weft_check_task.run(payload())
    assert FakeTask.last.comment == ("### Description of this paper's data: check\n\nNo corrections were given, so "
                                     "the description was checked as it is.\n\nThe description is not valid YAML.")


def test_check_errors_then_warnings(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(errors=["E1"], warnings=["W1"]))
    tasks.weft_check_task.run(payload())
    c = FakeTask.last.comment
    assert c.index("E1") < c.index("W1")
    assert ("The description does not pass the check yet. Fix these, then ask for `@roboneuro weft check` again:\n"
            "- E1") in c


def test_check_clean_and_no_draft(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload())
    assert FakeTask.last.comment == (
        "### Description of this paper's data: check\n\n"
        "No corrections were given, so the description was checked as it is.\n\n"
        "The description passes the check. There are no warnings.\n\n"
        "An editor or reviewer confirms it with `@roboneuro weft confirm`.")
    (world.work / "00041.weft.yaml").unlink()
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload())
    assert FakeTask.last.failed == ("There is no draft of this paper's data description on the server yet. Ask for "
                                    "`@roboneuro weft draft` first.")


def test_confirm_refused_without_data_archive(world, monkeypatch):
    seed_server(world)
    world.body["data-archive"] = None
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, fields=""))
    assert FakeTask.last.failed == (
        "The data archive has no DOI yet, so the description cannot be confirmed. Confirm it after the data has been "
        "deposited (`@roboneuro zenodo publish`, or set the archive DOI in the issue).")


def test_confirm_with_errors_writes_nothing(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(errors=["bad field", "bad link"]))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, fields=""))
    assert "bad field" in FakeTask.last.failed and "bad link" in FakeTask.last.failed
    assert world.gh.writes == []


def _recording_confirm(monkeypatch, seen):
    def confirm(weft, profiles, by, fields, submission, data_dir, site, db, run=None, record=None):
        seen.update(by=by, fields=fields, submission=submission, data_dir=str(data_dir), site=str(site), db=str(db),
                    record=str(record), profiles=str(profiles),
                    text=Path(submission).read_text() if submission else None, weft=str(weft),
                    weft_text=Path(weft).read_text())
        Path(weft).write_text("paper: x\nversion: 1.0.0\n")
        if record:
            tasks.weft_steps._record(Path(weft), Path(profiles), Path(record))     # as the real confirm does
        return tasks.weft_steps.ConfirmResult(version="1.0.0", addresses=tasks.weft_steps.addresses_of(DOI, ["age"]))
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)


BLOCK = "Here is the fix.\n\n```yaml\nfiles: [old]\n```\n\nand then\n\n```yml\nfiles: []\n```\n"


def test_confirm_success(world, monkeypatch):
    seed_server(world)
    seen = {}
    _recording_confirm(monkeypatch, seen)
    world.comments[100] = ("ada", "@roboneuro weft confirm fields a, b\n\n" + BLOCK)
    tasks.weft_confirm_task.run(payload(command_comment_id=100, fields="a, b"))
    c = FakeTask.last.comment
    assert "1.0.0" in c
    for part in ("/wefts/%s/fair2.json" % DOI, "/wefts/%s/croissant.json" % DOI, "/dois/%s.json" % DOI, "/terms/age/"):
        assert part in c
    assert world.gh.writes == [] and world.gh.files == {}
    assert "fork" not in c
    assert (world.flat / "00041.weft.yaml").read_text() == "paper: x\nversion: 1.0.0\n"
    assert seen["weft"] == str(world.work / "00041.weft.yaml")
    assert seen["fields"] == ["a", "b"] and seen["by"] == "ada"
    assert seen["data_dir"] == "/DATA_NFS/proj" and seen["site"] == "/DATA/evidentron/site"
    assert seen["db"] == "/DATA/evidentron/graph.db"
    assert seen["text"] == "files: []\n"              # the last yaml block of the comment
    assert seen["record"] == tasks.WEFTS_PATH
    assert world.rebuilds == [True]
    assert "Confirmed by @ada (reply in comment 100). The description is now version 1.0.0" in c


def test_confirm_at_a_comment_by_the_author(world, monkeypatch):
    seed_server(world)
    seen = {}
    _recording_confirm(monkeypatch, seen)
    world.comments[100] = ("eddie", "@roboneuro weft confirm at 200")
    world.comments[200] = ("Ada", "My corrections:\n```yaml\nfiles:\n- file: a.tsv\n```")
    tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed is None
    assert seen["by"] == "Ada"
    assert seen["text"] == "files:\n- file: a.tsv\n"
    assert "Confirmed by @Ada (reply in comment 200)." in FakeTask.last.comment


def test_confirm_at_a_comment_without_a_block_confirms_as_it_stands(world, monkeypatch):
    seed_server(world)
    seen = {}
    _recording_confirm(monkeypatch, seen)
    world.comments[100] = ("eddie", "@roboneuro weft confirm at 200")
    world.comments[200] = ("ada", "Looks good to me. ```python\nx = 1\n```")
    tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed is None
    assert seen["by"] == "ada" and seen["submission"] is None
    assert "Confirmed by @ada (reply in comment 200)." in FakeTask.last.comment


def test_a_reply_without_a_block_by_someone_else_is_refused(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    world.comments[200] = ("mallory", "Looks right.")
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed == ("Only the paper's author (@ada) or the editor (@eddie) can confirm; comment 200 "
                                    "is by @mallory.")


def test_an_unreadable_yaml_block_is_one_sentence(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: pytest.fail("check ran"))
    world.comments[200] = ("ada", "```yaml\nfiles: [unclosed\n```")
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    f = FakeTask.last.failed
    assert f.startswith("The `yaml` block in comment 200 could not be read (line ")
    assert f.endswith("), so nothing was applied. Fix the block and ask again.") and "\n" not in f


def test_confirm_at_a_missing_comment_is_a_sentence(world, monkeypatch):
    seed_server(world)
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, at=999))
    f = FakeTask.last.failed
    assert "999" in f and "\n" not in f and "fork" not in f


def test_corrections_by_someone_else_are_refused(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    world.comments[200] = ("mallory", "```yaml\nfiles: []\n```")
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed == ("Only the paper's author (@ada) or the editor (@eddie) can confirm; "
                                    "the corrections in comment 200 are by @mallory.")
    assert world.gh.writes == [] and world.calls == []


def test_the_editor_may_confirm_as_it_stands(world, monkeypatch):
    seed_server(world)
    seen = {}
    _recording_confirm(monkeypatch, seen)
    world.comments[100] = ("eddie", "@roboneuro weft confirm")
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert seen["by"] == "eddie" and seen["submission"] is None
    assert "Confirmed by @eddie as the draft stood." in FakeTask.last.comment


def test_confirm_without_a_block_confirms_as_it_stands(world, monkeypatch):
    seed_server(world)
    seen = {}
    _recording_confirm(monkeypatch, seen)
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert seen["by"] == "ada" and seen["submission"] is None and seen["fields"] is None
    assert "Confirmed by @ada as the draft stood." in FakeTask.last.comment


def test_confirm_without_the_command_comment_is_refused(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(fields=""))
    f = FakeTask.last.failed
    assert "comment" in f and "\n" not in f


def test_check_applies_the_block_of_its_own_comment(world, monkeypatch):
    seed_server(world)
    seen = {}

    def check(weft, profiles, data_dir, run=None, db=None, submission=None, by=None):
        seen.update(text=Path(submission).read_text() if submission else None, by=by)
        return tasks.weft_steps.CheckResult()
    monkeypatch.setattr(tasks.weft_steps, "check", check)
    world.comments[100] = ("mallory", "@roboneuro weft check\n```yaml\nfiles: []\n```")
    tasks.weft_check_task.run(payload(command_comment_id=100))
    assert seen == {"text": "files: []\n", "by": "mallory"}      # anyone's block may be checked
    assert FakeTask.last.failed is None
    seen.clear()
    world.comments[100] = ("ada", "@roboneuro weft check")
    tasks.weft_check_task.run(payload(command_comment_id=100))
    assert seen["text"] is None


def test_check_at_a_comment_without_a_block_checks_as_it_stands(world, monkeypatch):
    seed_server(world)
    seen = {}
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, submission=None, **k: seen.update(s=submission)
                        or tasks.weft_steps.CheckResult(warnings=["W1", "W2"]))
    world.comments[200] = ("ada", "no block here")
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed is None and seen == {"s": None}
    c = FakeTask.last.comment
    assert "No corrections were given, so the description was checked as it is." in c
    assert ("The description passes the check. There are 2 warnings, listed below; they do not block "
            "confirmation.") in c
    assert "An editor or reviewer confirms it with `@roboneuro weft confirm at 200`." in c
    assert c.endswith("<details><summary>Warnings</summary>\n\n- W1\n- W2\n\n</details>")


def test_extra_payload_survives_to_dict_and_from_dict(monkeypatch):
    class G:
        def __init__(self, *a, **k): pass
    monkeypatch.setattr(screening_client, "Github", G)
    monkeypatch.setattr(screening_client.ScreeningClient, "_init_responder", lambda self: None)
    client = screening_client.ScreeningClient("t", issue_id=3, fields="a, b", command_comment_id=100, at=200)
    d = client.to_dict()
    assert (d["fields"], d["command_comment_id"], d["at"], d["comment_id"]) == ("a, b", 100, 200, None)
    again = screening_client.ScreeningClient.from_dict(d)
    assert (again.fields, again.command_comment_id, again.at) == ("a, b", 100, 200)


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
    assert FakeTask.last.failed == ("The paper has no published build yet, so its code cannot be read. Run "
                                    "`@roboneuro production sync myst` first.")
    assert world.gh.writes == [] and world.clones == []


def test_clone_failure_is_one_sentence(world, monkeypatch):
    world.clone_error = "fatal: reference is not a tree\nmore"
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "could not be cloned" in FakeTask.last.failed and "\n" not in FakeTask.last.failed


def test_github_errors_become_a_sentence(world, monkeypatch):
    from github import GithubException
    seed_server(world)

    def boom(*a, **k):
        raise GithubException(404, {"message": "Not Found", "documentation_url": "x"}, {})
    monkeypatch.setattr(tasks, "gh_read_from_issue_body", boom)
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert FakeTask.last.failed == "GitHub could not be reached for the review issue (404 Not Found). Ask again in a minute."


def test_a_failed_term_linking_is_said_not_counted(world, monkeypatch):
    fake_draft(monkeypatch)
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda doi, db, backend, run=None: (None, "The model's answer was not JSON"))
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert "The paper's text could not be linked to shared terms: the model's answer was not JSON." in c
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


# The server is the place of record: check and confirm read its copy, the flat confirmed one first.

@pytest.mark.parametrize("where", ["work", "flat"])
def test_check_reads_the_weft_and_profiles_from_the_servers_copy(world, monkeypatch, where):
    seed_server(world, weft=f"paper: {where}\n", profiles=f'{{"{where}": true}}', where=where)
    seen = {}
    monkeypatch.setattr(tasks.weft_steps, "check", lambda weft, profiles, *a, **k: seen.update(
        w=Path(weft).read_text(), p=Path(profiles).read_text()) or tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload())
    assert FakeTask.last.failed is None
    assert seen == {"w": f"paper: {where}\n", "p": f'{{"{where}": true}}'}
    assert world.gh.writes == []


def test_check_prefers_the_flat_confirmed_copy_to_the_draft(world, monkeypatch):
    seed_server(world, weft="paper: draft\n", profiles='{"draft": true}')
    seed_server(world, weft="paper: confirmed\n", profiles='{"confirmed": true}', where="flat")
    seen = {}
    monkeypatch.setattr(tasks.weft_steps, "check", lambda weft, profiles, *a, **k: seen.update(
        w=Path(weft).read_text(), p=Path(profiles).read_text()) or tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload())
    assert seen == {"w": "paper: confirmed\n", "p": '{"confirmed": true}'}


def test_confirm_works_on_a_copy_of_the_flat_weft_and_records_it_there(world, monkeypatch):
    seed_server(world, weft="paper: draft\n", profiles='{"draft": true}')
    seed_server(world, weft="paper: confirmed\n", profiles='{"confirmed": true}', where="flat")
    seen = {}
    _recording_confirm(monkeypatch, seen)
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert seen["weft"] == str(world.work / "00041.weft.yaml")         # never the flat copy itself
    assert seen["weft_text"] == "paper: confirmed\n"
    assert seen["record"] == tasks.WEFTS_PATH
    assert (world.flat / "00041.weft.yaml").read_text() == "paper: x\nversion: 1.0.0\n"
    assert world.gh.writes == [] and "fork" not in FakeTask.last.comment


def test_no_profiles_anywhere_is_a_sentence(world, tmp_path):
    seed_server(world, profiles=None)
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload())
    assert FakeTask.last.failed == ("There is no draft of this paper's data description on the server yet. Ask for "
                                    "`@roboneuro weft draft` first.")
    assert str(tmp_path) not in FakeTask.last.failed and "\n" not in FakeTask.last.failed


def test_a_flat_weft_without_its_profiles_is_a_sentence(world, monkeypatch):
    seed_server(world, weft="paper: draft\n", profiles='{"draft": true}')
    seed_server(world, weft="paper: confirmed\n", profiles=None, where="flat")
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: pytest.fail("check ran"))
    with pytest.raises(Ignore):
        tasks.weft_check_task.run(payload())
    assert FakeTask.last.failed == (
        "The confirmed description of this paper's data is on the server without its profiles, so it cannot be "
        "checked. An editor restores the profiles beside it, or removes the confirmed description and asks for "
        "`@roboneuro weft draft` again.")


def test_draft_check_and_confirm_write_nothing_to_github(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload(command_comment_id=100))
    _recording_confirm(monkeypatch, {})
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert FakeTask.last.failed is None
    assert world.gh.writes == [] and world.gh.files == {}


# I6: a draft never replaces confirmed work.

def test_a_draft_over_a_confirmed_weft_is_refused(world, monkeypatch):
    fake_draft(monkeypatch)
    seed_server(world, weft="paper: x\nversion: 1.0.0\n", where="flat")
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == (
        "This paper's data description is already confirmed. To change it, reply with the entries to change in a "
        "`yaml` code block and ask an editor or reviewer for `@roboneuro weft confirm at <comment id>`; to start "
        "over, an editor removes the confirmed description on the server.")
    assert world.gh.writes == [] and world.calls == []


def test_a_draft_over_an_unconfirmed_weft_goes_on(world, monkeypatch):
    fake_draft(monkeypatch)
    seed_server(world, weft="paper: x\nfiles:\n- file: a.tsv\n  status: proposed\n")
    tasks.weft_draft_task.run(payload())
    assert "It replaces the earlier draft on the server." in FakeTask.last.comment


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
    seed_server(world)
    held = {}

    def confirm(*a, **k):
        held["confirm"] = _busy(tasks.STORE_LOCK_PATH)
        return tasks.weft_steps.ConfirmResult(version="1.0.0", confirmed=True, addresses={})
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", lambda: held.setdefault("rebuild", _busy(tasks.STORE_LOCK_PATH)) and {})
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert held == {"confirm": True, "rebuild": True}
    assert world.calls[0][1:] == ["-m", "evidentron.graph", "/DATA/cdn/current", "--doi", DOI,
                                  "--db", "/DATA/evidentron/graph.db"]
    assert "--identifier" not in world.calls[0]


def test_a_store_kept_busy_is_one_sentence(world, monkeypatch):
    import fcntl
    seed_server(world)
    monkeypatch.setattr(tasks, "STORE_LOCK_WAIT", 0.2)
    with open(tasks.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with pytest.raises(Ignore):
            tasks.weft_confirm_task.run(payload(command_comment_id=100))
    f = FakeTask.last.failed
    assert "busy" in f and "\n" not in f and "store.lock" not in f


def test_a_failed_reload_at_confirm_is_one_sentence(world, monkeypatch):
    seed_server(world)
    world.load_result = (1, "Traceback (most recent call last):\n  File x\nValueError: no such site")
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert "no such site" in FakeTask.last.failed and "Traceback" not in FakeTask.last.failed


# C3: the confirmed paper's files reach the served CDN.

def test_a_failed_cdn_rebuild_is_a_sentence_in_the_comment(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.0.0", confirmed=True, addresses=tasks.weft_steps.addresses_of(DOI, [])))

    def boom():
        raise RuntimeError("/DATA/cdn/current is a folder, not a symlink")
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", boom)
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    c = FakeTask.last.comment
    assert "Confirmed by @ada as the draft stood. The description is now version 1.0.0" in c
    assert c.endswith("The CDN could not be rebuilt (RuntimeError); the documents appear after the next rebuild.")
    assert "/DATA/cdn/current" not in c


# Confirmed but not published.

def test_confirmed_but_not_published_says_so(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.2.0", confirmed=True, errors=["No weft of x is in the server's folder of wefts."]))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100))
    f = FakeTask.last.failed
    assert f == ("The description was confirmed at version 1.2.0 but not published: no description of x is in the "
                 "server's folder of descriptions. Asking for `@roboneuro weft confirm` again is safe once this is fixed.")
    assert world.rebuilds == []


# I8: a local file error is one sentence.

def test_a_local_file_error_is_one_sentence(world, monkeypatch):
    seed_server(world)

    def full(*a, **k):
        raise OSError(28, "No space left on device", "/DATA/evidentron/wefts/00041")
    monkeypatch.setattr(tasks.os, "makedirs", full)
    for task in (tasks.weft_draft_task, tasks.weft_check_task, tasks.weft_confirm_task):
        fake_draft(monkeypatch)
        with pytest.raises(Ignore):
            task.run(payload(command_comment_id=100))
        f = FakeTask.last.failed
        assert "No space left on device" in f and "/DATA" not in f and "\n" not in f


def test_a_check_with_corrections_says_they_were_applied_to_a_copy(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    world.comments[200] = ("ada", "```yaml\nfiles: []\n```")
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    assert "The corrections from comment 200 were applied." in c
    assert ("The description passes the check. The check's warnings are not listed after corrections; the draft "
            "comment lists them.") in c
    assert "no warnings" not in c and "<details>" not in c
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        warnings=["The entry b.csv was added."]))
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    assert "warning" not in c.split("<details>")[1]
    assert c.endswith("<details><summary>Notes on the corrections</summary>\n\n- The entry b.csv was added.\n\n</details>")
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        errors=["E1"], warnings=["The entry b.csv was added."]))
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.comment.endswith("<summary>Notes on the corrections</summary>\n\n- The entry b.csv was added.\n\n</details>")
    assert "An editor or reviewer confirms it with `@roboneuro weft confirm at 200`." in c


def test_a_draft_holding_backticks_gets_a_longer_fence_and_reads_back(world, monkeypatch):
    fake_draft(monkeypatch)
    real = tasks.weft_steps.draft

    def draft(*a, **k):
        result = real(*a, **k)
        result.weft.write_text("paper: x\nnotes: |\n  ```yaml\n  a: 1\n  ```\n")
        return result
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert "````yaml\npaper: x\n" in c
    assert tasks._yaml_block(c) == "paper: x\nnotes: |\n  ```yaml\n  a: 1\n  ```\n"


# B9: the comments read as messages to an author.

TWO_TABLES = """paper: 10.55458/neurolibre.00041
files:
- file: harvests.csv
  row: {term: plot-year, description: One row is one field plot in one harvest year.}
  fields:
  - name: plot_id
    description: The identifier of the plot.
    datatype: string
    evidence_type: identifier
    term: plot
  - name: yield_t_ha
    description: 'Grain yield: tonnes per hectare.'
    datatype: float
    evidence_type: quantitative_raw
    unit: t/ha
    term: crop-yield
- file: stations.csv
  row: {description: NOAA weather station near the plots.}
  fields:
  - {name: station, description: The station code., datatype: string, evidence_type: identifier, term: null}
- file: notes.pdf
  kind: binary
  description: A field notebook, scanned.
new_terms:
  crop-yield: {label: crop yield, definition: The grain harvested per unit area.}
"""


def two_table_draft(monkeypatch, problems=None, warnings=None):
    def draft(doi, data_dir, repo_dir, work, backends, lock, run=None):
        weft, profiles = Path(work) / "00041.weft.yaml", Path(work) / "00041.profiles.json"
        weft.write_text(TWO_TABLES)
        profiles.write_text("{}")
        return tasks.weft_steps.DraftResult(weft=weft, profiles=profiles, drafted_by="openai-compatible", reason=None,
                                            problems=problems or [], warnings=warnings or [],
                                            summary=tasks.weft_steps.summary_of(weft))
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)


def a_store(path, links=(("crop-yield", "crop yield"), ("plot", "field plot")), concepts=True):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("create table kg_paper_concept (work_id text, concept_id text, role text, quote text)")
    if concepts:
        con.execute("create table kg_concept (id text primary key, label text)")
    for cid, label in links:
        con.execute("insert into kg_paper_concept values (?, ?, 'measures', 'q')", (DOI, cid))
        con.execute("insert into kg_paper_concept values (?, ?, 'studies', 'q')", (DOI, cid))
        if concepts and label:
            con.execute("insert into kg_concept values (?, ?)", (cid, label))
    con.execute("insert into kg_paper_concept values ('10.1/other', 'soil-ph', 'measures', 'q')")
    con.commit()
    con.close()
    return path


def prose_of(comment):
    """The comment without its code blocks and inline code: what an author reads as sentences."""
    import re
    text = re.sub(r"^ *```.*?^ *```$", "", comment, flags=re.S | re.M)
    return re.sub(r"`[^`]*`", "", text)


def test_the_draft_comment_for_two_tables_reads_as_a_message(world, monkeypatch, tmp_path):
    two_table_draft(monkeypatch, warnings=["The weft has a unit that is guessed."])
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "graph.db")))
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda doi, db, backend, run=None: (2, None))
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    (tmp_path / "rendered.md").write_text(c)
    assert c.startswith("### Description of this paper's data: draft\n\nNeuroLibre publishes, with each paper,")
    assert "[FAIR²](https://fair2.ai) and [Croissant](https://mlcommons.org/croissant/) documents." in c
    assert ("**What the draft found**\n"
            "- `harvests.csv`: a table with 2 columns. One row is one field plot in one harvest year.\n"
            "- `stations.csv`: a table with 1 column. One row is NOAA weather station near the plots.\n"
            "- `notes.pdf`: not a table; a field notebook, scanned.\n"
            "- Proposed new term `crop-yield`: crop yield.") in c
    assert "The paper's text was linked to 2 shared terms: crop yield, field plot." in c
    example = ("   ```yaml\n   files:\n     - file: harvests.csv\n       fields:\n         - name: plot_id\n"
               "           description: The identifier of the plot.\n           datatype: string\n"
               "           evidence_type: identifier\n           term: plot\n   ```")
    assert ", for example:\n\n" + example + "\n\n   To change a column's term, pick an id" in c
    assert "**What the editor or reviewer does**\nWrite `@roboneuro weft check at <comment id>`" in c
    assert ("the id is the number at the end of the reply's link. The confirmation is recorded under the name of "
            "the person who wrote that reply. An editor can write `@roboneuro weft confirm` without `at` to confirm "
            "the draft as it stands. To confirm only some columns, add `fields <name>, <name>`.") in c
    assert "<summary>Warnings from the check</summary>\n\n- The description has a unit that is guessed." in c
    # the draft itself reads back as the weft
    assert tasks._yaml_block(c) == TWO_TABLES
    # no fenced yaml inside a sentence, no nested backticks, and "weft" only in commands and file names
    assert all(line.lstrip().startswith("```") for line in c.splitlines() if "```" in line)
    assert "````" not in c and "—" not in c
    assert "weft" not in prose_of(c).replace("00041.weft.yaml", "")
    for line in prose_of(c).splitlines():
        assert ".." not in line, line


def test_the_example_comes_from_a_collection_when_there_is_no_table(world, monkeypatch):
    def draft(doi, data_dir, repo_dir, work, backends, lock, run=None):
        weft, profiles = Path(work) / "00041.weft.yaml", Path(work) / "00041.profiles.json"
        weft.write_text("files:\n- collection: spectra\n  variables:\n  - {name: wavelength, datatype: float, "
                        "concept: wavelength}\n")
        profiles.write_text("{}")
        return tasks.weft_steps.DraftResult(weft=weft, profiles=profiles, drafted_by="x", reason=None)
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)
    tasks.weft_draft_task.run(payload())
    assert ("   files:\n     - collection: spectra\n       variables:\n         - name: wavelength\n"
            "           datatype: float\n           term: wavelength\n") in FakeTask.last.comment


def test_with_nothing_to_show_the_example_is_dropped_and_the_sentence_kept(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    assert ("an entry with `remove: true` is dropped). To change a column's term, pick an id from the "
            "[list of terms](https://cdn.evidencepub.io/terms/)") in c
    assert "for example" not in c


def test_term_labels_come_from_the_store_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "g.db", links=(("plot", "field plot"),
                                                                                      ("soil-depth", None)))))
    assert tasks._paper_term_labels(DOI) == ["field plot", "soil depth"]
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "h.db", concepts=False)))
    assert tasks._paper_term_labels(DOI) == ["crop yield", "plot"]          # no vocabulary table: the ids in words
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "i.db", links=())))
    assert tasks._paper_term_labels(DOI) == []
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(tmp_path / "none.db"))
    assert tasks._paper_term_labels(DOI) is None
    assert not (tmp_path / "none.db").exists()


def test_no_linked_terms_means_no_terms_sentence(world, monkeypatch, tmp_path):
    fake_draft(monkeypatch)
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "g.db", links=())))
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda *a, **k: (0, None))
    tasks.weft_draft_task.run(payload())
    assert "text was linked" not in FakeTask.last.comment
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda *a, **k: (0, None))
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(tmp_path / "none.db"))
    tasks.weft_draft_task.run(payload())
    assert "text was linked" not in FakeTask.last.comment


def test_a_count_with_no_labels_for_the_paper_says_the_count(world, monkeypatch, tmp_path):
    fake_draft(monkeypatch)
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "g.db", links=())))
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda *a, **k: (3, None))
    tasks.weft_draft_task.run(payload())
    assert "\n\nThe paper's text was linked to 3 shared terms.\n\n" in FakeTask.last.comment


def test_the_confirm_comment_names_the_terms_by_their_labels(world, monkeypatch, tmp_path):
    seed_server(world)
    monkeypatch.setattr(tasks, "GRAPH_DB_PATH", str(a_store(tmp_path / "g.db")))
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.1.0", confirmed=True, addresses=tasks.weft_steps.addresses_of(DOI, ["crop-yield", "soil-ph"])))
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    cdn = "https://cdn.evidencepub.io"
    assert FakeTask.last.comment == (
        "### Description of this paper's data: confirmed\n\n"
        "Confirmed by @ada as the draft stood. The description is now version 1.1.0 and is published with the paper:\n\n"
        f"- FAIR²: {cdn}/wefts/{DOI}/fair2.json\n"
        f"- Croissant: {cdn}/wefts/{DOI}/croissant.json\n"
        f"- Metadata: {cdn}/dois/{DOI}.json\n"
        f"- Terms it uses: [crop yield]({cdn}/terms/crop-yield/), [soil ph]({cdn}/terms/soil-ph/)")
