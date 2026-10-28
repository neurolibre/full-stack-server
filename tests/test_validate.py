"""The preview server's repository check (/api/process), with GitHub's listing and files faked."""

import json
import os
from types import SimpleNamespace

import pytest

API = os.path.join(os.path.dirname(__file__), "..", "api")


@pytest.fixture
def preview(monkeypatch):
    here = os.getcwd()
    os.chdir(API)
    try:
        import neurolibre_preview_api as preview_api
    finally:
        os.chdir(here)
    return preview_api


def run(preview, monkeypatch, listing, myst_text):
    """The messages the check streams for a repository whose root lists `listing` and whose myst.yml is myst_text."""
    def get(url, timeout=None):
        if url.endswith("/myst.yml"):
            return SimpleNamespace(text=myst_text, raise_for_status=lambda: None)
        folder = url.split("/contents")[1].strip("/")
        return SimpleNamespace(json=lambda: listing[folder], raise_for_status=lambda: None)
    monkeypatch.setattr(preview.requests, "get", get)
    response = preview.app.test_client().get("/api/process?repo_url=https://github.com/someone/tide-gauges")
    return [json.loads(line[len("data: "):]) for line in response.get_data(as_text=True).split("\n\n") if line]


ROOT_WITHOUT_CONTENT = {
    "": [{"type": "dir", "name": "binder"},
         {"type": "file", "name": "myst.yml", "download_url": "https://raw.example/someone/tide-gauges/myst.yml"}],
    "binder": [{"name": "requirements.txt"}],
}


def test_a_myst_repository_without_a_content_folder_is_checked_and_its_live_code_described(preview, monkeypatch):
    said = run(preview, monkeypatch, ROOT_WITHOUT_CONTENT,
               "project:\n  title: Tide gauges\n  jupyter:\n    binder:\n      repo: someone/tide-gauges\n")
    assert {"message": "- ⚙️ myst.yml", "status": "positive"} in said
    assert any("runs code in the page on a BinderHub" in m["message"] for m in said)
    assert said[-1] == {"message": "Repository structure is valid.", "status": "success"}


def test_a_myst_yml_without_a_project_section_makes_the_repository_invalid(preview, monkeypatch):
    said = run(preview, monkeypatch, ROOT_WITHOUT_CONTENT, "site:\n  template: book-theme\n")
    assert {"message": "Error: myst.yml has no `project` section.", "status": "error"} in said
    assert said[-1] == {"message": "Repository structure is invalid.", "status": "failure"}
