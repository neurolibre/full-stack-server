"""The three weft endpoints, through Flask's test client, with the htpasswd check replaced and each task's apply_async faked."""

import re
from types import SimpleNamespace

import pytest

import neurolibre_preprint_api as api
import screening_client
import weft_messages

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
    for key in ("fields", "at", "command_comment_id", "by", "submission"):
        assert key not in d
    assert d["comment_id"] is None             # the bot's own reply comment is made by the worker


@pytest.mark.parametrize("route", ROUTES)
def test_optional_keys_reach_the_task(client, sent, route):
    task_name, _ = ROUTES[route]
    body = {"id": 41, "repository_url": REPO, "fields": "a,b", "comment_id": 100, "at": 200}
    assert client.post(route, json=body).status_code == 200
    d = sent[task_name]
    assert (d["fields"], d["command_comment_id"], d["at"]) == ("a,b", 100, 200)
    assert d["comment_id"] is None             # the command's comment is not the comment the bot replies in


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("key", ["by", "submission"])
def test_by_and_submission_are_refused(client, sent, route, key):
    body = {"id": 41, "repository_url": REPO, key: "ada"}
    assert client.post(route, json=body).status_code == 422
    assert sent == {}


def _description(view):
    return " ".join(str(a.options[0].get("description", "")) for a in view.__apispec__["docs"])


def test_the_options_are_described():
    schema = api.WeftSchema().fields
    assert set(schema) == {"id", "repository_url", "fields", "comment_id", "at"}
    assert "confirm" in schema["fields"].metadata["description"].lower()
    assert "draft" not in schema["fields"].metadata["description"].lower()
    for key in ("comment_id", "at"):
        assert "comment" in schema[key].metadata["description"]
    assert "submitted" not in _description(api.weft_check_post)
    for view in (api.weft_draft_post, api.weft_check_post, api.weft_confirm_post):
        text = _description(view)
        assert "fork" not in text and "weft" not in re.sub(r"`[^`]*`", "", text)    # only in the commands
        assert "on the server" in text and "—" not in text


def test_the_confirm_endpoint_says_who_confirms_in_the_one_sentence():
    text = _description(api.weft_confirm_post)
    assert weft_messages.WHO_CONFIRMS in text
    assert ("The confirmation is recorded under the name of the author of that comment, who must be the paper's "
            "author or the issue's editor.") in text
    assert "without `at`" not in text
