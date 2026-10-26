"""The weft steps: each composes the evidentron commands, tested with a fake that writes what a command would."""

import os
import sys
from pathlib import Path

import pytest
import yaml

from api import weft_steps

DOI = "10.55458/neurolibre.00041"
WEFT_YAML = """paper: 10.55458/neurolibre.00041
files:
- file: participants.tsv
  row:
    term: participant
    description: One volunteer.
  fields:
  - {name: a, term: age}
  - {name: b, concept: sex}
- collection: scans
  member_count: 115
  description: One image of a participant.
  term: mri-image
  variables:
  - {name: shim, term: rf-shimming}
new_terms:
  rf-shimming:
    label: RF shimming
    status: proposed
version: 1.0.0
"""


class Fake:
    """Records argv; `script` maps a command word to a function(argv) -> (code, output)."""

    def __init__(self, work, script=None):
        self.work = work
        self.calls = []
        self.script = script or {}

    def words(self):
        return [c[3] if c[2] == "evidentron.weft" else c[2:5] for c in self.calls]

    def __call__(self, argv):
        self.calls.append(argv)
        key = argv[3] if argv[2] == "evidentron.weft" else "other"
        if key in self.script:
            return self.script[key](argv)
        if key == "other" and "publish" in argv:
            return self._publish(argv)
        return {
            "profile": self._profile, "explore": self._explore, "check": self._check,
            "confirm": self._confirm,
        }.get(key, lambda a: (0, ""))(argv)

    def _confirm(self, argv):
        """Mark every entry and item confirmed, as the command does when no names are given."""
        weft = Path(argv[4])
        data = yaml.safe_load(weft.read_text())
        for entry in data.get("files") or []:
            entry["status"] = "confirmed"
            for name in ("fields", "variables", "parts"):
                for item in entry.get(name) or []:
                    item["status"] = "confirmed"
        weft.write_text(yaml.safe_dump(data, sort_keys=False))
        return 0, "The version is now 1.1.0, dated 2026-10-26.\n"

    def _publish(self, argv):
        folder = Path(argv_value(argv, "--out")) / "wefts" / DOI
        folder.mkdir(parents=True, exist_ok=True)
        for fmt in ("fair2", "croissant"):
            (folder / f"{fmt}.json").write_text("{}")
        return 0, "Wrote 3 term files, metadata for 1 papers, and the FAIR² and Croissant documents of 1 papers.\n"

    def _profile(self, argv):
        Path(argv[argv.index("-o") + 1]).write_text("{}")
        return 0, "Profiled 2 files.\n"

    def _explore(self, argv):
        Path(argv[argv.index("-o") + 1]).write_text(WEFT_YAML)
        return 0, "The weft was written.\n"

    def _check(self, argv):
        return 0, "warning: participants.tsv: a unit is missing\nThe check found 0 errors and 1 warnings.\n"


@pytest.fixture
def dirs(tmp_path):
    work, data = tmp_path / "work", tmp_path / "data"
    work.mkdir()
    data.mkdir()
    return work, data, tmp_path / "lock"


BACKENDS = {"draft": "openai-compatible", "redraft": "anthropic-api", "terms": "anthropic-api"}


def argv_value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_draft_happy_path(dirs):
    work, data, lock = dirs
    fake = Fake(work)
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert result.drafted_by == "openai-compatible"
    assert result.reason is None
    assert result.weft == work / "00041.weft.yaml"
    assert result.profiles == work / "00041.profiles.json"
    assert fake.words() == ["profile", "explore", "check"]
    explore = fake.calls[1]
    assert argv_value(explore, "--backend") == "openai-compatible"
    assert argv_value(explore, "--lock") == str(lock)
    assert argv_value(explore, "--data") == str(data)
    assert "--repo" not in explore
    assert result.warnings == ["participants.tsv: a unit is missing"]
    assert result.problems == []
    assert any(line.startswith("participants.tsv: 2 columns") for line in result.summary)


def test_draft_passes_the_repository(dirs):
    work, data, lock = dirs
    fake = Fake(work)
    weft_steps.draft(DOI, data, work / "repo", work, BACKENDS, lock, run=fake)
    assert argv_value(fake.calls[1], "--repo") == str(work / "repo")


