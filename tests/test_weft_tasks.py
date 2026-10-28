"""The three tasks that describe a paper's data, with weft_steps, the GitHub helpers and the data folder faked:
nothing touches the network."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore

import neurolibre_celery_tasks as celery_tasks
import screening_client
import weft_tasks as tasks
from conftest import DOI, FakeGitHub, FakeTask, said

REAL_ISDIR = os.path.isdir
SEEN = {}
CDN = "https://cdn.example.org"
ADDRESSES = {"metadata": f"{CDN}/dois/{DOI}.json", "fair2": f"{CDN}/wefts/{DOI}/fair2.json",
             "croissant": f"{CDN}/wefts/{DOI}/croissant.json"}


def published(version="1.0.0", terms=(("age", "age"),)):
    """What weft_steps.confirm gives when every step worked."""
    return tasks.weft_steps.ConfirmResult(version=version, confirmed=True, addresses=dict(ADDRESSES),
                                          terms=[{"id": i, "label": label} for i, label in terms],
                                          terms_url=f"{CDN}/terms/")


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
    config = tmp_path / "evidentron.yaml"
    config.write_text("weft:\n  backend:\n    draft: openai-compatible\n    redraft: anthropic-api\n"
                      "    terms: anthropic-api\n")
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(config))

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
    w.load_result = (0, said("graph", papers=[{"doi": DOI, "loaded": True}]))
    w.clones, w.commit = [], "abc1234def"
    monkeypatch.setattr(tasks, "REPOS_PATH", str(tmp_path / "repos"))
    monkeypatch.setattr(tasks, "_published_commit", lambda org, repo: w.commit)
    monkeypatch.setattr(tasks, "gh_clone_repository", lambda url, path, depth=1, commit=None, timeout=None:
                        w.clones.append((url, commit)) or w.clone_timeouts.append(timeout)
                        or w.clone_error and (_ for _ in ()).throw(RuntimeError(w.clone_error)))
    w.clone_timeouts = []
    w.clone_error = None
    monkeypatch.setattr(tasks.weft_steps, "subprocess_runner", runner)
    monkeypatch.setattr(tasks.weft_steps, "link_terms",
                        lambda doi, db, backend, run=None: tasks.weft_steps.LinkResult(linked=7))
    monkeypatch.setattr(tasks.weft_steps, "STORE_LOCK_PATH", tmp_path / "store.lock")
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
    assert world.gh.used == []
    assert "fork" not in t.comment
    assert (world.work / "00041.weft.yaml").read_text() == f"paper: {DOI}\n"
    assert (world.work / "00041.profiles.json").exists()
    assert "—" not in t.comment
    # the paper is loaded into the store first
    assert world.calls[0][3:] == ["/DATA/cdn/current", "--doi", DOI, "--db", "/DATA/evidentron/graph.db", "--json"]
    assert "A first draft was made with a language model from the files themselves." in t.comment
    assert "openai-compatible" not in t.comment and "self-hosted" not in t.comment
    assert world.clones == [(f"https://github.com/roboneurolibre/paper-repo", "abc1234def")]
    assert SEEN['repo_dir'].endswith("/repos/00041")


def test_second_draft_updates_and_says_replaced(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert "replaced" not in FakeTask.last.comment
    tasks.weft_draft_task.run(payload())
    assert world.gh.used == []
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
    world.load_result = (1, said("graph", errors=["The paper was not loaded from /DATA/cdn/current: no such site."]))
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The paper could not be loaded into the store: the paper was not loaded from "
                                    "/DATA/cdn/current: no such site.")


def test_draft_that_wrote_nothing_fails_with_the_problem(world, monkeypatch):
    fake_draft(monkeypatch, problems=["The data could not be profiled: bad"], write=False)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == "The description could not be drafted: the data could not be profiled: bad."
    assert world.gh.used == []


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
        "The description passes the check. There are no warnings.\n\n" + WHO)
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
    assert world.gh.used == []


def _recording_confirm(monkeypatch, seen):
    def confirm(weft, profiles, by, fields, submission, data_dir, site, db, run=None, record=None):
        seen.update(by=by, fields=fields, submission=submission, data_dir=str(data_dir), site=str(site), db=str(db),
                    record=str(record), profiles=str(profiles),
                    text=Path(submission).read_text() if submission else None, weft=str(weft),
                    weft_text=Path(weft).read_text())
        Path(weft).write_text("paper: x\nversion: 1.0.0\n")
        if record:
            tasks.weft_steps._record(Path(weft), Path(profiles), Path(record))     # as the real confirm does
        return published()
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
    assert world.gh.used == []
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
    assert world.gh.used == [] and world.calls == []


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

    def check(weft, profiles, data_dir, run=None, db=None, submission=None):
        seen.update(text=Path(submission).read_text() if submission else None, db=str(db))
        return tasks.weft_steps.CheckResult()
    monkeypatch.setattr(tasks.weft_steps, "check", check)
    world.comments[100] = ("mallory", "@roboneuro weft check\n```yaml\nfiles: []\n```")
    tasks.weft_check_task.run(payload(command_comment_id=100))
    assert seen == {"text": "files: []\n", "db": tasks.GRAPH_DB_PATH}      # anyone's block may be checked
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
    assert WHO + " For this reply, that is `@roboneuro weft confirm at 200`." in c
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

    def failing(doi, data_dir, repo_dir, work, backends, lock, run=None):
        return tasks.weft_steps.DraftResult(weft=Path(work) / "00041.weft.yaml", profiles=Path(work) / "00041.profiles.json",
                                            drafted_by="anthropic-api", reason="x", problems=["The data could not be profiled: bad"])
    monkeypatch.setattr(tasks.weft_steps, "draft", failing)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "could not be profiled" in FakeTask.last.failed
    assert world.gh.used == []


def test_no_published_commit_means_no_draft(world, monkeypatch):
    world.commit = None
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The paper has no published build yet, so its code cannot be read. Run "
                                    "`@roboneuro production sync myst` first.")
    assert world.gh.used == [] and world.clones == []


def test_clone_failure_is_one_sentence(world, monkeypatch):
    world.clone_error = "fatal: reference is not a tree\nmore"
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == "The repository roboneurolibre/paper-repo could not be cloned at abc1234: more."


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
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda doi, db, backend, run=None: tasks.weft_steps.LinkResult(
        failure="The model's answer was not JSON"))
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
    monkeypatch.setattr(celery_tasks, "DATA_ROOT_PATH", str(data))
    _site_build(data / celery_tasks.MYST_FOLDER / celery_tasks.GH_ORGANIZATION / "paper-repo" / "abc1234def" / "_build" / "site")
    monkeypatch.setattr(celery_tasks.myst_cdn, "CDN_ROOT", tmp_path / "cdn")
    monkeypatch.setattr(celery_tasks.myst_cdn, "EVIDENTRON_SITE", extras)
    asked = []
    monkeypatch.setattr(celery_tasks.requests, "get", lambda url, timeout=None: asked.append((url, timeout))
                        or SimpleNamespace(status_code=200, text="abc1234def\n"))

    class Rsync:
        def __init__(self, *a, **k): pass
        def communicate(self): return (b"", None)
        def wait(self): return 0
    monkeypatch.setattr(celery_tasks.subprocess, "Popen", Rsync)
    note = celery_tasks.add_to_myst_cdn(SimpleNamespace(repo_name="paper-repo"), "neurolibre.00041", "v1")
    assert asked == [(f"{celery_tasks.PREVIEW_SERVER}/myst/roboneurolibre/paper-repo/latest.txt", 20)]
    return note


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
    assert world.gh.used == []


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
    assert world.gh.used == [] and "fork" not in FakeTask.last.comment


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
        "checked. An editor restores the profiles beside it, or runs the reset command on the server and asks for "
        "`@roboneuro weft draft` again.")


def test_draft_check_and_confirm_write_nothing_to_github(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    tasks.weft_check_task.run(payload(command_comment_id=100))
    _recording_confirm(monkeypatch, {})
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert FakeTask.last.failed is None
    assert world.gh.used == []            # the client itself is never used: GitHub is reached only through
    assert sorted(n for n in vars(tasks) if n.startswith("gh_")) == [   # these helpers, which only read
        "gh_clone_repository", "gh_get_issue_comment", "gh_get_project_name", "gh_read_from_issue_body"]


# I6: a draft never replaces confirmed work.

def test_a_draft_over_a_confirmed_weft_is_refused(world, monkeypatch):
    fake_draft(monkeypatch)
    seed_server(world, weft="paper: x\nversion: 1.0.0\n", where="flat")
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == (
        "This paper's data description is already confirmed. To change it, reply with the entries to change in a "
        "`yaml` code block. " + WHO + " To start over, an editor runs the reset command on the server.")
    assert world.gh.used == [] and world.calls == []


def test_a_draft_over_an_unconfirmed_weft_goes_on(world, monkeypatch):
    fake_draft(monkeypatch)
    seed_server(world, weft="paper: x\nfiles:\n- file: a.tsv\n  status: proposed\n")
    tasks.weft_draft_task.run(payload())
    assert "It replaces the earlier draft on the server." in FakeTask.last.comment


def test_the_task_leaves_the_earlier_draft_to_the_draft_step(world, monkeypatch, tmp_path):
    """weft_steps.draft is the one place the earlier draft is removed (its tests say when); the task removes
    nothing, so with the step faked the earlier files are all still there."""
    old = tmp_path / "wefts" / "00041" / "00041.selfhosted.weft.yaml"
    old.parent.mkdir(parents=True)
    old.write_text("old")
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert old.read_text() == "old"


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
        loaded = (0, said("graph", papers=[{"doi": DOI, "loaded": True}]))
        return lambda argv: held.setdefault("load", _busy(tasks.weft_steps.STORE_LOCK_PATH)) and loaded or loaded
    monkeypatch.setattr(tasks.weft_steps, "subprocess_runner", runner)

    def draft(*a, **k):
        held["draft"] = _busy(tasks.weft_steps.STORE_LOCK_PATH)
        return fake_result(*a)
    fake_draft(monkeypatch)
    fake_result = tasks.weft_steps.draft
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)
    monkeypatch.setattr(tasks.weft_steps, "link_terms",
                        lambda *a, **k: held.setdefault("link", _busy(tasks.weft_steps.STORE_LOCK_PATH))
                        and tasks.weft_steps.LinkResult(linked=3) or tasks.weft_steps.LinkResult(linked=3))
    tasks.weft_draft_task.run(payload())
    assert held == {"load": True, "draft": False, "link": True}


def test_confirm_holds_the_store_lock_and_reloads_the_paper_first(world, monkeypatch):
    seed_server(world)
    held = {}

    def confirm(*a, **k):
        held["confirm"] = _busy(tasks.weft_steps.STORE_LOCK_PATH)
        return tasks.weft_steps.ConfirmResult(version="1.0.0", confirmed=True, addresses={})
    monkeypatch.setattr(tasks.weft_steps, "confirm", confirm)
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", lambda: held.setdefault("rebuild", _busy(tasks.weft_steps.STORE_LOCK_PATH)) and {})
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert held == {"confirm": True, "rebuild": True}
    assert world.calls[0][1:] == ["-m", "evidentron.graph", "/DATA/cdn/current", "--doi", DOI,
                                  "--db", "/DATA/evidentron/graph.db", "--json"]
    assert "--identifier" not in world.calls[0]


def test_a_store_kept_busy_is_one_sentence(world, monkeypatch):
    import fcntl
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "STORE_LOCK_WAIT", 0.2)
    with open(tasks.weft_steps.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with pytest.raises(Ignore):
            tasks.weft_confirm_task.run(payload(command_comment_id=100))
    f = FakeTask.last.failed
    assert "busy" in f and "\n" not in f and "store.lock" not in f


def test_a_failed_reload_at_confirm_is_one_sentence(world, monkeypatch):
    seed_server(world)
    world.load_result = (1, said("graph", errors=["The paper was not loaded: no such site."]))
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: pytest.fail("confirm ran"))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert "no such site" in FakeTask.last.failed and "Traceback" not in FakeTask.last.failed


# C3: the confirmed paper's files reach the served CDN.

def test_a_failed_cdn_rebuild_is_a_sentence_in_the_comment(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.0.0", confirmed=True, addresses=dict(ADDRESSES)))

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


def test_a_check_with_corrections_lists_its_warnings_like_any_other(world, monkeypatch):
    seed_server(world)
    world.comments[200] = ("ada", "```yaml\nfiles: []\n```")
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        warnings=["b.csv: a unit is missing"], notes=["The entry b.csv was added."]))
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    keep("check-comment.md", c)
    assert "The corrections from comment 200 were applied." in c
    assert "The description passes the check. There is 1 warning, listed below; it does not block confirmation." in c
    assert WHO + " For this reply, that is `@roboneuro weft confirm at 200`." in c
    assert ("<details><summary>Warnings</summary>\n\n- b.csv: a unit is missing\n\n</details>\n\n"
            "<details><summary>Notes on the corrections</summary>\n\n- The entry b.csv was added.\n\n</details>") in c
    assert "not listed" not in c
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        errors=["E1"], warnings=["W1"]))
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    assert c.endswith("<details><summary>Warnings</summary>\n\n- W1\n\n</details>")
    assert WHO not in c


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


TWO_TABLES_SUMMARY = [
    "`harvests.csv`: a table with 2 columns. One row is one field plot in one harvest year.",
    "`stations.csv`: a table with 1 column. One row is NOAA weather station near the plots.",
    "`notes.pdf`: not a table; a field notebook, scanned.",
    "Proposed new term `crop-yield`: crop yield.",
]
RENDERED = Path(os.environ.get("WEFT_RENDERED_DIR", "")) if os.environ.get("WEFT_RENDERED_DIR") else None


def two_table_draft(monkeypatch, problems=None, warnings=None):
    """A draft as Evidentron gives it: the weft written, and the summary from the command's own result."""
    def draft(doi, data_dir, repo_dir, work, backends, lock, run=None):
        weft, profiles = Path(work) / "00041.weft.yaml", Path(work) / "00041.profiles.json"
        weft.write_text(TWO_TABLES)
        profiles.write_text("{}")
        return tasks.weft_steps.DraftResult(weft=weft, profiles=profiles, drafted_by="openai-compatible", reason=None,
                                            problems=problems or [], warnings=warnings or [],
                                            summary=list(TWO_TABLES_SUMMARY))
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)


