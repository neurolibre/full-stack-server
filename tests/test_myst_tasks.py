"""The two tasks that write a paper's myst.yml in the journal's fork: starting production, and syncing a new
version from upstream. GitHub is faked; nothing touches the network."""

import base64
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore
from github import UnknownObjectException
from ruamel.yaml import YAML

import neurolibre_celery_tasks as celery_tasks
from conftest import FakeTask

AUTHOR_MYST = """\
# the author's own comment
project:
  title: 'Seed dispersal by ants'
  jupyter:
    binder:
      url: https://preview-binder.example
      repo: someone/ant-seeds
      ref: main
  license:
    code: MIT
site:
  template: book-theme
"""


def load(text):
    return YAML(typ="safe").load(text)


class Repo:
    """A repository with one file per branch, recording every write."""

    def __init__(self, full_name, files):
        self.full_name, self.files, self.writes, self.pulls = full_name, dict(files), [], []

    def get_contents(self, path, ref="main"):
        if (ref, path) not in self.files:
            raise UnknownObjectException(404, {"message": "Not Found"}, {})
        text = self.files[(ref, path)]
        return SimpleNamespace(decoded_content=text.encode(), sha=f"sha-{ref}", path=path)

    def update_file(self, path, message, content, sha, branch="main"):
        self.writes.append((branch, path))
        self.files[(branch, path)] = content

    def create_git_ref(self, ref, sha):
        self.files.update({(ref.split("/")[-1], p): t for (b, p), t in UPSTREAM.files.items()})

    def create_pull(self, title, body, head, base):
        self.pulls.append(body)
        return SimpleNamespace(html_url="https://github.com/pull/1", number=1, mergeable_state="clean")

    def get_branch(self, name):
        return SimpleNamespace(commit=SimpleNamespace(sha="upstream-sha"))


UPSTREAM = Repo("someone/ant-seeds", {})


@pytest.fixture
def journal(monkeypatch, tmp_path):
    """The fork and the upstream repository, and the helpers the production step calls, faked."""
    fork = Repo("journal-org/ant-seeds", {})
    UPSTREAM.files = {("main", "myst.yml"): AUTHOR_MYST}
    said = []
    client = SimpleNamespace(get_repo=lambda name: fork if name == fork.full_name else UPSTREAM)
    monkeypatch.setattr(celery_tasks, "Github", lambda token=None: client)
    monkeypatch.setattr(celery_tasks, "gh_forkify_it", lambda name: fork.full_name)
    monkeypatch.setattr(celery_tasks, "get_test_book_build", lambda *a: {"status": True, "book_url": "u"})
    monkeypatch.setattr(celery_tasks, "get_deposit_dir", lambda issue_id: str(tmp_path))
    monkeypatch.setattr(celery_tasks, "get_repository_license",
                        lambda client, url: {"status": False, "license": None, "message": "none"})
    monkeypatch.setattr(celery_tasks, "gh_get_jb_config", lambda *a: {})
    monkeypatch.setattr(celery_tasks, "gh_get_jb_toc", lambda *a: {})
    monkeypatch.setattr(celery_tasks, "gh_create_file", lambda *a, **k: {"status": True})
    monkeypatch.setattr(celery_tasks, "gh_read_from_issue_body", lambda *a: "N/A")
    monkeypatch.setattr(celery_tasks, "gh_template_respond",
                        lambda client, state, title, repo, issue, task_id=None, comment_id=None, msg="", *a, **k:
                        said.append((state, msg)))
    monkeypatch.setattr(celery_tasks, "gh_get_file_content",
                        lambda client, repo, path: fork.files.get(("main", path)))

    def update(client, repo, path, text, message):
        fork.update_file(path, message, text, "sha", branch="main")
        return {"status": True, "message": "Success"}
    monkeypatch.setattr(celery_tasks, "gh_update_file_content", update)
    for task in (celery_tasks.fork_configure_repository_task, celery_tasks.sync_fork_from_upstream_task):
        monkeypatch.setattr(task, "update_state", lambda *a, **k: None)
    return SimpleNamespace(fork=fork, said=said, client=client)


def start_production(issue_id=77):
    celery_tasks.fork_configure_repository_task.run(dict(
        issue_id=issue_id, comment_id=1, review_repository="journal/reviews",
        repository_url="https://github.com/someone/ant-seeds", task_title="t", commit_hash="abc"))


def test_production_points_live_code_at_the_fork_and_keeps_the_authors_file(journal):
    journal.fork.files[("main", "myst.yml")] = AUTHOR_MYST
    start_production()
    text = journal.fork.files[("main", "myst.yml")]
    assert text.startswith("# the author's own comment\n")
    project = load(text)["project"]
    assert project["jupyter"]["binder"] == {"url": celery_tasks.PRODUCTION_BINDERHUB,
                                            "repo": "journal-org/ant-seeds", "ref": "main"}
    assert project["license"] == {"code": "MIT", "content": celery_tasks.PUBLISH_LICENSE}
    assert journal.said[-1][0] == "success"