def test_draft_redrafts_when_the_model_server_is_down(dirs):
    work, data, lock = dirs
    explores = []

    def explore(argv):
        explores.append(argv_value(argv, "--backend"))
        if len(explores) == 1:
            return 1, "Trying the model\nConnection refused\n"
        Path(argv_value(argv, "-o")).write_text(WEFT_YAML)
        return 0, "The weft was written.\n"

    fake = Fake(work, {"explore": explore})
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert explores == ["openai-compatible", "anthropic-api"]
    assert result.drafted_by == "anthropic-api"
    assert result.reason.endswith("Connection refused")
    assert "--lock" not in fake.calls[2]


def test_draft_redrafts_when_the_check_finds_errors(dirs):
    work, data, lock = dirs
    checks = []

    def check(argv):
        checks.append(1)
        if len(checks) == 1:
            return 1, "error: a.tsv: no term\nThe check found 1 errors and 0 warnings.\n"
        return 0, "The check found 0 errors and 0 warnings.\n"

    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"check": check}))
    assert result.drafted_by == "anthropic-api"
    assert "no term" in result.reason
    assert result.problems == []


def test_draft_reports_when_both_backends_fail(dirs):
    work, data, lock = dirs
    fake = Fake(work, {"explore": lambda a: (1, "Connection refused\n")})
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert result.problems == ["Connection refused"]
    assert result.summary == []