def linked(monkeypatch, count, labels=(), failure=None):
    monkeypatch.setattr(tasks.weft_steps, "link_terms", lambda *a, **k: tasks.weft_steps.LinkResult(
        linked=count, labels=list(labels), failure=failure))


def prose_of(comment):
    """The comment without its code blocks and inline code: what an author reads as sentences."""
    import re
    text = re.sub(r"^ *```.*?^ *```$", "", comment, flags=re.S | re.M)
    return re.sub(r"`[^`]*`", "", text)


def keep(name, comment):
    """Write a rendered comment where WEFT_RENDERED_DIR says, for a person to read."""
    if RENDERED:
        RENDERED.mkdir(parents=True, exist_ok=True)
        (RENDERED / name).write_text(comment)


WHO = ("The paper's author or the issue's editor confirms, by replying; an editor or reviewer records an author's "
       "reply with `@roboneuro weft confirm at <comment id>`.")


def test_the_draft_comment_for_two_tables_reads_as_a_message(world, monkeypatch):
    two_table_draft(monkeypatch, warnings=["The weft has a unit that is guessed."])
    linked(monkeypatch, 2, ["crop yield", "field plot"])
    tasks.weft_draft_task.run(payload())
    c = FakeTask.last.comment
    keep("draft-comment.md", c)
    assert c.startswith("### Description of this paper's data: draft\n\nNeuroLibre publishes, with each paper,")
    assert "[FAIR²](https://fair2.ai) and [Croissant](https://mlcommons.org/croissant/) documents." in c
    assert "**What the draft found**\n" + "\n".join(f"- {line}" for line in TWO_TABLES_SUMMARY) in c
    assert "The paper's text was linked to 2 shared terms: crop yield, field plot." in c
    example = ("   ```yaml\n   files:\n     - file: harvests.csv\n       fields:\n         - name: plot_id\n"
               "           description: The identifier of the plot.\n           datatype: string\n"
               "           evidence_type: identifier\n           term: plot\n   ```")
    assert ", for example:\n\n" + example + "\n\n   To change a column's term, pick an id" in c
    assert ("**Checking and confirming**\nAnyone can write `@roboneuro weft check at <comment id>` to check a reply "
            "with its corrections applied; the id is the number at the end of the reply's link. " + WHO +
            " To confirm only some columns, add `fields <name>, <name>`.") in c
    assert "without `at`" not in c
    assert "nothing is published until it is confirmed." in c
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
            f"[list of terms]({tasks.weft_steps.CDN_URL}/terms/)") in c
    assert "for example" not in c


