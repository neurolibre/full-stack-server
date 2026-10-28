"""What Celery records about a task, as Flower shows it: FAILURE with the sentence the task said when it failed,
the message it posted as its result when it succeeded, and the request as a short readable summary."""

import pytest
from celery import Celery

import task_outcome
from task_outcome import TaskFailed

app = Celery("outcome-tests", task_cls=task_outcome.NeuroLibreTask)
app.conf.task_always_eager = True
app.conf.task_eager_propagates = False   # record a failure, as a worker does


@app.task(bind=True)
def says_and_returns_nothing(self, payload):
    task_outcome.succeeded(self, f"Archived {payload['what']}.")


@app.task(bind=True)
def returns_its_own_result(self, payload):
    task_outcome.succeeded(self, "said on the issue")
    return {"kept": 3}


@app.task(bind=True)
def fails_and_stops(self, payload):
    task_outcome.fail(self, "The archive could not be created: the bucket is gone.")
    raise AssertionError("not reached")


@app.task(bind=True)
def records_a_failure_and_returns(self, payload):
    # The older tasks post the failure, record it and return; the task still ends as a failure.
    task_outcome.failed(self, "Cannot download the data.")
    return


@app.task(bind=True)
def fails_after_saying_something(self, payload):
    task_outcome.succeeded(self, "First step done.")
    task_outcome.failed(self, "Second step failed.")


def test_the_message_a_task_posted_is_its_result():
    assert says_and_returns_nothing.apply(args=[{"what": "the book"}]).get() == "Archived the book."


def test_a_result_the_task_returns_is_kept():
    assert returns_its_own_result.apply(args=[{}]).get() == {"kept": 3}


def test_a_failure_ends_the_task_as_failed_with_its_sentence():
    result = fails_and_stops.apply(args=[{}])
    assert result.state == "FAILURE"
    assert isinstance(result.result, TaskFailed)
    assert str(result.result) == "The archive could not be created: the bucket is gone."


def test_a_recorded_failure_fails_the_task_when_it_returns():
    result = records_a_failure_and_returns.apply(args=[{}])
    assert result.state == "FAILURE" and str(result.result) == "Cannot download the data."


def test_a_failure_wins_over_an_earlier_success():
    result = fails_after_saying_something.apply(args=[{}])
    assert result.state == "FAILURE" and str(result.result) == "Second step failed."


def test_one_task_outcome_does_not_leak_into_the_next():
    fails_and_stops.apply(args=[{}])
    assert returns_its_own_result.apply(args=[{}]).get() == {"kept": 3}
    assert says_and_returns_nothing.apply(args=[{"what": "x"}]).state == "SUCCESS"


def test_an_expected_failure_is_not_logged_as_a_crash():
    assert TaskFailed in task_outcome.NeuroLibreTask.throws


def test_a_request_is_summarised_for_flower():
    payload = {"task_name": "Build MyST article", "issue_id": 52, "comment_id": None,
               "target_repo_url": "https://github.com/someone/seed-dispersal", "is_prod": True,
               "prod_version": "v1"}
    shown = task_outcome.describe([payload])
    assert shown["argsrepr"] == "issue 52 · someone/seed-dispersal · Build MyST article"
    assert shown["kwargsrepr"] == "is_prod=True, prod_version='v1'"


def test_other_names_for_the_repository_and_the_title_are_read():
    shown = task_outcome.describe([{"task_title": "Create Zenodo buckets", "issue_id": 7,
                                    "repository_url": "https://github.com/a/b"}])
    assert shown["argsrepr"] == "issue 7 · a/b · Create Zenodo buckets"
    assert shown["kwargsrepr"] == ""


def test_a_request_that_is_not_one_payload_is_left_to_celery():
    assert task_outcome.describe([3, "issue", "repo"]) == {}
    assert task_outcome.describe(None) == {}


def test_a_task_sent_without_a_summary_gets_one(monkeypatch):
    sent = {}
    monkeypatch.setattr(task_outcome.Task, "apply_async", lambda self, args=None, kwargs=None, **o: sent.update(o))
    says_and_returns_nothing.apply_async(args=[{"issue_id": 3, "repo_url": "https://github.com/x/y"}])
    assert sent["argsrepr"] == "issue 3 · x/y"
    says_and_returns_nothing.apply_async(args=[{"issue_id": 3}], argsrepr="given")
    assert sent["argsrepr"] == "given"
