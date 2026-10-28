"""What Celery records about a task: whether it failed, what it said, and the request it was given.

The tasks say their outcome on the review issue, and Flower shows what Celery records. The two used to disagree:
a task that failed posted the failure, then either returned (Celery recorded SUCCESS) or raised Ignore (Celery
recorded nothing, and Flower kept it at STARTED); and every result was None. NeuroLibreTask, the class of every
task on the app, makes the record follow what the task said:

- failed(task, message) records a failure and lets the task carry on to its return; the task then ends as
  FAILURE with that message. fail(task, message) records it and stops the task at once. A failure stays recorded
  even when something later succeeds.
- succeeded(task, message) records the message the task posted; a task that returns nothing ends with it as its
  result.
- A task whose arguments are one payload (a dict) is sent with a short summary of it, which Flower shows in place
  of the whole dict: the issue, the repository and the task's title as args, the other set values as kwargs.

TaskFailed is an expected error (`throws`), so the worker logs it in one line, not as a crash with a traceback.
"""

from celery import Task


class TaskFailed(Exception):
    """A task failed for the reason it said on the issue; the message is that sentence."""


class _Outcome:
    def __init__(self):
        self.message = None
        self.failure = None


class NeuroLibreTask(Task):
    throws = (TaskFailed,)

    def __call__(self, *args, **kwargs):
        self.request.outcome = _Outcome()
        result = super().__call__(*args, **kwargs)
        outcome = self.request.outcome
        if outcome.failure is not None:
            raise TaskFailed(outcome.failure)
        return outcome.message if result is None else result

    def apply_async(self, args=None, kwargs=None, **options):
        if "argsrepr" not in options and "kwargsrepr" not in options:
            options.update(describe(args))
        return super().apply_async(args=args, kwargs=kwargs, **options)


def _outcome(task):
    outcome = getattr(task.request, "outcome", None)
    if outcome is None:                      # run directly (.run), outside Celery's call
        outcome = task.request.outcome = _Outcome()
    return outcome


def succeeded(task, message):
    _outcome(task).message = message


def failed(task, message):
    outcome = _outcome(task)
    if outcome.failure is None:
        outcome.failure = str(message)


def fail(task, message):
    failed(task, message)
    raise TaskFailed(_outcome(task).failure)


# The payload keys that make the summary, in the names the endpoints use.
REPOSITORY_KEYS = ("target_repo_url", "repository_url", "repo_url")
TITLE_KEYS = ("task_name", "task_title")
LEFT_OUT = {"issue_id", *REPOSITORY_KEYS, *TITLE_KEYS}


def describe(args):
    """argsrepr and kwargsrepr for a task's arguments when they are one payload dict; {} otherwise."""
    if not args or len(args) != 1 or not isinstance(args[0], dict):
        return {}
    payload = args[0]
    parts = []
    if payload.get("issue_id") is not None:
        parts.append(f"issue {payload['issue_id']}")
    repository = next((payload[k] for k in REPOSITORY_KEYS if payload.get(k)), None)
    if repository:
        parts.append("/".join(str(repository).rstrip("/").split("/")[-2:]))
    title = next((payload[k] for k in TITLE_KEYS if payload.get(k)), None)
    if title:
        parts.append(str(title))
    rest = ", ".join(f"{k}={v!r}" for k, v in payload.items() if k not in LEFT_OUT and v is not None)
    return {"argsrepr": " · ".join(parts), "kwargsrepr": rest}