def test_the_linked_terms_are_named_by_the_labels_the_command_gives(world, monkeypatch):
    fake_draft(monkeypatch)
    linked(monkeypatch, 2, ["field plot", "`soil-depth`"])
    tasks.weft_draft_task.run(payload())
    assert "The paper's text was linked to 2 shared terms: field plot, `soil-depth`." in FakeTask.last.comment


def test_no_linked_terms_means_no_terms_sentence(world, monkeypatch):
    fake_draft(monkeypatch)
    linked(monkeypatch, 0)
    tasks.weft_draft_task.run(payload())
    assert "text was linked" not in FakeTask.last.comment


def test_a_count_with_no_labels_says_the_count(world, monkeypatch):
    fake_draft(monkeypatch)
    linked(monkeypatch, 3)
    tasks.weft_draft_task.run(payload())
    assert "\n\nThe paper's text was linked to 3 shared terms.\n\n" in FakeTask.last.comment


def test_the_confirm_comment_gives_publishs_addresses_and_the_terms(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: published(
        "1.1.0", [("crop-yield", "crop yield"), ("soil-ph", None)]))
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    keep("confirm-comment.md", FakeTask.last.comment)
    assert FakeTask.last.comment == (
        "### Description of this paper's data: confirmed\n\n"
        "Confirmed by @ada as the draft stood. The description is now version 1.1.0 and is published with the paper:\n\n"
        f"- FAIR²: {CDN}/wefts/{DOI}/fair2.json\n"
        f"- Croissant: {CDN}/wefts/{DOI}/croissant.json\n"
        f"- Metadata: {CDN}/dois/{DOI}.json\n"
        f"- Terms it uses: [crop yield]({CDN}/terms/crop-yield/), [`soil-ph`]({CDN}/terms/soil-ph/)")


