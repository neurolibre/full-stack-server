"""Both MyST build endpoints pass fresh_execution (run every notebook again) to the build task, off by default."""

import os
from types import SimpleNamespace

import pytest

import screening_client

API = os.path.join(os.path.dirname(__file__), "..", "api")


@pytest.fixture
def preview(monkeypatch):
    here = os.getcwd()
    os.chdir(API)
    try:
        import neurolibre_preview_api as api
    finally:
        os.chdir(here)
    monkeypatch.setattr(api.preview_api.htpasswd, "required",
                        lambda f: (lambda *a, **k: f(*a, user="tester", **k)))
    monkeypatch.setattr(screening_client, "Github",
                        lambda token=None: SimpleNamespace(get_repo=lambda name: SimpleNamespace(full_name=name)))
    monkeypatch.setattr(screening_client, "send_email", lambda *a, **k: None)
    monkeypatch.setattr(api, "format_commit_hash", lambda url, ref: "abc1234", raising=False)
    sent = []
    monkeypatch.setattr(api.preview_build_myst_task, "apply_async",
                        lambda args=None, **kw: sent.append(args[0]) or SimpleNamespace(task_id="id-build"))
    api.app.config["TESTING"] = True
    return SimpleNamespace(client=api.app.test_client(), sent=sent)


def test_the_build_endpoint_passes_fresh_execution(preview):
    preview.client.post("/api/myst/build", json={"id": 52, "repository_url": "https://github.com/someone/book",
                                                 "fresh_execution": True})
    assert preview.sent[-1]["fresh_execution"] is True


def test_the_build_endpoint_keeps_the_cache_by_default(preview):
    preview.client.post("/api/myst/build", json={"id": 52, "repository_url": "https://github.com/someone/book"})
    assert preview.sent[-1]["fresh_execution"] is False


def test_the_robo_endpoint_passes_fresh_execution(preview):
    preview.client.post("/api/book/build/test", json={"repo_url": "someone/book", "commit_hash": "latest",
                                                      "email": "author@example.org", "fresh_execution": True})
    assert preview.sent[-1]["fresh_execution"] is True
