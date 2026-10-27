"""The editor's reset command: start a paper's description over, without a task."""

import fcntl
import subprocess
import sys

import pytest

import weft_admin
from conftest import API, DOI


@pytest.fixture
def server(tmp_path, monkeypatch):
    wefts, site = tmp_path / "wefts", tmp_path / "site"
    wefts.mkdir()
    (wefts / "00041.weft.yaml").write_text("paper: x\nversion: 1.2.0\n")
    (wefts / "00041.profiles.json").write_text("{}")
    (site / "wefts" / DOI).mkdir(parents=True)
    monkeypatch.setattr(weft_admin, "WEFTS_PATH", wefts)
    monkeypatch.setattr(weft_admin, "SITE_PATH", site)
    monkeypatch.setattr(weft_admin, "GRAPH_DB_PATH", tmp_path / "graph.db")
    monkeypatch.setattr(weft_admin, "STORE_LOCK_PATH", tmp_path / "store.lock")
    rebuilds = []
    monkeypatch.setattr(weft_admin.myst_cdn, "rebuild", lambda: rebuilds.append(True) or {"projects": 3})
    return wefts, site, rebuilds


def test_reset_starts_the_paper_over_and_rebuilds_the_cdn(server, capsys):
    wefts, site, rebuilds = server
    assert weft_admin.main(["reset", DOI]) == 0
    out = capsys.readouterr().out
    assert not (wefts / "00041.weft.yaml").exists() and (wefts / "00041.weft.yaml.previous").exists()
    assert not (site / "wefts" / DOI).exists()
    assert rebuilds == [True]
    assert "The CDN was rebuilt without them." in out
    assert "Evidentron has no command to remove them." in out
    assert "Traceback" not in out and "—" not in out


def test_reset_waits_for_a_task_that_holds_the_store(server, monkeypatch, capsys):
    wefts, _, rebuilds = server
    monkeypatch.setattr(weft_admin, "STORE_LOCK_WAIT", 0.2)
    with open(weft_admin.STORE_LOCK_PATH, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        assert weft_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.strip() == ("Another task kept the paper store busy for more than 0.2 seconds, so "
                                               "nothing was reset. Try again in a while.")
    assert (wefts / "00041.weft.yaml").exists() and rebuilds == []


def test_a_failed_rebuild_is_one_sentence(server, monkeypatch, capsys):
    def boom():
        raise RuntimeError("/DATA/cdn/current is a folder, not a symlink")
    monkeypatch.setattr(weft_admin.myst_cdn, "rebuild", boom)
    assert weft_admin.main(["reset", DOI]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "The CDN could not be rebuilt (RuntimeError: /DATA/cdn/current is a folder, not a symlink); the documents "
        "leave it at the next rebuild.")


def test_the_command_runs_from_anywhere(tmp_path):
    done = subprocess.run([sys.executable, str(API / "weft_admin.py"), "--help"], capture_output=True, text=True,
                          cwd=tmp_path)
    assert done.returncode == 0 and "reset" in done.stdout