# B10: the backends, the clone, failures and timeouts.

def test_a_backend_missing_from_the_config_is_one_sentence(world, monkeypatch, tmp_path):
    fake_draft(monkeypatch)
    config = tmp_path / "partial.yaml"
    config.write_text("weft:\n  backend:\n    draft: a\n    redraft: b\n")
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(config))
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The server's Evidentron config names no `terms` backend under `weft: backend:`, "
                                    "so nothing was done. Ask an editor to set it.")
    assert world.clones == []
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(tmp_path / "none.yaml"))
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert "names no `draft` backend" in FakeTask.last.failed


def test_the_backends_come_from_the_config(world, monkeypatch, tmp_path):
    seen = {}

    def draft(doi, data_dir, repo_dir, work, backends, lock, run=None):
        seen.update(backends)
        return fake_result(doi, data_dir, repo_dir, work, backends, lock, run)
    fake_draft(monkeypatch)
    fake_result = tasks.weft_steps.draft
    monkeypatch.setattr(tasks.weft_steps, "draft", draft)
    config = tmp_path / "e.yaml"
    config.write_text("weft:\n  backend:\n    draft: a\n    redraft: b\n    terms: c\n")
    monkeypatch.setattr(tasks, "EVIDENTRON_CONFIG", str(config))
    tasks.weft_draft_task.run(payload())
    assert seen == {"draft": "a", "redraft": "b", "terms": "c"}
    assert not hasattr(tasks, "DEFAULT_WEFT_BACKENDS")