def test_check_lists_errors_and_warnings(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    out = "error: a.tsv: no term\nwarning: b.tsv: no unit\nThe check found 1 errors and 1 warnings.\n"
    result = weft_steps.check(weft, profiles, data, run=Fake(work, {"check": lambda a: (1, out)}))
    assert result.errors == ["a.tsv: no term"]
    assert result.warnings == ["b.tsv: no unit"]
    assert result.unreadable is None


def test_check_of_an_unreadable_file(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text("files: [unclosed\n  - : :\n")
    profiles.write_text("{}")
    fake = Fake(work)
    result = weft_steps.check(weft, profiles, data, run=fake)
    assert result.errors == []
    assert result.unreadable
    assert "Traceback" not in result.unreadable
    assert fake.calls == []


def test_check_of_a_traceback_is_unreadable(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    out = "Traceback (most recent call last):\n  File x\nValueError: bad weft\n"
    result = weft_steps.check(weft, profiles, data, run=Fake(work, {"check": lambda a: (1, out)}))
    assert result.errors == []
    assert "bad weft" in result.unreadable


def confirm_args(work, data, **kw):
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    args = dict(weft=weft, profiles=profiles, by="An Author", fields=None, submission=None, data_dir=data,
                site=work / "site", db=work / "graph.db")
    args.update(kw)
    return args


def test_confirm_with_errors_stops_before_loading(dirs):
    work, data, _ = dirs
    out = "error: a.tsv: no term\nThe check found 1 errors and 0 warnings.\n"
    fake = Fake(work, {"check": lambda a: (1, out)})
    result = weft_steps.confirm(**confirm_args(work, data), run=fake)
    assert result.errors == ["a.tsv: no term"]
    assert result.addresses == {}
    assert fake.words() == ["check"]


def test_confirm_happy_path(dirs):
    work, data, _ = dirs
    fake = Fake(work)
    result = weft_steps.confirm(**confirm_args(work, data, fields=["a", "b"]), run=fake)
    assert result.errors == []
    assert result.version == "1.1.0"
    assert fake.words() == ["check", "confirm", "load", "export", "export", ["evidentron.cli.data_cli", "publish", "--out"]]
    confirm = fake.calls[1]
    assert argv_value(confirm, "--by") == "An Author"
    assert argv_value(confirm, "--fields") == "a,b"
    assert [argv_value(c, "--format") for c in fake.calls[3:5]] == ["fair2", "croissant"]
    assert all("--offline" not in c for c in fake.calls[3:5])
    assert argv_value(fake.calls[5], "--out") == str(work / "site")
    base = "https://cdn.evidencepub.io"
    assert result.addresses["fair2"] == f"{base}/wefts/{DOI}/fair2.json"
    assert result.addresses["croissant"] == f"{base}/wefts/{DOI}/croissant.json"
    assert result.addresses["metadata"] == f"{base}/dois/{DOI}.json"
    assert result.addresses["terms"] == [f"{base}/terms/{t}/" for t in
                                         ["age", "mri-image", "participant", "rf-shimming", "sex"]]


def test_confirm_with_a_submission_applies_it_in_the_confirm_command(dirs):
    work, data, _ = dirs
    sub = work / "sub.yaml"
    sub.write_text("files: []\n")
    fake = Fake(work)
    weft_steps.confirm(**confirm_args(work, data, submission=sub), run=fake)
    confirm = next(c for c in fake.calls if c[3] == "confirm")
    assert argv_value(confirm, "--submit") == str(sub)
    assert "--profiles" in confirm


def test_confirm_refused_by_the_command_returns_its_sentence(dirs):
    work, data, _ = dirs
    fake = Fake(work, {"confirm": lambda a: (1, "Nothing was confirmed: no such field.\n")})
    result = weft_steps.confirm(**confirm_args(work, data), run=fake)
    assert result.errors == ["Nothing was confirmed: no such field."]
    assert fake.words() == ["check", "confirm"]


def test_link_terms_counts_the_terms_kept(dirs):
    work, _, _ = dirs
    fake = Fake(work, {"other": lambda a: (0, f"{DOI}: 4 terms, proposed x\n")})
    assert weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake) == (4, None)
    argv = fake.calls[0]
    assert argv[:3] == [sys.executable, "-m", "evidentron.graph"]
    assert argv_value(argv, "--paper") == DOI
    assert argv_value(argv, "--backend") == "anthropic-api"


def test_summary_of(tmp_path):
    weft = tmp_path / "w.yaml"
    weft.write_text(WEFT_YAML)
    assert weft_steps.summary_of(weft) == [
        "participants.tsv: 2 columns; one row is one volunteer",
        "scans: a collection of 115 files; one member is one image of a participant",
        "proposed new term rf-shimming: RF shimming",
    ]


def test_summary_of_an_unreadable_file_is_empty(tmp_path):
    assert weft_steps.summary_of(tmp_path / "missing.yaml") == []


REAL_SHAPE = """paper: 10.55458/neurolibre.00004
files:
- file: repo2data.log
  kind: binary
  description: A log written by the data download tool.
- file: notes.bin
  kind: binary
- file: database.xlsx#Details
  row:
    term: study
    description: One row is one published study.
  fields:
  - {name: a, term: study}
- collection: logs
  description: One member is a log file.
new_terms:
""" + "".join(f"  t{i}:\n    label: term {i}\n" for i in range(13))


def test_summary_of_a_real_shape(tmp_path):
    weft = tmp_path / "w.yaml"
    weft.write_text(REAL_SHAPE)
    lines = weft_steps.summary_of(weft)
    assert lines[0] == "repo2data.log: a binary file; A log written by the data download tool"
    assert lines[1] == "notes.bin: a binary file"
    assert lines[2] == "database.xlsx#Details: 1 columns; one row is one published study"
    assert lines[3] == "logs: a collection of files; one member is a log file"
    assert not any("0 columns" in line for line in lines)
    terms = [line for line in lines if line.startswith("proposed new term")]
    assert len(terms) == 10
    assert lines[-1] == "and 3 more proposed terms"


def test_summary_of_new_terms_that_is_not_a_mapping(tmp_path):
    weft = tmp_path / "w.yaml"
    weft.write_text("files: []\nnew_terms: [a, b]\n")
    assert weft_steps.summary_of(weft) == []


def test_summary_of_a_binary_file_is_empty(tmp_path):
    weft = tmp_path / "w.yaml"
    weft.write_bytes(b"\xff\xfe\x00\x80")
    assert weft_steps.summary_of(weft) == []


def test_confirm_of_an_unreadable_weft_with_a_submission(dirs):
    work, data, _ = dirs
    args = confirm_args(work, data, submission=work / "sub.yaml")
    args["weft"].write_text("files: [unclosed\n  - : :\n")
    fake = Fake(work)
    result = weft_steps.confirm(**args, run=fake)
    assert result.errors and "YAML" in result.errors[0]
    assert fake.calls == []


def test_confirm_with_a_refused_submission_returns_every_error(dirs):
    work, data, _ = dirs
    out = ("The submission leaves the weft with errors, so nothing was confirmed:\n"
           "- a.tsv: no term\n- b.tsv: no description\n")
    fake = Fake(work, {"confirm": lambda a: (1, out)})
    result = weft_steps.confirm(**confirm_args(work, data, submission=work / "sub.yaml"), run=fake)
    assert result.errors == ["a.tsv: no term", "b.tsv: no description"]


def test_a_traceback_from_a_later_step_is_one_sentence(dirs):
    work, data, _ = dirs
    trace = "Traceback (most recent call last):\n  File x\n    line\nOSError: disk full\n"
    for step in ("load", "export"):
        fake = Fake(work, {step: lambda a: (1, trace)})
        result = weft_steps.confirm(**confirm_args(work, data), run=fake)
        assert len(result.errors) == 1 and "disk full" in result.errors[0]
        assert "Traceback" not in result.errors[0]
    fake = Fake(work, {"confirm": lambda a: (1, trace)})
    result = weft_steps.confirm(**confirm_args(work, data), run=fake)
    assert len(result.errors) == 1 and "Traceback" not in result.errors[0]


def test_publish_runs_in_the_store_folder(dirs, monkeypatch):
    work, data, _ = dirs
    seen = []
    monkeypatch.setattr(weft_steps, "subprocess_runner", lambda cwd: (lambda argv: seen.append((cwd, argv)) or (0, "")))
    db = work / "store" / "graph.db"
    weft_steps.confirm(**confirm_args(work, data, db=db))
    publishes = [cwd for cwd, argv in seen if "publish" in argv]
    assert publishes == [work / "store"]


# R1: the commands run on Evidentron's own Python when the worker names one.

def test_the_commands_run_on_evidentrons_python(dirs, monkeypatch):
    work, data, lock = dirs
    monkeypatch.setenv("EVIDENTRON_PYTHON", "/opt/evidentron/bin/python")
    seen = []
    weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=lambda argv: seen.append(argv) or (0, ""))
    assert seen[0][:3] == ["/opt/evidentron/bin/python", "-m", "evidentron.graph"]
    assert weft_steps.weft_command()[0] == "/opt/evidentron/bin/python"
    assert weft_steps.data_cli_command() == ["/opt/evidentron/bin/python", "-m", "evidentron.cli.data_cli"]
    monkeypatch.delenv("EVIDENTRON_PYTHON")
    assert weft_steps.graph_command() == [sys.executable, "-m", "evidentron.graph"]


# I1: why the self-hosted draft failed, from what the explore command really writes.

def failed_explore(problems=None, stdout="", stderr=""):
    """explore as Evidentron writes it: the weft with # PROBLEM: lines at the top, and the path in its last line."""
    def explore(argv):
        out = Path(argv_value(argv, "-o"))
        if argv_value(argv, "--backend") == "anthropic-api":
            out.write_text(WEFT_YAML)
            return 0, "The weft was written.\n"
        if problems is not None:
            out.write_text("".join(f"# PROBLEM: {p}\n" for p in problems) + WEFT_YAML)
        return 1, weft_steps.Output(stdout + stderr, stdout=stdout)
    return explore


def test_the_reason_is_the_problem_at_the_top_of_the_weft(dirs):
    work, data, lock = dirs
    stdout = ("step 1\nThe weft was not accepted.\n"
              f"The weft was written to {work}/00041.weft.yaml, with its 2 problems listed at the top.\n")
    explore = failed_explore(["The exploration stopped with an error, and nothing was kept: ConnectError: refused.",
                              "a.tsv has no description."], stdout=stdout)
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.reason == "The exploration stopped with an error, and nothing was kept: ConnectError: refused."
    assert str(work) not in result.reason


def test_a_busy_slot_is_the_reason(dirs):
    work, data, lock = dirs
    stdout = "The exploration did not start: the self-hosted model's slot was busy for 3600 s.\n"
    explore = failed_explore(stdout=stdout, stderr="UserWarning: something about a library\n")
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.reason == "The exploration did not start: the self-hosted model's slot was busy for 3600 s."


def test_a_warning_on_stderr_is_not_the_reason(dirs):
    work, data, lock = dirs
    explore = failed_explore(stdout="Trying the model\nConnection refused\n", stderr="DeprecationWarning: old api\n")
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.reason == "Connection refused"


def test_the_runner_keeps_stdout_apart(tmp_path):
    run = weft_steps.subprocess_runner(tmp_path)
    code, output = run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert code == 0
    assert "out" in output and "err" in output
    assert output.stdout.strip() == "out"


def test_a_failed_redraft_lists_its_problems_not_its_path(dirs):
    work, data, lock = dirs

    def explore(argv):
        out = Path(argv_value(argv, "-o"))
        out.write_text("# PROBLEM: a.tsv has no term.\n# PROBLEM: b.tsv has no row.\n" + WEFT_YAML)
        return 1, f"The weft was written to {out}, with its 2 problems listed at the top.\n"
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.problems == ["a.tsv has no term.", "b.tsv has no row."]


def test_the_self_hosted_draft_is_kept_beside_the_redraft(dirs):
    work, data, lock = dirs
    explore = failed_explore(["a.tsv has no description."])
    result = weft_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    kept = work / "00041.selfhosted.weft.yaml"
    assert result.selfhosted == kept
    assert kept.read_text().startswith("# PROBLEM: a.tsv has no description.")
    assert not result.weft.read_text().startswith("# PROBLEM")


# I2: an unreadable weft says where.

def test_unreadable_yaml_says_where(dirs):
    work, data, _ = dirs
    weft = work / "00041.weft.yaml"
    weft.write_text("a: 1\nb: [1, 2\nc: 3\n")
    result = weft_steps.check(weft, work / "p.json", data, run=Fake(work))
    assert result.unreadable == ("The weft is not valid YAML, so it could not be checked: "
                                 "line 3, column 2: expected ',' or ']', but got ':'.")


# C2 and I5: the confirmed weft's place of record, and the version measured against it.

def test_confirm_keeps_the_confirmed_weft_in_the_flat_folder(dirs):
    work, data, _ = dirs
    record = work.parent / "wefts"
    record.mkdir()
    args = confirm_args(work, data)
    fake = Fake(work)
    result = weft_steps.confirm(**args, run=fake, record=record)
    assert result.errors == [] and result.confirmed
    assert (record / "00041.weft.yaml").read_text() == args["weft"].read_text()
    assert (record / "00041.profiles.json").read_text() == "{}"
    assert "--previous" not in fake.calls[1]


def test_confirm_measures_against_the_confirmed_copy(dirs):
    work, data, _ = dirs
    record = work.parent / "wefts"
    record.mkdir()
    (record / "00041.weft.yaml").write_text(WEFT_YAML)
    fake = Fake(work)
    weft_steps.confirm(**confirm_args(work, data), run=fake, record=record)
    confirm = next(c for c in fake.calls if c[3] == "confirm")
    assert argv_value(confirm, "--previous") == str(record / "00041.weft.yaml")


def test_confirm_fails_with_publishs_sentence_when_the_documents_are_not_written(dirs):
    work, data, _ = dirs
    refused = f"No weft of {DOI} is in the server's folder of wefts: it looks for 00041.weft.yaml there."
    fake = Fake(work, {"other": lambda a: (0, "Wrote 3 term files.\n- The terms are published without QUDT units: x\n"
                                              f"- {refused}\n")})
    result = weft_steps.confirm(**confirm_args(work, data), run=fake, record=work.parent / "wefts")
    assert result.errors == [refused]
    assert result.confirmed and result.version == "1.1.0"
    assert result.addresses == {}


def test_confirm_does_not_take_old_documents_for_new_ones(dirs):
    work, data, _ = dirs
    folder = work / "site" / "wefts" / DOI
    folder.mkdir(parents=True)
    for fmt in ("fair2", "croissant"):
        (folder / f"{fmt}.json").write_text("{}")
        os.utime(folder / f"{fmt}.json", (1, 1))
    fake = Fake(work, {"other": lambda a: (0, "Wrote 3 term files.\n")})
    result = weft_steps.confirm(**confirm_args(work, data), run=fake, record=work.parent / "wefts")
    assert result.errors == [f"The FAIR² and Croissant documents of {DOI} were not written when the site was published."]


# Triaged minors.

def test_the_terms_listed_are_those_of_confirmed_items(tmp_path):
    weft = yaml.safe_load(WEFT_YAML)
    assert weft_steps._terms_used(weft) == []
    weft["files"][0]["fields"][0]["status"] = "confirmed"
    assert weft_steps._terms_used(weft) == ["age", "participant"]


def test_link_terms_that_fail_say_why(dirs):
    work, _, _ = dirs
    fake = Fake(work, {"other": lambda a: (0, f"{DOI}: The model's answer was not JSON\n")})
    assert weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake) == \
        (None, "The model's answer was not JSON")
    trace = "Traceback (most recent call last):\n  File x\nOSError: disk full\n"
    count, why = weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=Fake(work, {"other": lambda a: (1, trace)}))
    assert count is None and "disk full" in why and "Traceback" not in why