def test_starting_production_again_writes_nothing(journal):
    journal.fork.files[("main", "myst.yml")] = AUTHOR_MYST
    start_production()
    start_production()
    assert journal.fork.writes == [("main", "myst.yml")]
    assert load(journal.fork.files[("main", "myst.yml")])["site"]["actions"] == [
        {"title": "🏠 Home", "url": f"https://doi.org/{celery_tasks.DOI_PREFIX}/{celery_tasks.DOI_SUFFIX}.00077"}]


def test_a_myst_yml_production_cannot_read_fails_with_one_sentence(journal):
    journal.fork.files[("main", "myst.yml")] = "site:\n  template: book-theme\n"
    start_production()
    state, message = journal.said[-1]
    assert state == "failure"
    assert message == "Could not configure myst.yml for journal-org/ant-seeds: myst.yml has no `project` section."
    assert journal.fork.writes == []


class SyncTask(FakeTask):
    client = None

    def __init__(self, celery_task, screening=None, payload=None):
        super().__init__(celery_task, screening)
        self.screening.preprint_version = "v2"
        self.screening.github_client = SyncTask.client


@pytest.fixture
def sync(journal, monkeypatch):
    monkeypatch.setattr(celery_tasks, "BaseNeuroLibreTask", SyncTask)
    monkeypatch.setattr(SyncTask, "client", journal.client)
    monkeypatch.setattr(celery_tasks, "load_txt_file", lambda path: "{upstream_repo_name} {preprint_version}")
    return journal


def run_sync():
    celery_tasks.sync_fork_from_upstream_task.run(dict(issue_id=77, target_repo_url="https://github.com/someone/ant-seeds"))


def test_a_new_version_brings_the_authors_myst_changes_configured_for_production(sync):
    sync.fork.files[("main", "myst.yml")] = AUTHOR_MYST
    start_production()
    UPSTREAM.files[("main", "myst.yml")] = AUTHOR_MYST.replace("  license:", "  keywords:\n    - myrmecochory\n  license:")
    run_sync()
    synced = load(sync.fork.files[("sync-upstream-v2", "myst.yml")])["project"]
    assert synced["keywords"] == ["myrmecochory"]
    assert synced["jupyter"]["binder"]["repo"] == "journal-org/ant-seeds"
    body = sync.fork.pulls[-1]
    assert "- `project.keywords`: (absent) → `['myrmecochory']`" in body
    assert "jupyter" not in body
    assert FakeTask.last.comment.startswith("✅")


def test_a_hand_edit_in_the_fork_that_merging_undoes_is_listed(sync):
    sync.fork.files[("main", "myst.yml")] = AUTHOR_MYST
    start_production()
    sync.fork.files[("main", "myst.yml")] = sync.fork.files[("main", "myst.yml")].replace(
        "  title: 'Seed dispersal by ants'", "  title: 'Seed dispersal by ants'\n  short_title: Ants")
    run_sync()
    assert "- `project.short_title`: `Ants` → (absent)" in sync.fork.pulls[-1]


def test_an_upstream_myst_yml_production_cannot_read_fails_the_sync_once(sync):
    UPSTREAM.files[("main", "myst.yml")] = "- not\n- settings\n"
    with pytest.raises(Ignore):
        run_sync()
    assert FakeTask.last.failed == ("⛔️ Could not configure the upstream myst.yml for production: "
                                    "myst.yml is not a mapping of settings.")
    assert sync.fork.pulls == []


def test_a_new_version_keeps_the_banner_production_chose(sync, monkeypatch):
    article = AUTHOR_MYST.replace("template: book-theme", "template: article-theme")
    sync.fork.files[("main", "myst.yml")] = article
    UPSTREAM.files[("main", "myst.yml")] = article
    banners = iter(range(1, 100))
    monkeypatch.setattr(celery_tasks.random, "randint", lambda a, b: next(banners))
    start_production()
    chosen = load(sync.fork.files[("main", "myst.yml")])["project"]["banner"]
    run_sync()
    assert load(sync.fork.files[("sync-upstream-v2", "myst.yml")])["project"]["banner"] == chosen
    assert "banner" not in sync.fork.pulls[-1]


def test_an_upstream_without_myst_yml_says_merging_removes_the_forks(sync):
    sync.fork.files[("main", "myst.yml")] = AUTHOR_MYST
    del UPSTREAM.files[("main", "myst.yml")]
    run_sync()
    assert "merging removes the fork's `myst.yml`" in sync.fork.pulls[-1]