def test_a_failed_clone_leaves_the_earlier_draft(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    earlier = {p.name: p.read_text() for p in world.work.iterdir()}
    world.clone_error = "fatal: could not read from remote repository"
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The repository roboneurolibre/paper-repo could not be cloned at abc1234: "
                                    "fatal: could not read from remote repository.")
    assert {p.name: p.read_text() for p in world.work.iterdir()} == earlier


def test_an_unreachable_preview_server_is_one_sentence(world, monkeypatch):
    import requests

    def down(org, repo):
        raise requests.ConnectTimeout("timed out after 20 s")
    monkeypatch.setattr(tasks, "_published_commit", down)
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == ("The published commit of roboneurolibre/paper-repo could not be read from the "
                                    "preview server: ConnectTimeout.")


def test_the_published_commit_is_read_once_with_a_timeout(monkeypatch):
    asked = []
    monkeypatch.setattr(celery_tasks.requests, "get", lambda url, timeout=None: asked.append((url, timeout))
                        or SimpleNamespace(status_code=200, text="abc1234\n"))
    assert celery_tasks._published_commit("org", "repo") == "abc1234"
    assert asked == [(f"{celery_tasks.PREVIEW_SERVER}/myst/org/repo/latest.txt", 20)]
    monkeypatch.setattr(celery_tasks.requests, "get", lambda url, timeout=None: SimpleNamespace(status_code=404, text="x"))
    assert celery_tasks._published_commit("org", "repo") is None
    source = Path(celery_tasks.__file__).read_text() + Path(tasks.__file__).read_text()
    assert source.count("/latest.txt") == 1