# B7: a check with a submission applies it through `weft confirm --submit` on a scratch copy, then discards it.

def test_check_with_a_submission_confirms_a_scratch_copy_and_keeps_the_weft(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    sub = work / "submission.yaml"
    sub.write_text("files: []\n")
    seen = {}

    def confirm(argv):
        seen["argv"], seen["copy"] = argv, Path(argv[4])
        seen["copied"] = Path(argv[4]).read_text()
        return Fake(work)._confirm(argv)
    fake = Fake(work, {"confirm": confirm})
    result = weft_steps.check(weft, profiles, data, run=fake, submission=sub, by="ada")
    assert fake.words() == ["confirm"]
    argv = seen["argv"]
    assert argv_value(argv, "--submit") == str(sub) and argv_value(argv, "--by") == "ada"
    assert argv_value(argv, "--profiles") == str(profiles)
    assert seen["copy"] != weft and seen["copied"] == WEFT_YAML
    assert not seen["copy"].exists()                    # the scratch folder is gone
    assert weft.read_text() == WEFT_YAML                # the weft is not confirmed
    assert result.errors == [] and result.warnings == [] and result.unreadable is None


def test_check_with_a_refused_submission_lists_its_errors_and_notes(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    sub = work / "submission.yaml"
    sub.write_text("files: []\n")
    out = ("There was no field z in participants.tsv to remove.\n"
           "The submission leaves the weft with errors, so nothing was confirmed:\n"
           "- participants.tsv: a has no term\n- scans: no description\n")
    result = weft_steps.check(weft, profiles, data, run=Fake(work, {"confirm": lambda a: (1, out)}),
                              submission=sub, by="ada")
    assert result.errors == ["participants.tsv: a has no term", "scans: no description"]
    assert result.warnings == ["There was no field z in participants.tsv to remove."]


def test_check_with_a_submission_that_passes_says_its_notes(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    sub = work / "submission.yaml"
    sub.write_text("files: []\n")
    out = "There was no entry for x.tsv to remove.\nThe version is now 1.1.0, dated 2026-10-26.\n"
    result = weft_steps.check(weft, profiles, data, run=Fake(work, {"confirm": lambda a: (0, out)}),
                              submission=sub, by="ada")
    assert result.errors == []
    assert result.warnings == ["There was no entry for x.tsv to remove."]


def test_check_with_a_submission_not_applied_is_an_error(dirs):
    work, data, _ = dirs
    weft, profiles = work / "00041.weft.yaml", work / "00041.profiles.json"
    weft.write_text(WEFT_YAML)
    profiles.write_text("{}")
    sub = work / "submission.yaml"
    sub.write_text("files: 3\n")
    out = f"The submission in {sub} was not applied, so nothing was confirmed: it must be one mapping with a files list\n"
    result = weft_steps.check(weft, profiles, data, run=Fake(work, {"confirm": lambda a: (1, out)}),
                              submission=sub, by="ada")
    assert result.errors == [out.strip()] and result.warnings == []
