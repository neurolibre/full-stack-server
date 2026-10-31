"""The MyST build task writes the live log: its own progress, the start email's link, and the outcome at the end.
GitHub, Redis, the registry and Docker are faked; the build stops where the fake Jupyter fails to start."""

import smtplib
from types import SimpleNamespace

import pytest

import live_log
import neurolibre_celery_tasks as celery_tasks
import screening_client

TASK = "25c73a15-a292-463e-9e8c-5fda511e5d82"


class Lock:
    def __init__(self, free):
        self.free = free

    def acquire(self, blocking=False):
        return self.free

    def release(self):
        pass


@pytest.fixture
def build(monkeypatch, tmp_path):
    emails = []

    def no_smtp(*a, **k):
        raise AssertionError("a test tried to send an email")
    monkeypatch.setattr(smtplib, "SMTP", no_smtp)
    # A ScreeningClient made with an email address sends "request received" when it is created
    monkeypatch.setattr(screening_client, "send_email", lambda to, subject, body: emails.append(body))
    monkeypatch.setattr(screening_client, "Github", lambda token=None: SimpleNamespace(get_repo=lambda name: None))
    monkeypatch.setattr(celery_tasks, "LOGS_ROOT", str(tmp_path))
    monkeypatch.setattr(celery_tasks.time, "sleep", lambda s: None)
    monkeypatch.setattr(celery_tasks, "format_commit_hash", lambda url, ref: "abc1234")
    monkeypatch.setattr(celery_tasks, "write_log", lambda *a, **k: "myst/someone/tide-gauges/1.log")
    monkeypatch.setattr(celery_tasks, "cleanup_hub", lambda hub: None)
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "email_user", lambda self, message: emails.append(message))
    monkeypatch.setattr(celery_tasks, "REES", lambda config: SimpleNamespace(found_image_name="img", preserve_cache=True))

    def no_jupyter(rees, **kwargs):
        def spawn():
            raise RuntimeError("The Jupyter server at http://localhost:8888 did not answer within 120 seconds")
        return SimpleNamespace(spawn_jupyter_hub=spawn)
    monkeypatch.setattr(celery_tasks, "JupyterHubLocalSpawner", no_jupyter)
    task = celery_tasks.preview_build_myst_task
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None)

    def run(lock_free=True):
        monkeypatch.setattr(celery_tasks, "_lock_redis", SimpleNamespace(lock=lambda key, timeout=None: Lock(lock_free)))
        screening = screening_client.ScreeningClient(
            task_name="build", email_address="author@example.org",
            target_repo_url="https://github.com/someone/tide-gauges",
            is_prod=False, commit_hash="abc1234", binder_hash="latest", build_cache=True, prod_version="v0")
        task.push_request(id=TASK)
        try:
            task.run(screening.to_dict())
        finally:
            task.pop_request()
        return live_log.path_for(tmp_path, TASK)

    return SimpleNamespace(run=run, emails=emails)


def test_a_failed_start_is_in_the_live_log_with_the_full_log(build):
    path = build.run()
    text = path.read_text()
    assert text.startswith("MyST build of someone/tide-gauges started at ")
    assert "pulling the execution image and starting Jupyter" in text
    assert "did not answer within 120 seconds" in text
    assert live_log.outcome(path) == {
        "status": "failure",
        "log_url": f"{celery_tasks.PREVIEW_SERVER}/api/logs/myst/someone/tide-gauges/1.log"}


def test_the_start_email_links_to_the_live_log(build):
    build.run()
    assert any(f"{celery_tasks.PREVIEW_SERVER}/api/logs/live/{TASK}" in body for body in build.emails)


def test_a_build_already_running_ends_the_live_log_at_once(build):
    path = build.run(lock_free=False)
    assert "already in progress" in path.read_text()
    assert live_log.outcome(path)["status"] == "failure"