@pytest.mark.parametrize("error, said", [
    (RuntimeError("Not Found"), "The paper's data folder could not be found on the server: Not Found."),
    (RuntimeError("line one\nline two"), "The paper's data folder could not be found on the server."),
])
def test_a_data_folder_that_cannot_be_found_is_one_sentence(world, monkeypatch, error, said):
    def boom(client, url):
        raise error
    monkeypatch.setattr(tasks, "gh_get_project_name", boom)
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == said


def test_the_store_lock_waits_half_an_hour():
    assert tasks.weft_steps.STORE_LOCK_WAIT == 1800 and not hasattr(tasks, "STORE_LOCK_WAIT")


def test_a_cdn_rebuild_kept_waiting_says_why(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: published())

    def busy():
        raise TimeoutError("another rebuild kept the CDN busy for more than 30 minutes")
    monkeypatch.setattr(tasks.myst_cdn, "rebuild", busy)
    tasks.weft_confirm_task.run(payload(command_comment_id=100))
    assert FakeTask.last.comment.endswith("The CDN could not be rebuilt (another rebuild kept the CDN busy for more "
                                          "than 30 minutes); the documents appear after the next rebuild.")


def test_the_places_come_from_myst_cdn_and_the_config():
    assert tasks.EVIDENTRON_SITE_PATH == str(tasks.myst_cdn.EVIDENTRON_SITE)
    assert tasks.CDN_CURRENT_PATH == str(tasks.myst_cdn.CDN_CURRENT)
    assert tasks.weft_steps.CDN_URL == "https://cdn.evidencepub.io"
    assert tasks.say.TERMS_URL == tasks.weft_steps.CDN_URL + "/terms/"
    assert tasks.say.DESCRIBING_URL == tasks.weft_steps.CDN_URL + "/terms/describing/"


def test_the_tasks_keep_their_names_and_are_registered():
    for name in ("weft_draft_task", "weft_check_task", "weft_confirm_task"):
        assert getattr(tasks, name).name == f"neurolibre_celery_tasks.{name}"
        assert f"neurolibre_celery_tasks.{name}" in celery_tasks.celery_app.tasks


# B10 review.

def test_the_clone_has_a_time_limit(world, monkeypatch):
    fake_draft(monkeypatch)
    tasks.weft_draft_task.run(payload())
    assert world.clone_timeouts == [tasks.weft_steps.CLONE_TIMEOUT] == [600]


def test_the_task_limits_come_from_the_steps():
    assert tasks.weft_draft_task.soft_time_limit == tasks.weft_steps.DRAFT_SECONDS
    assert tasks.weft_draft_task.time_limit == tasks.weft_steps.DRAFT_SECONDS + tasks.HARD_MARGIN
    assert tasks.weft_confirm_task.soft_time_limit == tasks.weft_steps.CONFIRM_SECONDS
    assert tasks.weft_confirm_task.time_limit == tasks.weft_steps.CONFIRM_SECONDS + tasks.HARD_MARGIN


