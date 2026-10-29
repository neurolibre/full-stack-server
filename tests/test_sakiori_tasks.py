"""Sakiori's build as a task: asked for through the endpoint, or after a paper is published to the CDN; a failed
crawl is a failed task, and the notes or answers that were not written are said in its result."""

from types import SimpleNamespace

import pytest

import neurolibre_preprint_api as api
import sakiori
import sakiori_tasks
from task_outcome import TaskFailed


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.preprint_api.htpasswd, "required", lambda f: (lambda *a, **k: f(*a, user="tester", **k)))
    api.app.config["TESTING"] = True
    return api.app.test_client()


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(sakiori_tasks.sakiori_build_task, "delay",
                        lambda papers: calls.append(papers) or SimpleNamespace(id="task-1"))
    return calls


def test_the_endpoint_queues_one_paper(client, queued):
    response = client.post("/api/sakiori/build", json={"id": 52})
    assert response.status_code == 202
    assert queued == [["00052"]]
    assert "task-1" in response.get_data(as_text=True) and "https://sakiori.evidencepub.io/00052" in response.get_data(as_text=True)


def test_the_endpoint_without_a_paper_queues_the_whole_collection(client, queued):
    assert client.post("/api/sakiori/build", json={}).status_code == 202
    assert queued == [[]]


def test_publishing_a_paper_queues_its_walk_with_a_line_for_the_comment(queued):
    line = sakiori_tasks.queue("neurolibre.00052")
    assert queued == [["00052"]]
    assert "https://sakiori.evidencepub.io/00052" in line


def test_a_walk_that_cannot_be_queued_is_said_not_raised(monkeypatch):
    def broken(papers):
        raise ConnectionError("redis is down")
    monkeypatch.setattr(sakiori_tasks.sakiori_build_task, "delay", broken)
    assert "redis is down" in sakiori_tasks.queue("neurolibre.00052")


def test_a_failed_crawl_fails_the_task(monkeypatch):
    monkeypatch.setattr(sakiori, "build", lambda papers: {"ok": False, "build": None, "said": ["The crawl failed: no CDN."]})
    with pytest.raises(TaskFailed, match="no CDN"):
        sakiori_tasks.sakiori_build_task.run(["00052"])


def test_what_was_not_written_is_in_the_result(monkeypatch):
    monkeypatch.setattr(sakiori, "build", lambda papers: {"ok": True, "build": "/b", "said": ["The notes were not written."]})
    assert "The notes were not written." in sakiori_tasks.sakiori_build_task.run(["00052"])
