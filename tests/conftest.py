"""What the tests of the api folder share: the import setup, and the fakes of a task and of Evidentron's results.

The api modules import each other by their plain names (`import weft_steps`) and read config/*.yaml relative to
the api folder, so the folder goes on sys.path and the modules the tests use are imported from inside it, once.
A test imports them by the same plain names, so it patches the module the code under test uses.
"""

import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

if not hasattr(ast, "Str"):        # Werkzeug 2.0 (pinned with Flask 2.0) builds routes with ast.Str, gone in Python 3.14
    class _Str(ast.Constant):
        def __init__(self, s=""):
            super().__init__(value=s)

        s = property(lambda self: self.value, lambda self, v: setattr(self, "value", v))

    ast.Str = _Str

API = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API))
_here = os.getcwd()
os.chdir(API)
try:
    import neurolibre_celery_tasks      # noqa: F401  (registers the weft tasks, as the worker does)
    import weft_tasks                    # noqa: F401
    import neurolibre_preprint_api       # noqa: F401
    import screening_client              # noqa: F401
finally:
    os.chdir(_here)

from celery.exceptions import Ignore

DOI = "10.55458/neurolibre.00041"

# The keys every result of an Evidentron command has, and those of each command (evidentron/frames/result.py).
COMMON = {"ok": True, "errors": [], "warnings": [], "notes": []}
EXTRA = {
    "profile": {"weft": None, "profiles": None, "problems": [], "drafted_by": None, "summary": []},
    "explore": {"weft": None, "profiles": None, "problems": [], "drafted_by": None, "summary": []},
    "check": {"unreadable": None},
    "confirm": {"unreadable": None, "version": None, "modified": None, "confirmed": [], "applied": [], "terms": []},
    "load": {"fields": 0, "paper": None},
    "export": {"written": None, "left_out": [], "refused": None},
    "graph concepts": {"linked": 0, "terms": []},
    "graph": {"papers": []},
    "publish": {"terms": [], "terms_left_out": 0, "exported": [], "refused": [], "metadata": [], "addresses": {},
                "terms_url": None, "folder": None},
}


def said(command, **keys):
    """The one line of JSON an Evidentron command prints under --json: `ok` is false when errors are given."""
    result = dict(COMMON, command=command, **EXTRA[command])
    result.update(keys)
    if "ok" not in keys:
        result["ok"] = not result["errors"]
    return json.dumps(result) + "\n"


class FakeTask:
    """Stands for BaseNeuroLibreTask: records what the task says on the issue."""
    last = None

    def __init__(self, celery_task, screening=None, payload=None):
        s = dict(screening)
        self.screening = SimpleNamespace(
            issue_id=s["issue_id"], review_repository="neurolibre/reviews",
            target_repo_url=s["target_repo_url"], github_client=FakeGitHub.current,
            fields=s.get("fields"), at=s.get("at"), command_comment_id=s.get("command_comment_id"),
            commit_hash=s.get("commit_hash"))
        self.owner_name, self.repo_name = "someone", "paper-repo"
        self.started, self.comment, self.failed = [], None, None
        FakeTask.last = self

    def start(self, message=""):
        self.started.append(message)

    def succeed(self, message, collapsable=True, attachment_path=None):
        self.comment = message

    def fail(self, message, attachment_path=None):
        self.failed = message
        raise Ignore()


class FakeGitHub:
    """The GitHub client a task is given. The weft tasks reach GitHub only through the read helpers the tests
    replace, so every use of the client itself is recorded: a test can say it was never used."""
    current = None

    def __init__(self):
        self.used = []
        FakeGitHub.current = self

    def __getattr__(self, name):
        def record(*a, **k):
            self.used.append(name)
            raise AssertionError(f"the GitHub client was used directly: {name}")
        return record
