"""What publishing leaves behind on the preprint server, removed once Zenodo has the published copies: the
archives of a paper, after its records are published, and the paper's image, once it is saved in its archive.
Folders are made in a temporary directory and Docker is faked."""

import subprocess
from types import SimpleNamespace

import pytest

import zenodo_cleanup

ARCHIVES = ["DockerImage_10.55458_NeuroLibre_00052_a6acc9.tar.gz", "GitHubRepo_10.55458_NeuroLibre_00052_a6acc9.zip",
            "LivingPreprint_10.55458_NeuroLibre_00052_a6acc9.zip"]


def archive_folder(root):
    folder = root / "zenodo" / "00052"
    folder.mkdir(parents=True)
    for name in ARCHIVES:
        (folder / name).write_bytes(b"x" * 1000)
    return folder


def test_removes_the_archives_and_their_folder(tmp_path):
    folder = archive_folder(tmp_path)
    report = zenodo_cleanup.remove_archives(folder)
    assert not folder.exists()
    assert sorted(report.removed) == sorted(ARCHIVES)
    assert report.freed == 3000


def test_a_file_that_is_not_an_archive_is_kept_with_its_folder(tmp_path):
    folder = archive_folder(tmp_path)
    (folder / "notes.txt").write_text("kept")
    report = zenodo_cleanup.remove_archives(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]
    assert len(report.removed) == 3


def test_a_missing_folder_removes_nothing(tmp_path):
    report = zenodo_cleanup.remove_archives(tmp_path / "zenodo" / "00099")
    assert report.removed == [] and report.freed == 0


def test_the_sentence_says_what_was_removed(tmp_path):
    report = zenodo_cleanup.remove_archives(archive_folder(tmp_path))
    assert zenodo_cleanup.archives_sentence(report) == (
        "The 3 archives were removed from the server (2.9 KB); Zenodo keeps the published copies.")
    assert zenodo_cleanup.archives_sentence(zenodo_cleanup.Report()) is None


def docker(returncode=0, stderr=""):
    calls = []

    def run(command, **kw):
        calls.append(command)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return run, calls


def test_removes_the_saved_image():
    run, calls = docker()
    assert zenodo_cleanup.remove_image("registry.example.org/binder-a-2db-1f2e3d:abc", run=run) is None
    assert calls == [["docker", "image", "rm", "registry.example.org/binder-a-2db-1f2e3d:abc"]]


def test_an_image_that_cannot_be_removed_is_one_sentence():
    run, _ = docker(returncode=1, stderr="Error response from daemon: conflict: unable to remove (must force)\n")
    assert zenodo_cleanup.remove_image("img:tag", run=run) == (
        "The image img:tag could not be removed from the server: Error response from daemon: conflict: unable to "
        "remove (must force).")


def test_docker_missing_is_one_sentence():
    def run(command, **kw):
        raise FileNotFoundError("docker")
    assert zenodo_cleanup.remove_image("img:tag", run=run) == (
        "The image img:tag could not be removed from the server: docker was not found.")


# The publish task removes the paper's archives only once every record is published.

import neurolibre_celery_tasks as celery_tasks
from conftest import FakeTask


@pytest.fixture
def zenodo(tmp_path, monkeypatch):
    folder = archive_folder(tmp_path)
    said, comments = [], []
    published = {"after": True}
    monkeypatch.setattr(celery_tasks, "Github", lambda token=None: SimpleNamespace())
    monkeypatch.setattr(celery_tasks, "get_archive_dir", lambda issue_id: str(folder))
    monkeypatch.setattr(celery_tasks, "get_gpt_response", lambda prompt: "")
    monkeypatch.setattr(celery_tasks, "zenodo_collect_dois", lambda issue_id: {"book": "10.5281/zenodo.1"})
    monkeypatch.setattr(celery_tasks, "zenodo_publish", lambda issue_id: ["published book"])
    monkeypatch.setattr(celery_tasks, "gh_create_comment", lambda client, repo, issue, text: comments.append(text))
    monkeypatch.setattr(celery_tasks, "gh_template_respond",
                        lambda client, state, title, repo, issue, task_id=None, comment_id=None, msg="", *a, **k:
                        said.append((state, msg)))
    calls = []

    def status(issue_id, kind):
        calls.append(kind)
        if len(calls) == 1:
            return [published.get("before", False), "0 of 3"]
        return [published["after"], "2 of 3"]
    monkeypatch.setattr(celery_tasks, "zenodo_confirm_status", status)
    return SimpleNamespace(folder=folder, said=said, comments=comments, published=published)


