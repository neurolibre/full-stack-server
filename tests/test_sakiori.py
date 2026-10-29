"""Sakiori's build on the server: crawl into a new build, carry the notes and answers over, write the asked papers'
notes and answers, and serve the new build by moving one link; a failed crawl leaves the served build alone, and a
failed note or answer is said while the build is still served."""

import json
import os

import sakiori


class Steps:
    """Runs the steps as the scripts would, by writing their files; `fail` names the steps that fail."""

    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def __call__(self, argv, cwd, env, timeout):
        script = os.path.basename(argv[1])
        self.calls.append((script, argv, cwd, env))
        step = script.removesuffix(".py")
        if step in self.fail:
            return 1, f"{step} went wrong\nthe last line says why"
        data = argv[argv.index("--out" if step == "crawl" else "--data") + 1]
        papers = [a for a in argv[2:] if a.isdigit()] or ["00052"]
        if step == "crawl":
            os.makedirs(f"{data}/walks")
            open(f"{data}/graph.json", "w").write("{}")
            for n in ("00012", "00052"):
                open(f"{data}/walks/{n}.json", "w").write("{}")
        else:
            folder = f"{data}/{'notes' if step == 'notes' else 'moments'}"
            os.makedirs(folder, exist_ok=True)
            for n in papers:
                open(f"{folder}/{n}.json", "w").write(json.dumps({"by": step}))
        return 0, ""


def build(tmp_path, steps, papers=("00052",), env=None, stamp="20261030-120000"):
    return sakiori.build(list(papers), root=tmp_path / "sakiori", evidentron=tmp_path / "evidentron", run=steps,
                         env={"EVIDENTRON_LLM_URL": "http://model", **(env or {})}, stamp=stamp)


def served(tmp_path):
    return tmp_path / "sakiori" / "data" / "current"


def test_a_build_crawls_writes_the_papers_notes_and_answer_and_is_served(tmp_path):
    steps = Steps()
    got = build(tmp_path, steps)
    assert got["ok"]
    assert [c[0] for c in steps.calls] == ["crawl.py", "notes.py", "moment.py"]
    assert steps.calls[1][1][-1] == "00052" and steps.calls[2][1][-1] == "00052"      # only the asked paper
    assert steps.calls[2][3]["EVIDENTRON_DIR"] == str(tmp_path / "evidentron")       # the agent runs from its folder
    assert (served(tmp_path) / "walks" / "00012.json").exists()
    assert (served(tmp_path) / "notes" / "00052.json").exists()
    assert (served(tmp_path) / "moments" / "00052.json").exists()
    assert os.path.islink(served(tmp_path))


def test_a_named_paper_is_answered_again_and_a_full_build_only_fills_the_gaps(tmp_path):
    steps = Steps()
    build(tmp_path, steps, papers=("00052",), stamp="20261030-120000")
    build(tmp_path, steps, papers=(), stamp="20261030-130000")
    answers = [c[1] for c in steps.calls if c[0] == "moment.py"]
    assert "--force" in answers[0] and "--force" not in answers[1]


def test_the_other_papers_keep_their_notes_and_answers(tmp_path):
    build(tmp_path, Steps(), papers=("00012",), stamp="20261030-120000")
    build(tmp_path, Steps(), papers=("00052",), stamp="20261030-130000")
    assert (served(tmp_path) / "notes" / "00012.json").exists()                # carried over from the build before
    assert (served(tmp_path) / "moments" / "00052.json").exists()


def test_a_failed_crawl_leaves_the_served_build_alone(tmp_path):
    build(tmp_path, Steps(), stamp="20261030-120000")
    before = os.readlink(served(tmp_path))
    got = build(tmp_path, Steps(fail={"crawl"}), stamp="20261030-130000")
    assert not got["ok"]
    assert "the last line says why" in got["said"][0]
    assert os.readlink(served(tmp_path)) == before
    assert not (tmp_path / "sakiori" / "data" / "builds" / "20261030-130000").exists()


def test_a_failed_note_or_answer_is_said_and_the_build_is_still_served(tmp_path):
    got = build(tmp_path, Steps(fail={"moment"}))
    assert got["ok"]
    assert any("answer" in s and "the last line says why" in s for s in got["said"])
    assert (served(tmp_path) / "walks" / "00052.json").exists()


def test_without_the_model_address_the_notes_are_skipped_and_said(tmp_path):
    steps = Steps()
    got = sakiori.build(["00052"], root=tmp_path / "sakiori", evidentron=tmp_path / "evidentron", run=steps, env={},
                        stamp="20261030-120000")
    assert [c[0] for c in steps.calls] == ["crawl.py", "moment.py"]
    assert any("EVIDENTRON_LLM_URL" in s for s in got["said"])


def test_only_the_two_latest_builds_are_kept(tmp_path):
    for hour in ("10", "11", "12"):
        build(tmp_path, Steps(), stamp=f"20261030-{hour}0000")
    left = sorted(p.name for p in (tmp_path / "sakiori" / "data" / "builds").iterdir())
    assert left == ["20261030-110000", "20261030-120000"]
    assert os.readlink(served(tmp_path)).endswith("20261030-120000")
