"""A MyST build that wrote its site but had problems on some pages ends as completed, and says which pages: in the
message, the email, the live log and the full log. GitHub, Redis, the registry, Docker and mystmd are faked."""

import smtplib
from types import SimpleNamespace

import pytest

import live_log
import neurolibre_celery_tasks as celery_tasks
import screening_client

TASK = "25c73a15-a292-463e-9e8c-5fda511e5d82"
COMMIT = "078af409c5863856b418a79fe3358a4f760e397b"

MYST_OUTPUT_WITH_PROBLEMS = """📖 Built 2 T1 mapping.md in 1.55 s.
⛔️ 5 B0 Mapping/Notebooks/Figure-5-1-1.ipynb An exception occurred during code execution, halting further execution:
ValueError: Invalid property specified for object of type plotly.graph_objs.heatmap.ColorBar: 'titleside'
⛔️ 5 B0 mapping.md Cannot find image "./videos/KSpace.mp4" in /DATA/myst/someone/tide-gauges/latest
📚 Built 113 pages for project in 2.13 min.
"""


@pytest.fixture
def build(monkeypatch, tmp_path):
    out = SimpleNamespace(emails=[], succeeded=[], failed=[], logs=[])

    def no_smtp(*a, **k):
        raise AssertionError("a test tried to send an email")
    monkeypatch.setattr(smtplib, "SMTP", no_smtp)
    monkeypatch.setattr(screening_client, "send_email", lambda to, subject, body: None)
    monkeypatch.setattr(screening_client, "Github", lambda token=None: SimpleNamespace(get_repo=lambda name: None))
    monkeypatch.setattr(celery_tasks, "LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(celery_tasks, "DATA_ROOT_PATH", str(tmp_path))
    monkeypatch.setattr(celery_tasks.time, "sleep", lambda s: None)
    monkeypatch.setattr(celery_tasks, "format_commit_hash", lambda url, ref: COMMIT)
    monkeypatch.setattr(celery_tasks, "write_log", lambda owner, repo, kind, text, info=None:
                        out.logs.append(text) or "myst/someone/tide-gauges/1.log")
    monkeypatch.setattr(celery_tasks, "cleanup_hub", lambda hub: None)
    monkeypatch.setattr(celery_tasks, "_lock_redis",
                        SimpleNamespace(lock=lambda key, timeout=None: SimpleNamespace(acquire=lambda blocking=False: True,
                                                                                        release=lambda: None)))
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "email_user", lambda self, message: out.emails.append(message))
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "succeed",
                        lambda self, message, collapsable=True, attachment_path=None: out.succeeded.append(message))
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "fail",
                        lambda self, message, attachment_path=None: out.failed.append(message))
    monkeypatch.setattr(celery_tasks, "REES", lambda config: SimpleNamespace(found_image_name="img", preserve_cache=True))
    where = {"owner": "someone"}

    def myst():
        return tmp_path / celery_tasks.MYST_FOLDER / where["owner"] / "tide-gauges"

    def spawner(rees, **kwargs):
        for owner in {"someone", where["owner"]}:
            folder = tmp_path / celery_tasks.MYST_FOLDER / owner / "tide-gauges" / "latest"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "myst.yml").write_text("version: 1")
        return SimpleNamespace(spawn_jupyter_hub=lambda: [])
    monkeypatch.setattr(celery_tasks, "JupyterHubLocalSpawner", spawner)

    class Builder:
        output = MYST_OUTPUT_WITH_PROBLEMS

        def __init__(self, hub):
            pass

        def setenv(self, key, value):
            pass

        def build(self, *args, **kwargs):
            # myst-libre >= 0.4.6 saves a build that wrote its site, whatever its notebooks printed
            site = myst() / COMMIT / "_build" / "html"
            site.mkdir(parents=True, exist_ok=True)
            (site / "index.html").write_text("<html>")
            return Builder.output

        def cleanup(self):
            pass
    monkeypatch.setattr(celery_tasks, "MystBuilder", Builder)
    monkeypatch.setattr(celery_tasks.preview_build_myst_task, "update_state", lambda *a, **k: None)

    def run(is_prod=False, issue_id=None, owner="someone", **extra):
        where["owner"] = owner
        screening = screening_client.ScreeningClient(
            task_name="build", email_address="author@example.org", issue_id=issue_id,
            target_repo_url="https://github.com/someone/tide-gauges",
            is_prod=is_prod, commit_hash=COMMIT, binder_hash="latest", build_cache=True,
            prod_version="v1" if is_prod else "v0", **extra)
        celery_tasks.preview_build_myst_task.push_request(id=TASK)
        try:
            celery_tasks.preview_build_myst_task.run(screening.to_dict())
        finally:
            celery_tasks.preview_build_myst_task.pop_request()
        return live_log.path_for(tmp_path / "logs", TASK)

    out.run, out.builder = run, Builder
    return out


def test_a_build_with_problems_completes_and_lists_the_pages(build):
    build.run()
    assert build.failed == [] and len(build.succeeded) == 1
    message = build.succeeded[0]
    assert "MyST build has been completed" in message
    assert "2 pages had problems" in message
    assert "`5 B0 Mapping/Notebooks/Figure-5-1-1.ipynb`: ValueError: Invalid property" in message
    assert "/DATA/" not in message


def test_the_email_lists_the_pages(build):
    build.run()
    completion = [body for body in build.emails if "_build/html/index.html" in body][-1]
    assert "2 pages had problems" in completion and "Figure-5-1-1.ipynb" in completion


def test_the_live_log_ends_as_completed_with_problems(build):
    path = build.run()
    assert live_log.outcome(path)["status"] == "warnings"


def test_the_full_log_ends_with_the_list(build):
    build.run()
    assert "2 pages had problems" in build.logs[-1].split("📚 Built 113 pages")[-1]


def test_a_clean_build_says_nothing_about_problems(build):
    build.builder.output = "📖 Built 2 T1 mapping.md in 1.55 s.\n📚 Built 113 pages\n"
    path = build.run()
    assert "had problems" not in build.succeeded[0]
    assert live_log.outcome(path)["status"] == "success"


# Fresh execution: production builds always run every notebook with the final image; a preview build does when asked.

@pytest.fixture
def build_calls(build, monkeypatch):
    calls = []
    original = build.builder.build

    def recording(self, *args, **kwargs):
        calls.append(kwargs)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(build.builder, "build", recording)
    return calls


def test_a_preview_build_keeps_the_cache_by_default(build, build_calls):
    build.run()
    assert build_calls[0]["fresh_execution"] is False


def test_a_preview_build_runs_every_notebook_when_asked(build, build_calls):
    build.run(fresh_execution=True)
    assert build_calls[0]["fresh_execution"] is True


def test_a_production_build_always_runs_every_notebook(build, build_calls, monkeypatch, tmp_path):
    monkeypatch.setattr(celery_tasks, "gh_forkify_it", lambda url: "https://github.com/roboneurolibre/tide-gauges")
    monkeypatch.setattr(celery_tasks, "gh_read_from_issue_body", lambda *a: "N/A")
    monkeypatch.setattr(celery_tasks.BaseNeuroLibreTask, "start", lambda self, message="": None)
    monkeypatch.setattr(screening_client.ScreeningClient, "gh_create_comment", lambda self, *a, **k: 1)
    monkeypatch.setattr(screening_client.ScreeningClient, "gh_update_comment", lambda self, *a, **k: 1)
    build.run(is_prod=True, issue_id=52, owner="roboneurolibre")
    assert build_calls[0]["fresh_execution"] is True