def publish():
    return celery_tasks.zenodo_publish_task.apply(args=[dict(
        issue_id=52, comment_id=1, review_repository="journal/reviews", task_title="Publish")])


def test_publishing_removes_the_archives(zenodo):
    assert publish().state == "SUCCESS"
    assert not zenodo.folder.exists()
    assert "The 3 archives were removed from the server" in zenodo.comments[-1]


def test_archives_stay_when_a_record_is_not_published(zenodo):
    zenodo.published["after"] = False
    assert publish().state == "FAILURE"
    assert sorted(p.name for p in zenodo.folder.iterdir()) == sorted(ARCHIVES)


def test_archives_of_a_paper_published_earlier_are_removed(zenodo):
    zenodo.published["before"] = True
    publish()
    assert not zenodo.folder.exists()
    assert any("The 3 archives were removed" in msg for state, msg in zenodo.said if state == "exists")


# The Docker task removes the paper's image once it is saved in its archive, and only then.

class DockerTask(FakeTask):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.screening.bucket_url = "https://zenodo.org/api/files/bucket"
        self.provider_name = "github.com"

    def get_dotenv_path(self):
        return "."


@pytest.fixture
def image(tmp_path, monkeypatch):
    removed = []
    saved = {"ok": True}
    monkeypatch.setattr(celery_tasks, "BaseNeuroLibreTask", DockerTask)
    monkeypatch.setattr(celery_tasks, "format_commit_hash", lambda url, ref: "a6acc98f27")
    monkeypatch.setattr(celery_tasks, "get_archive_dir", lambda issue_id: str(tmp_path))
    monkeypatch.setattr(celery_tasks, "get_deposit_dir", lambda issue_id: str(tmp_path))
    monkeypatch.setattr(celery_tasks, "REES", lambda config: SimpleNamespace(
        found_image_name="registry.example.org/binder-roboneurolibre-2dpaper-2drepo-9b12c3", pull_image=lambda: None))
    def save(name, issue_id, commit):
        saved["name"] = name
        if saved["ok"]:
            return {"status": True}, str(tmp_path / "DockerImage.tar.gz")
        return {"status": False, "message": "no space left on device"}, None
    monkeypatch.setattr(celery_tasks, "docker_save", save)
    monkeypatch.setattr(celery_tasks, "zenodo_upload_item", lambda *a: SimpleNamespace(
        status_code=201, json=lambda: {"key": "DockerImage.tar.gz"}, __class__=celery_tasks.requests.Response))
    monkeypatch.setattr(celery_tasks.zenodo_cleanup, "remove_image", lambda name: removed.append(name))
    return SimpleNamespace(removed=removed, saved=saved)


def archive_docker():
    celery_tasks.zenodo_upload_docker_task.run(dict(issue_id=52, target_repo_url="https://github.com/someone/paper-repo"))
    return FakeTask.last


def test_the_image_is_removed_once_saved(image):
    task = archive_docker()
    assert image.removed == [image.saved["name"]]
    assert image.removed[0].endswith("/registry.example.org/binder-roboneurolibre-2dpaper-2drepo-9b12c3:a6acc98f27")
    assert task.failed is None


def test_an_image_that_was_not_saved_is_kept(image):
    from task_outcome import TaskFailed
    image.saved["ok"] = False
    with pytest.raises(TaskFailed):
        archive_docker()
    assert image.removed == []
