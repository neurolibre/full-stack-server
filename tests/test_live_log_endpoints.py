"""The preview server's live log page and stream, and /api/logs refusing paths outside the logs folder."""

import json
import os

import pytest

import live_log

API = os.path.join(os.path.dirname(__file__), "..", "api")
TASK = "25c73a15-a292-463e-9e8c-5fda511e5d82"


@pytest.fixture
def preview(monkeypatch, tmp_path):
    here = os.getcwd()
    os.chdir(API)
    try:
        import neurolibre_preview_api as api
        import neurolibre_common_api as common
    finally:
        os.chdir(here)
    monkeypatch.setattr(api, "LOGS_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(common, "DATA_ROOT_PATH", str(tmp_path.parent))
    monkeypatch.setitem(common.common_config, "LOGS_FOLDER", tmp_path.name)
    monkeypatch.setattr(api, "celery_task_outcome", lambda task_id: None, raising=False)
    monkeypatch.setattr(live_log, "events",
                        lambda path, finished, offset=0, _real=live_log.events:
                        _real(path, finished, offset=offset, sleep=lambda s: None, poll=0))
    api.app.config["TESTING"] = True
    return api


def test_the_page_points_at_the_stream(preview):
    r = preview.app.test_client().get(f"/api/logs/live/{TASK}")
    assert r.status_code == 200
    assert f"/api/logs/live/{TASK}/stream" in r.get_data(as_text=True)


def test_a_page_for_something_that_is_not_a_task_id_is_not_found(preview):
    assert preview.app.test_client().get("/api/logs/live/not-a-task").status_code == 404


def test_the_stream_sends_the_log_then_the_outcome(preview, tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "Build started")
    live_log.finish(path, "success", "https://preview.example/api/logs/myst/o/r/1.log")

    r = preview.app.test_client().get(f"/api/logs/live/{TASK}/stream")

    assert r.content_type.startswith("text/event-stream")
    assert r.headers["X-Accel-Buffering"] == "no"
    body = r.get_data(as_text=True)
    assert "Build started" in body and "event: done" in body


def test_the_stream_continues_from_the_last_event_id(preview, tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "first")
    live_log.append(path, "second")
    live_log.finish(path, "success", None)

    body = preview.app.test_client().get(f"/api/logs/live/{TASK}/stream",
                                         headers={"Last-Event-ID": str(len("first\n"))}).get_data(as_text=True)
    assert "second" in body and "first" not in body


def test_the_stream_ends_when_celery_says_the_task_ended(preview, tmp_path, monkeypatch):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "Build started")      # the worker died before writing the outcome
    monkeypatch.setattr(preview, "celery_task_outcome", lambda task_id: {"status": "failure", "log_url": None})
    body = preview.app.test_client().get(f"/api/logs/live/{TASK}/stream").get_data(as_text=True)
    assert "event: done" in body


def test_a_log_file_is_still_served(preview, tmp_path):
    (tmp_path / "myst" / "o" / "r").mkdir(parents=True)
    (tmp_path / "myst" / "o" / "r" / "1.log").write_text("Built 2 T1 mapping.md")
    r = preview.app.test_client().get("/api/logs/myst/o/r/1.log")
    assert r.status_code == 200 and "Built 2 T1 mapping.md" in r.get_data(as_text=True)


def test_a_log_cannot_close_the_viewers_script_block(preview, tmp_path):
    (tmp_path / "myst" / "o" / "r").mkdir(parents=True)
    (tmp_path / "myst" / "o" / "r" / "1.log").write_text("</script><script>alert(1)</script>")
    body = preview.app.test_client().get("/api/logs/myst/o/r/1.log").get_data(as_text=True)
    assert "</script><script>alert(1)" not in body


@pytest.mark.parametrize("asked", ["%2e%2e/secret.txt", "myst/%2e%2e/%2e%2e/secret.txt", "..%2fsecret.txt"])
def test_a_path_outside_the_logs_folder_is_not_served(preview, tmp_path, asked):
    (tmp_path.parent / "secret.txt").write_text("do not show")
    r = preview.app.test_client().get(f"/api/logs/{asked}")
    assert r.status_code == 404
    assert "do not show" not in r.get_data(as_text=True)
