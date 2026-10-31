"""The data caching task (preview_download_data) with GitHub, repo2data, rsync and email faked: a dataset added later
is downloaded without asking for overwrite, every dataset is copied to its own place on the BinderHub share, and a
folder holding something else is not replaced unless asked."""

import json
import smtplib
from types import SimpleNamespace

import pytest

import neurolibre_celery_tasks as celery_tasks
import screening_client

MANIFEST = {
    "a": {"src": "https://example.org/a.zip", "dst": "../data", "projectName": "book/A-one"},
    "b": {"src": "https://example.org/b.zip", "dst": "../data", "projectName": "book/b"},
}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    data, nfs = tmp_path / "DATA", tmp_path / "NFS"
    data.mkdir()
    emails, synced, installed = [], [], []

    def no_smtp(*a, **k):
        raise AssertionError("a test tried to send an email")
    monkeypatch.setattr(smtplib, "SMTP", no_smtp)
    monkeypatch.setattr(screening_client, "send_email", lambda to, subject, body: None)
    monkeypatch.setattr(celery_tasks, "send_email", lambda to, subject, body: emails.append(body))
    repo = SimpleNamespace(get_contents=lambda path: SimpleNamespace(decoded_content=json.dumps(MANIFEST).encode()))
    monkeypatch.setattr(screening_client, "Github", lambda token=None: SimpleNamespace(get_repo=lambda name: repo))
    monkeypatch.setattr(celery_tasks, "DATA_ROOT_PATH", str(data))
    monkeypatch.setattr(celery_tasks, "DATA_NFS_PATH", str(nfs))
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "join_data_root_path",
                        lambda self, *parts: str(data.joinpath(*parts)))

    class FakeRepo2Data:
        def __init__(self, json_path, server=False):
            self.manifest = json.load(open(json_path))

        def set_server_dst_folder(self, folder):
            self.folder = folder

        def install(self):
            paths = []
            for key, entry in self.manifest.items():
                folder = data / entry["projectName"]
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{key}.mat").write_text("x")
                paths.append(str(folder))
            installed.append(paths)
            return paths
    monkeypatch.setattr(celery_tasks, "Repo2Data", FakeRepo2Data)
    monkeypatch.setattr(celery_tasks, "local_to_nfs",
                        lambda source, destination: synced.append((source, destination)) or (True, ""))
    monkeypatch.setattr(celery_tasks.preview_download_data, "update_state", lambda *a, **k: None)

    def run(is_overwrite=None):
        screening = screening_client.ScreeningClient(
            task_name="DOWNLOAD (CACHE) DATA", target_repo_url="https://github.com/someone/book",
            email="editor@example.org", is_overwrite=is_overwrite)
        celery_tasks.preview_download_data.push_request(id="25c73a15-a292-463e-9e8c-5fda511e5d82")
        try:
            celery_tasks.preview_download_data.run(screening.to_dict())
        finally:
            celery_tasks.preview_download_data.pop_request()

    return SimpleNamespace(run=run, data=data, nfs=nfs, emails=emails, synced=synced, installed=installed)


def record(cache, key, entry):
    folder = cache.data / entry["projectName"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}_repo2data_cache_record.json").write_text(json.dumps(entry))


def test_a_dataset_added_later_is_downloaded_without_asking_for_overwrite(cache):
    record(cache, "a", MANIFEST["a"])
    cache.run()
    assert cache.installed, "repo2data was not run"
    assert "book/b" in cache.emails[-1]


def test_every_dataset_goes_to_its_own_place_on_the_share(cache):
    cache.run()
    assert sorted(cache.synced) == sorted([
        (str(cache.data / "book" / "A-one"), str(cache.nfs / "book")),
        (str(cache.data / "book" / "b"), str(cache.nfs / "book")),
    ])
    assert (cache.nfs / "book").is_dir()


def test_a_folder_holding_something_else_is_not_replaced(cache):
    record(cache, "a", dict(MANIFEST["a"], src="https://example.org/another-paper.zip"))
    cache.run()
    assert cache.installed == [] and cache.synced == []
    assert "book/A-one" in cache.emails[-1] and "overwrite" in cache.emails[-1]


def test_overwrite_replaces_it(cache):
    record(cache, "a", dict(MANIFEST["a"], src="https://example.org/another-paper.zip"))
    cache.run(is_overwrite=True)
    assert cache.installed and len(cache.synced) == 2


def test_an_email_request_without_an_issue_succeeds(cache, monkeypatch):
    monkeypatch.setattr(screening_client.ScreeningClient, "gh_create_comment",
                        lambda self, *a, **k: pytest.fail("commented on an issue that does not exist"))
    cache.run()
    assert "Data download has failed" not in cache.emails[-1]
