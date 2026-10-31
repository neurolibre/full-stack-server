"""The live log of a MyST build: the file the worker and mystmd write to while the build runs, the events the
preview server streams from it, and the check that keeps /api/logs inside the logs folder."""

import json
import os
import time
import uuid

import pytest

import live_log

TASK = "25c73a15-a292-463e-9e8c-5fda511e5d82"


# --- where the file goes ---------------------------------------------------------------------------------------

def test_the_file_is_named_after_the_task(tmp_path):
    assert live_log.path_for(tmp_path, TASK) == tmp_path / "myst" / "live" / f"{TASK}.log"


@pytest.mark.parametrize("task_id", ["../../etc/passwd", "abc", "", f"{TASK}/..", TASK.upper() + "x"])
def test_anything_but_a_task_id_is_refused(tmp_path, task_id):
    with pytest.raises(ValueError):
        live_log.path_for(tmp_path, task_id)


# --- writing ---------------------------------------------------------------------------------------------------

def test_start_replaces_and_append_adds_lines(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "old")
    live_log.start(path, "Build started")
    live_log.append(path, "Pulling the image")
    assert path.read_text() == "Build started\nPulling the image\n"


def test_writing_never_fails_the_build(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("")
    live_log.start(blocked / "live.log", "x")        # its folder cannot be made: logged, not raised
    live_log.append(blocked / "live.log", "x")
    live_log.finish(blocked / "live.log", "failure", None)


def test_finish_records_the_outcome_and_the_full_log(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "Build started")
    assert live_log.outcome(path) is None
    live_log.finish(path, "success", "https://preview.example/api/logs/myst/o/r/1.log")
    assert live_log.outcome(path) == {"status": "success", "log_url": "https://preview.example/api/logs/myst/o/r/1.log"}


# --- streaming -------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def parse(events):
    """(event name, id, data) for each event; comments (keepalives) as ('comment', None, text)."""
    out = []
    for raw in events:
        if raw.startswith(":"):
            out.append(("comment", None, raw.strip()))
            continue
        fields = dict(line.split(": ", 1) for line in raw.strip().split("\n"))
        out.append((fields.get("event"), fields.get("id"), json.loads(fields["data"])))
    return out


def test_streams_the_file_then_the_outcome(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "Build started")
    clock = Clock()
    polls = {"n": 0}

    def finished():
        polls["n"] += 1
        if polls["n"] == 2:
            live_log.append(path, "\x1b[0;31mValueError\x1b[0m: bad colorbar")
        if polls["n"] == 3:
            live_log.finish(path, "failure", "https://preview.example/api/logs/myst/o/r/1.log")
        return live_log.outcome(path)

    events = parse(live_log.events(path, finished, clock=clock, sleep=clock.sleep))

    assert events[0] == ("log", str(len("Build started\n")), "Build started\n")
    assert events[1][0] == "log" and events[1][2] == "ValueError: bad colorbar\n"        # colour codes removed
    assert events[-1] == ("done", None, {"status": "failure", "log_url": "https://preview.example/api/logs/myst/o/r/1.log"})


def test_a_reconnecting_viewer_continues_where_it_stopped(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "first")
    live_log.append(path, "second")
    live_log.finish(path, "success", None)
    clock = Clock()
    events = parse(live_log.events(path, lambda: live_log.outcome(path), offset=len("first\n"),
                                   clock=clock, sleep=clock.sleep))
    assert events[0][2] == "second\n"


def test_a_quiet_build_gets_keepalives(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "Pulling the image")
    clock = Clock()

    def finished():
        return {"status": "success", "log_url": None} if clock.now >= 40 else None

    events = parse(live_log.events(path, finished, clock=clock, sleep=clock.sleep, keepalive=15))
    assert [e[0] for e in events].count("comment") == 2


def test_a_build_that_never_started_writing_waits_for_the_file(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    clock = Clock()

    def finished():
        if clock.now == 2:
            live_log.start(path, "Build started")
        return {"status": "success", "log_url": None} if clock.now >= 4 else None

    events = parse(live_log.events(path, finished, clock=clock, sleep=clock.sleep))
    assert events[0][2] == "Build started\n"


def test_the_stream_ends_when_the_viewer_has_waited_too_long(tmp_path):
    path = live_log.path_for(tmp_path, TASK)
    live_log.start(path, "x")
    clock = Clock()
    events = parse(live_log.events(path, lambda: None, clock=clock, sleep=clock.sleep, max_seconds=100))
    assert events[-1][0] == "timeout"


# --- old files -------------------------------------------------------------------------------------------------

def test_files_older_than_a_week_are_removed(tmp_path):
    old = live_log.path_for(tmp_path, str(uuid.uuid4()))
    new = live_log.path_for(tmp_path, str(uuid.uuid4()))
    live_log.start(old, "x")
    live_log.finish(old, "success", None)
    live_log.start(new, "y")
    week_ago = time.time() - 8 * 86400
    for p in old.parent.glob(f"{old.stem}*"):
        os.utime(p, (week_ago, week_ago))

    live_log.remove_old(tmp_path)

    assert sorted(p.name for p in new.parent.iterdir()) == [new.name]


# --- /api/logs stays inside the logs folder --------------------------------------------------------------------

def test_a_log_inside_the_folder_is_found(tmp_path):
    (tmp_path / "myst" / "o" / "r").mkdir(parents=True)
    (tmp_path / "myst" / "o" / "r" / "1.log").write_text("x")
    assert live_log.resolve_log_file(tmp_path, "myst/o/r/1.log") == str(tmp_path / "myst" / "o" / "r" / "1.log")


@pytest.mark.parametrize("asked", ["../secret", "myst/../../secret", "/etc/passwd", "myst/link/secret"])
def test_a_path_outside_the_folder_is_refused(tmp_path, asked):
    logs = tmp_path / "logs"
    (logs / "myst").mkdir(parents=True)
    (tmp_path / "secret").write_text("x")
    (logs / "myst" / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        live_log.resolve_log_file(logs, asked)
