"""What a task says on its review issue: its status comment while it runs, and its result in a comment of its own.

GitHub sends nothing for an edited comment, so a result written over the status comment reached no one; it is now a
new comment, and the status comment links to it. A task with a live log (a MyST build) links to it while it runs.
GitHub is faked."""

from types import SimpleNamespace

import pytest

import screening_client


@pytest.fixture
def issue(monkeypatch):
    comments = {}

    def create(self, body, override_assign=False):
        number = 100 + len(comments)
        comments[number] = body
        if not override_assign:
            self.comment_id = number
        return number

    def update(self, body):
        comments[self.comment_id] = body

    monkeypatch.setattr(screening_client, "Github", lambda token=None: SimpleNamespace(get_repo=lambda name: None))
    monkeypatch.setattr(screening_client.ScreeningClient, "gh_create_comment", create)
    monkeypatch.setattr(screening_client.ScreeningClient, "gh_update_comment", update)
    client = screening_client.ScreeningClient(task_name="SAKIORI WALK", issue_id=52, notify_target=True,
                                              review_repository="neurolibre/neurolibre-reviews")
    client.task_id = "29e05264-4fe3-4228-a24e-ffbbd1a134fe"
    return SimpleNamespace(client=client, comments=comments)


def test_the_result_is_a_new_comment_and_the_status_comment_points_to_it(issue):
    status = issue.client.comment_id
    issue.client.respond.STARTED("Weaving.")
    result = issue.client.respond.SUCCESS("Meet your paper's walk.", collapsable=False)
    assert result != status and list(issue.comments) == [status, result]
    assert "Meet your paper's walk." in issue.comments[result] and "Success" in issue.comments[result]
    assert "Success" in issue.comments[status]
    assert f"neurolibre/neurolibre-reviews/issues/52#issuecomment-{result}" in issue.comments[status]
    assert issue.client.comment_id == status            # later updates still go to the status comment


def test_without_a_status_comment_the_result_is_still_posted(issue):
    issue.client.comment_id = None
    issue.comments.clear()
    result = issue.client.respond.SUCCESS("Done.")
    assert list(issue.comments) == [result]


def test_a_failure_stays_on_the_status_comment(issue):
    status = issue.client.comment_id
    issue.client.respond.FAILURE("It broke.", collapsable=False)
    assert list(issue.comments) == [status] and "It broke." in issue.comments[status]


def test_a_task_with_a_live_log_links_to_it_while_it_runs(issue):
    issue.client.respond.STARTED("Warming up the myst builder...")
    assert "live" not in issue.comments[issue.client.comment_id]
    issue.client.live_log_url = "https://preview.neurolibre.org/api/logs/live/29e05264-4fe3-4228-a24e-ffbbd1a134fe"
    issue.client.respond.STARTED("Warming up the myst builder...")
    assert "[Follow the build's log live](https://preview.neurolibre.org/api/logs/live/" in \
        issue.comments[issue.client.comment_id]
