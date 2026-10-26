"""The three weft endpoints, through Flask's test client, with the htpasswd check replaced and each task's apply_async faked."""

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

if not hasattr(ast, "Str"):        # Werkzeug 2.0 (pinned with Flask 2.0) builds routes with ast.Str, gone in Python 3.14
    class _Str(ast.Constant):
        def __init__(self, s=""):
            super().__init__(value=s)

        s = property(lambda self: self.value, lambda self, v: setattr(self, "value", v))

    ast.Str = _Str

API = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API))
_here = os.getcwd()
os.chdir(API)                      # the modules read config/*.yaml relative to the api folder
try:
    import neurolibre_preprint_api as api
    import screening_client
finally:
    os.chdir(_here)

ROUTES = {
    "/api/weft/draft": ("weft_draft_task", "WEFT DRAFT"),
    "/api/weft/check": ("weft_check_task", "WEFT CHECK"),
    "/api/weft/confirm": ("weft_confirm_task", "WEFT CONFIRM"),
}
REPO = "https://github.com/someone/paper-repo"


@pytest.fixture
def client(monkeypatch):
    # The real check reads an htpasswd file; here every request is a logged-in user.
    monkeypatch.setattr(
        api.preprint_api.htpasswd, "required",
        lambda f: (lambda *a, **k: f(*a, user="tester", **k)))
    # ScreeningClient asks GitHub for the repository when it is made.
    monkeypatch.setattr(
        screening_client, "Github",
        lambda token=None: SimpleNamespace(get_repo=lambda name: SimpleNamespace(full_name=name)))
    api.app.config["TESTING"] = True
    return api.app.test_client()


@pytest.fixture
def sent(monkeypatch):
    calls = {}
    for route, (task_name, _) in ROUTES.items():
        task = getattr(api, task_name)

        def fake(args=None, _name=task_name, **kw):
            calls[_name] = args[0]
            return SimpleNamespace(task_id=f"id-{_name}")
        monkeypatch.setattr(task, "apply_async", fake)
    return calls


@pytest.mark.parametrize("route", ROUTES)
def test_payload_without_id_is_422(client, sent, route):
    assert client.post(route, json={"repository_url": REPO}).status_code == 422
    assert sent == {}


@pytest.mark.parametrize("route", ROUTES)
def test_valid_payload_starts_the_task(client, sent, route):
    task_name, label = ROUTES[route]
    r = client.post(route, json={"id": 41, "repository_url": REPO})
    assert r.status_code == 200
    assert f"id-{task_name}" in r.get_data(as_text=True)
    d = sent[task_name]
    assert d["task_name"] == label and d["issue_id"] == 41 and d["target_repo_url"] == REPO
    for key in ("fields", "by", "submission"):
        assert key not in d


@pytest.mark.parametrize("route", ROUTES)
def test_optional_keys_reach_the_task(client, sent, route):
    task_name, _ = ROUTES[route]
    body = {"id": 41, "repository_url": REPO, "fields": "a,b", "by": "octocat", "submission": "x: 1\n"}
    assert client.post(route, json=body).status_code == 200
    d = sent[task_name]
    assert (d["fields"], d["by"], d["submission"]) == ("a,b", "octocat", "x: 1\n")
