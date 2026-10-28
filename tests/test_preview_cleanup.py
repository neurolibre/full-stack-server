"""Cleaning up after a paper's technical screening on the preview server: the author's repository keeps its last
successful build, and the images pulled for it go. Folders are made in a temporary directory and Docker is faked."""

from types import SimpleNamespace

import pytest

import preview_cleanup

OLD, MIDDLE, LAST = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b", "2b3c4d5", "3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d"


def repository(root, owner="someone", repo="seed-dispersal", latest=LAST, builds=(OLD, MIDDLE, LAST)):
    folder = root / owner / repo
    for commit in builds:
        (folder / commit / "_build" / "html").mkdir(parents=True)
        (folder / commit / "_build" / "html" / "index.html").write_text("x" * 1000)
        (folder / f"{commit}.tar.gz").write_bytes(b"y" * 500)
    (folder / "latest" / "_build").mkdir(parents=True)
    (folder / "latest" / "_build" / "cache.db").write_bytes(b"z" * 2000)
    if latest is not None:
        (folder / "latest.txt").write_text(latest + "\n")
    return folder


def test_keeps_the_last_successful_build_and_removes_the_rest(tmp_path):
    folder = repository(tmp_path)
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    assert sorted(p.name for p in folder.iterdir()) == sorted([LAST, f"{LAST}.tar.gz", "latest.txt"])
    assert (folder / LAST / "_build" / "html" / "index.html").exists()
    assert sorted(report.removed) == sorted([OLD, f"{OLD}.tar.gz", MIDDLE, f"{MIDDLE}.tar.gz", "latest"])
    assert report.freed == 2 * 1500 + 2000
    assert report.problems == []


def test_a_build_asked_for_is_kept_as_well(tmp_path):
    folder = repository(tmp_path)
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal", keep=[MIDDLE])
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [LAST, f"{LAST}.tar.gz", MIDDLE, f"{MIDDLE}.tar.gz", "latest.txt"])
    assert report.notes == []


def test_a_build_asked_for_that_is_not_there_is_said(tmp_path):
    repository(tmp_path)
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal", keep=["abcdef0"])
    assert report.notes == ["There is no build of commit abcdef0 to keep."]


def test_running_it_twice_removes_nothing_the_second_time(tmp_path):
    repository(tmp_path)
    preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    assert report.removed == [] and report.freed == 0 and report.problems == []


def test_without_a_successful_build_nothing_is_removed(tmp_path):
    folder = repository(tmp_path, latest=None)
    before = sorted(p.name for p in folder.iterdir())
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    assert sorted(p.name for p in folder.iterdir()) == before
    assert report.removed == []
    assert report.problems == ["No successful build is recorded for someone/seed-dispersal (no latest.txt), "
                               "so its builds were left as they are."]


def test_a_recorded_build_that_is_missing_stops_the_removal(tmp_path):
    folder = repository(tmp_path, latest="9f9f9f9", builds=(OLD,))
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    assert (folder / OLD).exists() and (folder / "latest").exists()
    assert report.problems == ["latest.txt names the build 9f9f9f9, which is not on the server, so the builds of "
                               "someone/seed-dispersal were left as they are."]


def test_other_files_in_the_folder_are_left_and_counted(tmp_path):
    folder = repository(tmp_path, builds=(LAST,))
    (folder / "notes.md").write_text("kept")
    (folder / "drafts").mkdir()
    report = preview_cleanup.clean_builds(tmp_path, "someone", "seed-dispersal")
    assert (folder / "notes.md").exists() and (folder / "drafts").exists()
    assert report.notes == ["2 other entries in the folder are not builds and were left as they are."]


def test_a_repository_with_no_folder_says_so(tmp_path):
    report = preview_cleanup.clean_builds(tmp_path, "someone", "never-built")
    assert report.removed == []
    assert report.notes == ["There are no MyST builds of someone/never-built on the preview server."]


def test_owner_and_repository_cannot_leave_the_builds_folder(tmp_path):
    with pytest.raises(ValueError):
        preview_cleanup.clean_builds(tmp_path, "..", "elsewhere")


class Images:
    """The images of a fake Docker host: a list of tagged images, recording what is removed."""

    def __init__(self, *tagged, refuse=()):
        self.items = [SimpleNamespace(id=f"sha256:{i}", tags=tags, attrs={"Size": 100 * (i + 1)})
                      for i, tags in enumerate(tagged)]
        self.removed, self.refuse = [], set(refuse)

    def list(self):
        return list(self.items)

    def remove(self, image, force=False):
        if image in self.refuse:
            raise RuntimeError("conflict: unable to remove repository reference (must force) - container abc is using it")
        self.removed.append(image)


def docker_with(images):
    return SimpleNamespace(images=images)


REGISTRY = "registry.example.org"


