"""The editor's commands on the server, without a task: reset (start a paper's description over) and publish."""

import fcntl
import subprocess
import sys

import pytest

import weft_admin
import weft_steps
from conftest import API, DOI


@pytest.fixture
def server(tmp_path, monkeypatch):
    wefts, site = tmp_path / "wefts", tmp_path / "site"
    (wefts / "00041").mkdir(parents=True)
    (wefts / "00041.weft.yaml").write_text("paper: x\nversion: 1.2.0\n")
    (wefts / "00041.profiles.json").write_text("{}")
    monkeypatch.setattr(weft_steps, "WEFTS_PATH", wefts)
    monkeypatch.setattr(weft_steps, "SITE_PATH", site)
    monkeypatch.setattr(weft_steps, "GRAPH_DB_PATH", tmp_path / "graph.db")
    monkeypatch.setattr(weft_steps, "STORE_LOCK_PATH", tmp_path / "store.lock")
    w = type("Server", (), {})()
    w.wefts, w.rebuilds, w.publishes = wefts, [], []
    w.publish_result = {"ok": True, "errors": []}
    monkeypatch.setattr(weft_steps, "publish", lambda s, db, run=None: w.publishes.append((s, db)) or w.publish_result)
    monkeypatch.setattr(weft_admin.myst_cdn, "rebuild", lambda: w.rebuilds.append(True) or {"projects": 3})
    return w


def test_reset_starts_the_paper_over_publishes_and_rebuilds_the_cdn(server, capsys, tmp_path):
    assert weft_admin.main(["reset", DOI]) == 0
    out = capsys.readouterr().out
    assert not (server.wefts / "00041.weft.yaml").exists() and (server.wefts / "00041.weft.yaml.previous").exists()
    assert not (server.wefts / "00041").exists()
    assert server.publishes == [(tmp_path / "site", tmp_path / "graph.db")]
    assert server.rebuilds == [True]
    assert "The CDN was rebuilt without them." in out
    assert "Evidentron has no command to remove them." in out
    assert "Traceback" not in out and "—" not in out


def test_a_reset_with_nothing_confirmed_rebuilds_nothing(server, capsys):
    (server.wefts / "00041.weft.yaml").unlink()
    assert weft_admin.main(["reset", DOI]) == 0
    out = capsys.readouterr().out
    assert server.publishes == [] and server.rebuilds == []
    assert "rebuilt" not in out
    assert f"There is no confirmed description of {DOI} on the server." in out


def test_a_reset_whose_publish_failed_does_not_rebuild(server, capsys):
    server.publish_result = {"ok": False, "errors": ["The site could not be written: disk full."]}
    assert weft_admin.main(["reset", DOI]) == 1
    assert server.rebuilds == []
    assert "Evidentron's site could not be published again" in capsys.readouterr().out


def test_a_reset_that_stopped_exits_1(server, monkeypatch, capsys):
    def refuse(src, dst):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(weft_steps.os, "replace", refuse)
    assert weft_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.strip() == "The reset stopped: Permission denied."
    assert server.publishes == [] and server.rebuilds == []


def test_reset_waits_for_a_task_that_holds_the_store(server, monkeypatch, capsys):
    monkeypatch.setattr(weft_steps, "STORE_LOCK_WAIT", 0.2)
    with open(weft_steps.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        assert weft_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.strip() == ("Another task kept the paper store busy for more than 0.2 seconds, so "
                                               "nothing was done. Ask again in a while.")
    assert (server.wefts / "00041.weft.yaml").exists() and server.rebuilds == []


def test_a_failed_rebuild_is_one_sentence(server, monkeypatch, capsys):
    def boom():
        raise RuntimeError("/DATA/cdn/current is a folder, not a symlink")
    monkeypatch.setattr(weft_admin.myst_cdn, "rebuild", boom)
    assert weft_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "The CDN could not be rebuilt (RuntimeError: /DATA/cdn/current is a folder, not a symlink); the documents "
        "leave it at the next rebuild.")


def test_the_admin_has_no_places_of_its_own():
    for name in ("WEFTS_PATH", "GRAPH_DB_PATH", "SITE_PATH", "STORE_LOCK_PATH", "STORE_LOCK_WAIT", "EVIDENTRON_PATH"):
        assert not hasattr(weft_admin, name)


def test_the_command_runs_from_anywhere(tmp_path):
    done = subprocess.run([sys.executable, str(API / "weft_admin.py"), "--help"], capture_output=True, text=True,
                          cwd=tmp_path)
    assert done.returncode == 0 and "reset" in done.stdout


def test_publish_publishes_evidentrons_site_again_and_rebuilds_the_cdn(server, capsys):
    """After Evidentron changes (a new rule for kin, say), its site and index are published again from the store."""
    assert weft_admin.main(["publish"]) == 0
    assert server.publishes == [(weft_steps.SITE_PATH, weft_steps.GRAPH_DB_PATH)] and server.rebuilds == [True]
    said = capsys.readouterr().out
    assert "published again" in said and "rebuilt" in said


def test_a_publish_that_fails_rebuilds_nothing_and_says_why(server, capsys):
    server.publish_result = {"ok": False, "errors": ["the store could not be read"]}
    assert weft_admin.main(["publish"]) == 1
    assert server.rebuilds == []
    assert "the store could not be read" in capsys.readouterr().out
