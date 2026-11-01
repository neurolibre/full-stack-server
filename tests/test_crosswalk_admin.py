"""The editor's commands on the server, without a task: reset (start a paper's crosswalk over) and publish."""

import fcntl
import json
import os
import pwd
import subprocess
import sys

import pytest

import crosswalk_admin
import crosswalk_steps
from conftest import API, DOI


@pytest.fixture
def server(tmp_path, monkeypatch):
    crosswalks, site = tmp_path / "crosswalks", tmp_path / "site"
    (crosswalks / "00041").mkdir(parents=True)
    (crosswalks / "00041.crosswalk.yaml").write_text("paper: x\nversion: 1.2.0\n")
    (crosswalks / "00041.profiles.json").write_text("{}")
    monkeypatch.setattr(crosswalk_steps, "CROSSWALKS_PATH", crosswalks)
    monkeypatch.setattr(crosswalk_steps, "SITE_PATH", site)
    monkeypatch.setattr(crosswalk_steps, "GRAPH_DB_PATH", tmp_path / "graph.db")
    monkeypatch.setattr(crosswalk_steps, "STORE_LOCK_PATH", tmp_path / "store.lock")
    w = type("Server", (), {})()
    w.crosswalks, w.rebuilds, w.publishes = crosswalks, [], []
    w.publish_result = {"ok": True, "errors": []}
    monkeypatch.setattr(crosswalk_steps, "publish", lambda s, db, run=None: w.publishes.append((s, db)) or w.publish_result)
    monkeypatch.setattr(crosswalk_admin.myst_cdn, "rebuild", lambda: w.rebuilds.append(True) or {"projects": 3})
    return w


def test_reset_starts_the_paper_over_publishes_and_rebuilds_the_cdn(server, capsys, tmp_path):
    assert crosswalk_admin.main(["reset", DOI]) == 0
    out = capsys.readouterr().out
    assert not (server.crosswalks / "00041.crosswalk.yaml").exists() and (server.crosswalks / "00041.crosswalk.yaml.previous").exists()
    assert not (server.crosswalks / "00041").exists()
    assert server.publishes == [(tmp_path / "site", tmp_path / "graph.db")]
    assert server.rebuilds == [True]
    assert "The CDN was rebuilt without them." in out
    assert "Evidentron has no command to remove them." in out
    assert "Traceback" not in out and "—" not in out


def test_a_reset_with_nothing_confirmed_rebuilds_nothing(server, capsys):
    (server.crosswalks / "00041.crosswalk.yaml").unlink()
    assert crosswalk_admin.main(["reset", DOI]) == 0
    out = capsys.readouterr().out
    assert server.publishes == [] and server.rebuilds == []
    assert "rebuilt" not in out
    assert f"There is no confirmed crosswalk of {DOI} on the server." in out


def test_a_reset_whose_publish_failed_does_not_rebuild(server, capsys):
    server.publish_result = {"ok": False, "errors": ["The site could not be written: disk full."]}
    assert crosswalk_admin.main(["reset", DOI]) == 1
    assert server.rebuilds == []
    assert "Evidentron's site could not be published again" in capsys.readouterr().out


def test_a_reset_that_stopped_exits_1(server, monkeypatch, capsys):
    def refuse(src, dst):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(crosswalk_steps.os, "replace", refuse)
    assert crosswalk_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.strip() == "The reset stopped: Permission denied."
    assert server.publishes == [] and server.rebuilds == []


def test_reset_waits_for_a_task_that_holds_the_store(server, monkeypatch, capsys):
    monkeypatch.setattr(crosswalk_steps, "STORE_LOCK_WAIT", 0.2)
    with open(crosswalk_steps.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        assert crosswalk_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.strip() == ("Another task kept the paper store busy for more than 0.2 seconds, so "
                                               "nothing was done. Ask again in a while.")
    assert (server.crosswalks / "00041.crosswalk.yaml").exists() and server.rebuilds == []


def test_a_failed_rebuild_is_one_sentence(server, monkeypatch, capsys):
    def boom():
        raise RuntimeError("/DATA/cdn/current is a folder, not a symlink")
    monkeypatch.setattr(crosswalk_admin.myst_cdn, "rebuild", boom)
    assert crosswalk_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "The CDN could not be rebuilt (RuntimeError: /DATA/cdn/current is a folder, not a symlink); the documents "
        "leave it at the next rebuild.")


def test_the_admin_has_no_places_of_its_own():
    for name in ("CROSSWALKS_PATH", "GRAPH_DB_PATH", "SITE_PATH", "STORE_LOCK_PATH", "STORE_LOCK_WAIT", "EVIDENTRON_PATH"):
        assert not hasattr(crosswalk_admin, name)


def test_the_command_runs_from_anywhere(tmp_path):
    done = subprocess.run([sys.executable, str(API / "crosswalk_admin.py"), "--help"], capture_output=True, text=True,
                          cwd=tmp_path)
    assert done.returncode == 0 and "reset" in done.stdout


def test_publish_publishes_evidentrons_site_again_and_rebuilds_the_cdn(server, capsys):
    """After Evidentron changes (a new rule for kin, say), its site and index are published again from the store."""
    assert crosswalk_admin.main(["publish"]) == 0
    assert server.publishes == [(crosswalk_steps.SITE_PATH, crosswalk_steps.GRAPH_DB_PATH)] and server.rebuilds == [True]
    said = capsys.readouterr().out
    assert "published again" in said and "rebuilt" in said


def test_publish_without_evidentron_python_runs_on_the_provisioned_python(tmp_path, monkeypatch, capsys):
    """Run by hand, the command has no EVIDENTRON_PYTHON: Evidentron's publish runs on the provisioned Python."""
    monkeypatch.delenv("EVIDENTRON_PYTHON", raising=False)
    monkeypatch.setattr(crosswalk_steps, "PROVISIONED_PYTHON", str(tmp_path / "{user}" / "python"))
    provisioned = tmp_path / pwd.getpwuid(os.geteuid()).pw_name / "python"
    provisioned.parent.mkdir()
    provisioned.write_text("")
    monkeypatch.setattr(crosswalk_steps, "STORE_LOCK_PATH", tmp_path / "store.lock")
    monkeypatch.setattr(crosswalk_steps, "GRAPH_DB_PATH", tmp_path / "graph.db")
    ran = []
    monkeypatch.setattr(crosswalk_steps, "subprocess_runner",
                        lambda work: lambda argv: ran.append(argv) or (0, json.dumps({"ok": True})))
    monkeypatch.setattr(crosswalk_admin.myst_cdn, "rebuild", lambda: {})
    assert crosswalk_admin.main(["publish"]) == 0
    assert [argv[:3] for argv in ran] == [[str(provisioned), "-m", "evidentron.cli.data_cli"]]
    assert "Evidentron's site was published again." in capsys.readouterr().out


def test_the_admin_says_how_it_finds_evidentrons_python():
    assert "EVIDENTRON_PYTHON" in crosswalk_admin.__doc__
    assert "/home/<you>/venv/evidentron/bin/python" in crosswalk_admin.__doc__


def test_a_publish_that_fails_rebuilds_nothing_and_says_why(server, capsys):
    server.publish_result = {"ok": False, "errors": ["the store could not be read"]}
    assert crosswalk_admin.main(["publish"]) == 1
    assert server.rebuilds == []
    assert "the store could not be read" in capsys.readouterr().out