def test_removes_the_images_pulled_for_the_repository_and_no_other():
    images = Images(
        [f"{REGISTRY}/binder-someone-2dseed-2ddispersal-4b1c9e:abc123", f"{REGISTRY}/binder-someone-2dseed-2ddispersal-4b1c9e:def456"],
        [f"{REGISTRY}/binder-someone-2dseed-2ddispersal-2dextended-77aa01:abc"],
        [f"{REGISTRY}/binder-roboneurolibre-2dseed-2ddispersal-0c0c0c:latest"],
        [f"{REGISTRY}:5000/binder-someone-2dseed-2ddispersal-aa11bb:1"],
        ["busybox:latest"],
        [],
    )
    report = preview_cleanup.remove_images(docker_with(images), "someone", "seed-dispersal")
    assert images.removed == ["sha256:0", "sha256:3"]
    assert report.freed == 100 + 400
    assert len(report.removed) == 2 and report.problems == []


def test_an_image_in_use_is_kept_with_one_sentence():
    images = Images([f"{REGISTRY}/binder-someone-2dseed-2ddispersal-4b1c9e:abc"], refuse={"sha256:0"})
    report = preview_cleanup.remove_images(docker_with(images), "someone", "seed-dispersal")
    assert images.removed == []
    assert report.problems == [f"The image {REGISTRY}/binder-someone-2dseed-2ddispersal-4b1c9e:abc could not be "
                               "removed, most likely because a container still uses it."]


def test_a_long_repository_name_matches_the_name_binderhub_shortens():
    from myst_libre.utils.naming import BinderHubNaming
    repo = "a-very-long-repository-name-for-a-study-of-river-sediment-transport-over-decades"
    exact = BinderHubNaming.build_exact_image_name(f"someone/{repo}").lower()
    images = Images([f"{REGISTRY}/{exact}:abc"])
    preview_cleanup.remove_images(docker_with(images), "someone", repo)
    assert images.removed == ["sha256:0"]


# The task that runs the cleanup for a screening issue and says on it what was done.

import neurolibre_celery_tasks as celery_tasks
from task_outcome import TaskFailed
from conftest import FakeTask


class Lock:
    held = False

    def __init__(self, key, timeout):
        self.key = key

    def acquire(self, blocking=True):
        return not Lock.held

    def release(self):
        pass


@pytest.fixture
def preview(tmp_path, monkeypatch):
    """A preview server with paper-repo built three times, and two images pulled for it."""
    monkeypatch.setattr(celery_tasks, "BaseNeuroLibreTask", FakeTask)
    monkeypatch.setattr(celery_tasks, "DATA_ROOT_PATH", str(tmp_path))
    monkeypatch.setattr(celery_tasks, "MYST_FOLDER", "myst")
    monkeypatch.setattr(celery_tasks._lock_redis, "lock", Lock)
    Lock.held = False
    folder = repository(tmp_path / "myst", owner="someone", repo="paper-repo")
    images = Images([f"{REGISTRY}/binder-someone-2dpaper-2drepo-1f2e3d:abc"],
                    [f"{REGISTRY}/binder-roboneurolibre-2dpaper-2drepo-0a0b0c:latest"])
    monkeypatch.setattr(celery_tasks, "_docker_client", lambda: docker_with(images))
    return SimpleNamespace(folder=folder, images=images)


def run(owner="someone", commit_hash=None):
    FakeTask.owner_override = owner
    celery_tasks.preview_cleanup_task.run(dict(issue_id=52, target_repo_url=f"https://github.com/{owner}/paper-repo",
                                               commit_hash=commit_hash))
    return FakeTask.last


@pytest.fixture(autouse=True)
def owner_from_url(monkeypatch):
    """FakeTask names every repository someone/paper-repo; here the owner is the one the test asks for."""
    original = FakeTask.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        self.owner_name = getattr(FakeTask, "owner_override", "someone")

    monkeypatch.setattr(FakeTask, "__init__", init)


def test_the_task_keeps_the_last_build_and_says_what_it_removed(preview):
    task = run()
    assert sorted(p.name for p in preview.folder.iterdir()) == sorted([LAST, f"{LAST}.tar.gz", "latest.txt"])
    assert preview.images.removed == ["sha256:0"]
    assert f"The build of commit {LAST[:7]} is kept" in task.comment
    assert "2 earlier builds" in task.comment
    assert "1 image" in task.comment
    assert "The paper's data and the build logs are kept" in task.comment
    assert task.failed is None


def test_the_task_keeps_the_commit_it_is_given(preview):
    task = run(commit_hash=MIDDLE)
    assert (preview.folder / MIDDLE).exists() and (preview.folder / f"{MIDDLE}.tar.gz").exists()
    assert f"The build of commit {MIDDLE[:7]}, asked for, is kept as well." in task.comment
    assert "1 earlier build was removed" in task.comment


def test_a_repository_name_that_leaves_the_folder_is_refused(preview):
    FakeTask.owner_override = ".."
    with pytest.raises(TaskFailed):
        celery_tasks.preview_cleanup_task.run(dict(issue_id=52, target_repo_url="https://github.com/x/y"))
    assert FakeTask.last.failed.endswith("is not a repository name.")


