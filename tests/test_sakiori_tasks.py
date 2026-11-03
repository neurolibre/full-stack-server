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


class WalkTask:
    """Stands in for BaseNeuroLibreTask: records what the task says on the issue."""
    last = None

    def __init__(self, celery_task, screening):
        self.screening = SimpleNamespace(issue_id=screening["issue_id"])
        self.started = self.succeeded = self.failed = None
        WalkTask.last = self

    def start(self, message):
        self.started = message

    def succeed(self, message, collapsable=True):
        self.succeeded = message

    def fail(self, message):
        self.failed = message
        raise TaskFailed(message)


def test_a_served_walk_is_announced_on_the_issue_with_what_sakiori_shows(monkeypatch):
    monkeypatch.setattr(sakiori_tasks, "BaseNeuroLibreTask", WalkTask)
    asked = []
    monkeypatch.setattr(sakiori, "build", lambda papers: asked.append(papers) or {"ok": True, "build": "/b", "said": []})
    sakiori_tasks.sakiori_walk_task.run({"issue_id": 52, "target_repo_url": "https://github.com/x/y"})
    said = WalkTask.last.succeeded
    assert asked == [["00052"]]
    assert said.startswith("🧵 **Your paper's walk through the collection is ready:** https://sakiori.evidencepub.io/00052")
    assert "You put in the hard work to do more than a PDF, so we have a surprise for you." in said
    assert "Not everything was written" not in said


def test_what_the_walk_could_not_write_is_folded_under_the_announcement(monkeypatch):
    monkeypatch.setattr(sakiori_tasks, "BaseNeuroLibreTask", WalkTask)
    monkeypatch.setattr(sakiori, "build", lambda papers: {"ok": True, "build": "/b", "said": ["Not every agent answer was written."]})
    sakiori_tasks.sakiori_walk_task.run({"issue_id": 52, "target_repo_url": "https://github.com/x/y"})
    assert "<summary>Not everything was written this time</summary>" in WalkTask.last.succeeded
    assert "- Not every agent answer was written." in WalkTask.last.succeeded


def test_a_walk_that_cannot_be_woven_fails_with_why(monkeypatch):
    monkeypatch.setattr(sakiori_tasks, "BaseNeuroLibreTask", WalkTask)
    monkeypatch.setattr(sakiori, "build", lambda papers: {"ok": False, "build": None, "said": ["The crawl of the CDN failed."]})
    with pytest.raises(TaskFailed):
        sakiori_tasks.sakiori_walk_task.run({"issue_id": 52, "target_repo_url": "https://github.com/x/y"})
    assert WalkTask.last.failed == "The crawl of the CDN failed."


def test_a_publication_queues_a_walk_that_reports_in_a_comment_of_its_own(monkeypatch):
    sent = []
    monkeypatch.setattr(sakiori_tasks.sakiori_walk_task, "apply_async", lambda args: sent.append(args) or SimpleNamespace(id="t"))
    screening = SimpleNamespace(to_dict=lambda: {"issue_id": 52, "comment_id": 999, "task_name": "SYNC MYST"})
    line = sakiori_tasks.queue("neurolibre.00052", screening)
    assert sent == [[{"issue_id": 52, "comment_id": None, "task_name": "SAKIORI WALK"}]]   # not the sync's comment
    assert "https://sakiori.evidencepub.io/00052" in line


def test_the_walk_endpoint_starts_the_task_for_the_issue(client, monkeypatch):
    import screening_client
    # ScreeningClient asks GitHub for the repository when it is made.
    monkeypatch.setattr(screening_client, "Github",
                        lambda token=None: SimpleNamespace(get_repo=lambda name: SimpleNamespace(full_name=name)))
    sent = []
    monkeypatch.setattr(sakiori_tasks.sakiori_walk_task, "apply_async", lambda args: sent.append(args) or SimpleNamespace(task_id="t-9"))
    response = client.post("/api/sakiori/walk", json={"id": 52, "repository_url": "https://github.com/x/y"})
    assert response.status_code < 300, response.get_data(as_text=True)
    assert sent[0][0]["issue_id"] == 52 and sent[0][0]["task_name"] == "SAKIORI WALK"