def test_a_github_error_reading_the_data_requirement_is_its_reason(world, monkeypatch):
    from github import GithubException

    def boom(client, url):
        raise GithubException(502, {"message": "Bad Gateway"}, {})
    monkeypatch.setattr(tasks, "gh_get_project_name", boom)
    fake_draft(monkeypatch)
    with pytest.raises(Ignore):
        tasks.weft_draft_task.run(payload())
    assert FakeTask.last.failed == "The paper's data folder could not be found on the server: 502 Bad Gateway."


def test_a_check_that_could_not_run_is_not_a_fault_of_the_description(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        failure="The check step took longer than 15 minutes and was stopped."))
    tasks.weft_check_task.run(payload(command_comment_id=100))
    assert FakeTask.last.comment == (
        "### Description of this paper's data: check\n\n"
        "The check could not be run: the check step took longer than 15 minutes and was stopped. "
        "Ask for `@roboneuro weft check` again later.")


def test_a_check_with_corrections_that_could_not_run_repeats_the_command_with_at(world, monkeypatch):
    seed_server(world)
    world.comments[200] = ("ada", "```yaml\nfiles: []\n```")
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult(
        failure="The command gave no result: ImportError: no module."))
    tasks.weft_check_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    assert c == ("### Description of this paper's data: check\n\n"
                 "The check could not be run: the command gave no result: ImportError: no module. "
                 "Ask for `@roboneuro weft check at 200` again later.")
    assert "applied" not in c and "checked as it is" not in c


def test_the_check_task_limits_come_from_its_step():
    assert tasks.weft_check_task.soft_time_limit == tasks.weft_steps.CHECK_SECONDS
    assert tasks.weft_check_task.time_limit == tasks.weft_steps.CHECK_SECONDS + tasks.HARD_MARGIN
    assert tasks.weft_steps.CHECK_SECONDS > tasks.weft_steps.TIME_LIMITS["check"]


def test_a_block_by_someone_who_cannot_confirm_is_not_offered_for_confirmation(world, monkeypatch):
    seed_server(world)
    monkeypatch.setattr(tasks.weft_steps, "check", lambda *a, **k: tasks.weft_steps.CheckResult())
    world.comments[100] = ("rita", "@roboneuro weft check\n```yaml\nfiles: []\n```")
    tasks.weft_check_task.run(payload(command_comment_id=100))
    c = FakeTask.last.comment
    assert "confirm at 100" not in c
    assert ("These corrections are by @rita, who cannot confirm them: the paper's author or the editor replies with "
            "them, then `@roboneuro weft confirm at` that reply.") in c
    world.comments[100] = ("ada", "@roboneuro weft check\n```yaml\nfiles: []\n```")
    tasks.weft_check_task.run(payload(command_comment_id=100))
    assert "For this reply, that is `@roboneuro weft confirm at 100`." in FakeTask.last.comment


def test_confirmed_but_not_kept_names_the_reply_to_confirm_again(world, monkeypatch):
    seed_server(world)
    world.comments[100] = ("eddie", "@roboneuro weft confirm at 200")
    world.comments[200] = ("ada", "```yaml\nfiles: []\n```")
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: tasks.weft_steps.ConfirmResult(
        version="1.2.0", confirmed=True, errors=["The disk is full."]))
    with pytest.raises(Ignore):
        tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    assert FakeTask.last.failed.endswith("Asking for `@roboneuro weft confirm at 200` again is safe once this is fixed.")


def test_the_terms_a_reply_proposes_are_named(world, monkeypatch):
    seed_server(world)
    world.comments[100] = ("eddie", "@roboneuro weft confirm at 200")
    world.comments[200] = ("ada", "```yaml\nfiles: []\nnew_terms:\n  leaf-area: {label: leaf area}\n  soil-ph: {}\n```")
    monkeypatch.setattr(tasks.weft_steps, "confirm", lambda *a, **k: published("1.1.0", [("soil-ph", "soil pH")]))
    tasks.weft_confirm_task.run(payload(command_comment_id=100, at=200))
    c = FakeTask.last.comment
    assert c.endswith(f"- Terms it uses: [soil pH]({CDN}/terms/soil-ph/)\n- Terms it proposes: `leaf-area` (leaf area)")