def test_the_journals_copies_are_never_cleaned(preview):
    with pytest.raises(TaskFailed):
        run(owner=celery_tasks.GH_ORGANIZATION)
    assert FakeTask.last.failed == (f"The builds of the journal's copies ({celery_tasks.GH_ORGANIZATION}) are all "
                                    f"kept; the cleanup is for the author's repository.")
    assert preview.images.removed == []


def test_a_build_in_progress_stops_the_cleanup(preview):
    Lock.held = True
    with pytest.raises(TaskFailed):
        run()
    assert FakeTask.last.failed == ("A MyST build of someone/paper-repo is in progress; run the cleanup again "
                                    "once it has finished.")
    assert (preview.folder / OLD).exists()


def test_without_docker_the_builds_are_still_cleaned(preview, monkeypatch):
    def unreachable():
        raise ConnectionError("socket")
    monkeypatch.setattr(celery_tasks, "_docker_client", unreachable)
    task = run()
    assert not (preview.folder / OLD).exists()
    assert "Docker could not be reached on the preview server, so no image was removed." in task.comment


# The endpoint the screening bot calls.

import os

import screening_client


@pytest.fixture
def preview_api(monkeypatch):
    here = os.getcwd()
    os.chdir(os.path.join(os.path.dirname(__file__), "..", "api"))
    try:
        import neurolibre_preview_api as api
    finally:
        os.chdir(here)
    monkeypatch.setattr(api.preview_api.htpasswd, "required",
                        lambda f: (lambda *a, **k: f(*a, user="tester", **k)))
    monkeypatch.setattr(screening_client, "Github",
                        lambda token=None: SimpleNamespace(get_repo=lambda name: SimpleNamespace(full_name=name)))
    sent = []
    monkeypatch.setattr(api.preview_cleanup_task, "apply_async",
                        lambda args=None, **kw: sent.append(args[0]) or SimpleNamespace(task_id="id-cleanup"))
    api.app.config["TESTING"] = True
    return SimpleNamespace(client=api.app.test_client(), sent=sent)


def test_the_endpoint_starts_the_cleanup_for_an_issue(preview_api):
    r = preview_api.client.post("/api/myst/cleanup", json={"id": 52, "repository_url": "https://github.com/someone/paper-repo"})
    assert r.status_code == 200 and "id-cleanup" in r.get_data(as_text=True)
    assert preview_api.sent[0]["issue_id"] == 52
    assert preview_api.sent[0]["target_repo_url"] == "https://github.com/someone/paper-repo"


def test_the_endpoint_passes_a_commit_to_keep(preview_api):
    preview_api.client.post("/api/myst/cleanup", json={"id": 52, "repository_url": "https://github.com/someone/paper-repo",
                                                       "commit_hash": "2b3c4d5"})
    assert preview_api.sent[0]["commit_hash"] == "2b3c4d5"


def test_the_endpoint_needs_the_issue(preview_api):
    r = preview_api.client.post("/api/myst/cleanup", json={"repository_url": "https://github.com/someone/paper-repo"})
    assert r.status_code == 422 and preview_api.sent == []


# Through Celery with the real BaseNeuroLibreTask: Flower shows the sentence of a failure, and the comment of a
# success as the task's result.

BASE_TASK = celery_tasks.BaseNeuroLibreTask


@pytest.fixture
def real_task(preview, monkeypatch):
    posted = []
    respond = SimpleNamespace(STARTED=lambda m="": posted.append(("started", m)),
                              FAILURE=lambda m, collapsable=True: posted.append(("failure", m)),
                              SUCCESS=lambda m, collapsable=True: posted.append(("success", m)))

    def screening(d):
        return SimpleNamespace(issue_id=d["issue_id"], target_repo_url=d["target_repo_url"], task_id=None,
                               commit_hash=d.get("commit_hash"), respond=respond, email_address=None)
    monkeypatch.setattr(celery_tasks, "BaseNeuroLibreTask", BASE_TASK)
    monkeypatch.setattr(celery_tasks.ScreeningClient, "from_dict", staticmethod(screening))
    monkeypatch.setattr(celery_tasks.preview_cleanup_task, "update_state", lambda *a, **k: None)
    return posted


def send(owner):
    return celery_tasks.preview_cleanup_task.apply(args=[dict(
        issue_id=52, target_repo_url=f"https://github.com/{owner}/paper-repo")])


def test_a_failed_cleanup_is_a_failure_with_its_sentence(real_task):
    result = send(celery_tasks.GH_ORGANIZATION)
    assert result.state == "FAILURE"
    assert str(result.result).startswith("The builds of the journal's copies")
    assert real_task[-1][0] == "failure"


def test_a_cleanup_has_its_comment_as_its_result(real_task, preview):
    result = send("someone")
    assert result.state == "SUCCESS"
    assert result.result.startswith("🧹 Preview cleanup of someone/paper-repo")
    assert real_task[-1] == ("success", result.result)
