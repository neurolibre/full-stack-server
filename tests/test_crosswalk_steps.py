"""The steps of a paper's crosswalk: each runs the evidentron commands with --json and reads the one object each
prints. Tested with a fake that writes what a command would and answers with the command's JSON."""

import json
import os
import pwd
import sys
import time
from pathlib import Path

import pytest

import crosswalk_steps
from conftest import DOI, said

CROSSWALK_YAML = """paper: 10.55458/neurolibre.00041
files:
- file: participants.tsv
  row:
    term: participant
    description: One volunteer.
  fields:
  - {name: a, term: age}
  - {name: b, concept: sex}
version: 1.0.0
"""
SUMMARY = ["`participants.tsv`: a table with 2 columns. One row is one volunteer."]
CDN = "https://cdn.example.org"
ADDRESSES = {"metadata": f"{CDN}/dois/{DOI}.json", "fair2": f"{CDN}/crosswalks/{DOI}/fair2.json",
             "croissant": f"{CDN}/crosswalks/{DOI}/croissant.json"}


def argv_value(argv, flag):
    return argv[argv.index(flag) + 1]


class Fake:
    """Records argv; `script` maps a command word to a function(argv) -> (code, output)."""

    def __init__(self, work, script=None):
        self.work = work
        self.calls = []
        self.script = script or {}

    @staticmethod
    def word(argv):
        if argv[2] == "evidentron.crosswalk":
            return argv[3]
        return "publish" if "publish" in argv else "concepts" if "concepts" in argv else "graph load"

    def words(self):
        return [self.word(c) for c in self.calls]

    def __call__(self, argv):
        self.calls.append(argv)
        key = self.word(argv)
        if key in self.script:
            return self.script[key](argv)
        return getattr(self, "_" + key.replace(" ", "_"))(argv)

    def _profile(self, argv):
        Path(argv_value(argv, "-o")).write_text("{}")
        return 0, said("profile", profiles=argv_value(argv, "-o"), summary=["`p.tsv`: a table with 2 columns."])

    def _explore(self, argv):
        Path(argv_value(argv, "-o")).write_text(CROSSWALK_YAML)
        return 0, said("explore", crosswalk=argv_value(argv, "-o"), summary=SUMMARY,
                       drafted_by={"backend": argv_value(argv, "--backend"), "model": "a-model"})

    def _check(self, argv):
        return 0, said("check", warnings=["participants.tsv: a unit is missing"])

    def _confirm(self, argv):
        return 0, said("confirm", version="1.1.0", modified="2026-10-27", terms=[{"id": "age", "label": "age"}, {"id": "participant", "label": None}],
                       confirmed=[{"entry": "participants.tsv", "name": "a"}])

    def _load(self, argv):
        return 0, said("load", fields=2, paper=DOI)

    def _export(self, argv):
        return 0, said("export", written=f"/tmp/x.{argv_value(argv, '--format')}.json")

    def _publish(self, argv):
        return 0, said("publish", exported=[DOI], metadata=[DOI], addresses={DOI: ADDRESSES},
                       terms_url=f"{CDN}/terms/", folder="/x/site.1")

    def _concepts(self, argv):
        return 0, said("graph concepts", linked=2, terms=[{"id": "crop-yield", "label": "crop yield"},
                                                          {"id": "plot", "label": None}])

    def _graph_load(self, argv):
        return 0, said("graph", papers=[{"doi": argv_value(argv, "--doi"), "loaded": True}])


@pytest.fixture
def dirs(tmp_path):
    work, data = tmp_path / "work", tmp_path / "data"
    work.mkdir()
    data.mkdir()
    return work, data, tmp_path / "lock"


BACKENDS = {"draft": "openai-compatible", "redraft": "anthropic-api", "terms": "anthropic-api"}


# Every command is asked for its JSON, and its printed text is never read.

def test_draft_happy_path(dirs):
    work, data, lock = dirs
    fake = Fake(work)
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert fake.words() == ["profile", "explore", "check"]
    assert all("--json" in argv for argv in fake.calls)
    explore = fake.calls[1]
    assert argv_value(explore, "--backend") == "openai-compatible"
    assert argv_value(explore, "--lock") == str(lock)
    assert argv_value(explore, "--data") == str(data)
    assert "--repo" not in explore
    assert result.drafted_by == "openai-compatible" and result.model == "a-model"
    assert result.reason is None
    assert result.crosswalk == work / "00041.crosswalk.yaml" and result.profiles == work / "00041.profiles.json"
    assert result.warnings == ["participants.tsv: a unit is missing"]
    assert result.problems == []
    assert result.summary == SUMMARY                     # the command's own summary


def test_draft_passes_the_repository(dirs):
    work, data, lock = dirs
    fake = Fake(work)
    crosswalk_steps.draft(DOI, data, work / "repo", work, BACKENDS, lock, run=fake)
    assert argv_value(fake.calls[1], "--repo") == str(work / "repo")


def test_draft_redrafts_when_the_model_server_is_down(dirs):
    work, data, lock = dirs
    explores = []

    def explore(argv):
        explores.append(argv_value(argv, "--backend"))
        if len(explores) == 1:
            return 1, said("explore", errors=["The exploration did not start: the model server refused the connection."])
        return Fake(work)._explore(argv)

    fake = Fake(work, {"explore": explore})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert explores == ["openai-compatible", "anthropic-api"]
    assert result.drafted_by == "anthropic-api"
    assert result.reason == "The exploration did not start: the model server refused the connection."
    assert "--lock" not in fake.calls[2]


def test_the_reason_is_the_first_problem_of_the_draft(dirs):
    work, data, lock = dirs

    def explore(argv):
        if argv_value(argv, "--backend") == "anthropic-api":
            return Fake(work)._explore(argv)
        Path(argv_value(argv, "-o")).write_text("# PROBLEM: a.tsv has no description.\n" + CROSSWALK_YAML)
        problems = ["a.tsv has no description.", "b.tsv has no row."]
        return 1, said("explore", problems=problems, errors=["another error"] + problems)
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.reason == "a.tsv has no description."
    kept = work / "00041.selfhosted.crosswalk.yaml"
    assert result.selfhosted == kept
    assert kept.read_text().startswith("# PROBLEM: a.tsv has no description.")
    assert result.crosswalk.read_text() == CROSSWALK_YAML


def test_draft_redrafts_when_the_check_finds_errors(dirs):
    work, data, lock = dirs
    checks = []

    def check(argv):
        checks.append(1)
        if len(checks) == 1:
            return 1, said("check", errors=["a.tsv: no term"])
        return 0, said("check")

    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"check": check}))
    assert result.drafted_by == "anthropic-api"
    assert result.reason == "a.tsv: no term"
    assert result.problems == []


def test_draft_reports_when_both_backends_fail(dirs):
    work, data, lock = dirs
    explore = lambda a: (1, said("explore", problems=["a.tsv has no term."], errors=["a.tsv has no term."]))
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.problems == ["a.tsv has no term."]
    assert result.summary == []


# Data with files that are neither tables nor text goes to the strongest backend from the start (made-up papers
# from other fields: a sonar survey's .xtf lines and gridded depths, a herbarium's scans).

def profile_of(*files):
    def profile(argv):
        Path(argv_value(argv, "-o")).write_text(json.dumps({"files": [{"path": p, "kind": k} for p, k in files]}))
        return 0, said("profile", profiles=argv_value(argv, "-o"), summary=["files"])
    return profile


SONAR = profile_of(("lines/L1.xtf", "binary"), ("lines/L2.xtf", "binary"), ("grids/depth.nc.gz", "binary"),
                   ("README.md", "not-read"), ("lines.csv", "table"))


def test_data_with_files_that_are_not_tables_is_drafted_by_the_strongest_backend_from_the_start(dirs):
    work, data, lock = dirs
    fake = Fake(work, {"profile": SONAR})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    explores = [argv for argv in fake.calls if fake.word(argv) == "explore"]
    assert [argv_value(a, "--backend") for a in explores] == ["anthropic-api"]
    assert "--lock" not in explores[0]                    # the self-hosted model's queue is not waited on
    assert result.drafted_by == "anthropic-api" and result.selfhosted is None
    assert result.reason == ("its data holds 3 files that are not tables (2 .xtf, 1 .nc.gz), which the self-hosted "
                             "model describes poorly")


def test_data_of_tables_and_text_only_is_drafted_by_the_self_hosted_model(dirs):
    work, data, lock = dirs
    fake = Fake(work, {"profile": profile_of(("plots.csv", "table"), ("README.md", "not-read"))})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert result.drafted_by == "openai-compatible" and result.reason is None


def test_a_strongest_backend_draft_that_fails_its_checks_is_kept_for_review_not_drafted_again(dirs):
    work, data, lock = dirs
    fake = Fake(work, {"profile": SONAR, "check": lambda argv: (1, said("check", errors=["lines: no term"]))})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert fake.words().count("explore") == 1
    assert result.problems == ["lines: no term"] and result.crosswalk.exists() and result.summary == SUMMARY


def test_a_strongest_backend_that_cannot_draft_says_why(dirs):
    work, data, lock = dirs
    explore = lambda a: (1, said("explore", errors=["The exploration did not start: the API refused the key."]))
    fake = Fake(work, {"profile": SONAR, "explore": explore})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert result.problems == ["The exploration did not start: the API refused the key."]
    assert fake.words().count("explore") == 1


def test_a_profile_that_fails_says_why_and_leaves_the_earlier_draft(dirs):
    work, data, lock = dirs
    (work / "00041.crosswalk.yaml").write_text("the earlier draft")
    fake = Fake(work, {"profile": lambda a: (1, said("profile", errors=["The folder is empty."]))})
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=fake)
    assert result.problems == ["The data could not be profiled: The folder is empty."]
    assert fake.words() == ["profile"]
    assert (work / "00041.crosswalk.yaml").read_text() == "the earlier draft"


def test_the_earlier_draft_is_removed_right_before_explore(dirs):
    work, data, lock = dirs
    (work / "00041.crosswalk.yaml").write_text("the earlier draft")
    (work / "00041.selfhosted.crosswalk.yaml").write_text("the earlier self-hosted draft")
    seen = {}

    def explore(argv):
        seen["crosswalk"] = (work / "00041.crosswalk.yaml").exists()
        seen["selfhosted"] = (work / "00041.selfhosted.crosswalk.yaml").exists()
        return Fake(work)._explore(argv)

    def profile(argv):
        seen["at profile"] = (work / "00041.crosswalk.yaml").read_text()
        return Fake(work)._profile(argv)
    crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore, "profile": profile}))
    assert seen == {"at profile": "the earlier draft", "crosswalk": False, "selfhosted": False}


def test_a_command_that_prints_no_json_is_one_sentence(dirs):
    work, data, lock = dirs
    crash = crosswalk_steps.Output("", "Traceback (most recent call last):\n  File x\nModuleNotFoundError: No module named 'yaml'\n")
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"profile": lambda a: (1, crash)}))
    assert result.problems == ["The data could not be profiled: The command gave no result: "
                               "ModuleNotFoundError: No module named 'yaml'."]
    silent = crosswalk_steps.Output("", "")
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"profile": lambda a: (1, silent)}))
    assert result.problems == ["The data could not be profiled: The command gave no result and wrote nothing."]


# The check.

def seed(work):
    crosswalk, profiles = work / "00041.crosswalk.yaml", work / "00041.profiles.json"
    crosswalk.write_text(CROSSWALK_YAML)
    profiles.write_text("{}")
    return crosswalk, profiles


def test_check_lists_errors_warnings_and_notes(dirs):
    work, data, _ = dirs
    crosswalk, profiles = seed(work)
    out = said("check", errors=["a.tsv: no term"], warnings=["b.tsv: no unit"],
               notes=["The paper's text was not found, so the cited blocks were not checked."])
    fake = Fake(work, {"check": lambda a: (1, out)})
    result = crosswalk_steps.check(crosswalk, profiles, data, run=fake, db=work / "graph.db")
    assert result.errors == ["a.tsv: no term"]
    assert result.warnings == ["b.tsv: no unit"]
    assert result.notes == ["The paper's text was not found, so the cited blocks were not checked."]
    assert result.unreadable is None
    argv = fake.calls[0]
    assert argv[3:5] == ["check", str(crosswalk)] and "--json" in argv
    assert argv_value(argv, "--profiles") == str(profiles) and argv_value(argv, "--data") == str(data)
    assert argv_value(argv, "--db") == str(work / "graph.db") and "--submit" not in argv


def test_check_of_an_unreadable_crosswalk_is_the_commands_sentence(dirs):
    work, data, _ = dirs
    crosswalk, profiles = seed(work)
    sentence = "The crosswalk is not valid YAML, so it could not be checked: line 3, column 2: expected ',' or ']'."
    out = said("check", unreadable=sentence, errors=[sentence])
    result = crosswalk_steps.check(crosswalk, profiles, data, run=Fake(work, {"check": lambda a: (1, out)}))
    assert result.unreadable == sentence and result.errors == []


def test_check_with_corrections_runs_the_full_check_with_them(dirs):
    work, data, _ = dirs
    crosswalk, profiles = seed(work)
    sub = work / "submission.yaml"
    sub.write_text("files: []\n")
    out = said("check", warnings=["participants.tsv: a unit is missing"], notes=["There was no entry for x.tsv to remove."])
    fake = Fake(work, {"check": lambda a: (0, out)})
    result = crosswalk_steps.check(crosswalk, profiles, data, run=fake, db=work / "graph.db", submission=sub)
    assert fake.words() == ["check"]
    argv = fake.calls[0]
    assert argv_value(argv, "--submit") == str(sub) and argv_value(argv, "--data") == str(data)
    assert argv_value(argv, "--db") == str(work / "graph.db") and "--json" in argv
    assert result.warnings == ["participants.tsv: a unit is missing"]
    assert result.notes == ["There was no entry for x.tsv to remove."]
    assert crosswalk.read_text() == CROSSWALK_YAML


# The confirmation.

def confirm_args(work, data, **kw):
    crosswalk, profiles = seed(work)
    args = dict(crosswalk=crosswalk, profiles=profiles, by="An Author", fields=None, submission=None, data_dir=data,
                site=work / "site", db=work / "graph.db")
    args.update(kw)
    return args


def test_confirm_with_errors_stops_before_loading(dirs):
    work, data, _ = dirs
    fake = Fake(work, {"check": lambda a: (1, said("check", errors=["a.tsv: no term"]))})
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=fake)
    assert result.errors == ["a.tsv: no term"]
    assert result.addresses == {} and not result.confirmed
    assert fake.words() == ["check"]


def test_confirm_happy_path(dirs):
    work, data, _ = dirs
    fake = Fake(work)
    result = crosswalk_steps.confirm(**confirm_args(work, data, fields=["a", "b"]), run=fake)
    assert result.errors == [] and result.confirmed
    assert result.version == "1.1.0"
    assert fake.words() == ["check", "confirm", "load", "export", "export", "publish"]
    assert all("--json" in argv for argv in fake.calls)
    confirm = fake.calls[1]
    assert argv_value(confirm, "--by") == "An Author"
    assert argv_value(confirm, "--fields") == "a,b"
    assert [argv_value(c, "--format") for c in fake.calls[3:5]] == ["fair2", "croissant"]
    assert all("--offline" not in c for c in fake.calls[3:5])
    assert fake.calls[5][1:4] == ["-m", "evidentron.cli.data_cli", "publish"]
    assert argv_value(fake.calls[5], "--out") == str(work / "site")
    assert result.addresses == ADDRESSES                  # publish's own addresses
    assert result.terms == [{"id": "age", "label": "age"}, {"id": "participant", "label": None}]   # confirm's own
    assert result.terms_url == f"{CDN}/terms/"


def test_confirm_with_a_submission_checks_it_in_full_then_applies_it(dirs):
    work, data, _ = dirs
    sub = work / "sub.yaml"
    sub.write_text("files: []\n")
    fake = Fake(work)
    crosswalk_steps.confirm(**confirm_args(work, data, submission=sub), run=fake)
    check, confirm = fake.calls[0], fake.calls[1]
    assert argv_value(check, "--submit") == str(sub) and argv_value(check, "--data") == str(data)
    assert argv_value(confirm, "--submit") == str(sub)
    assert argv_value(confirm, "--profiles") == str(work / "00041.profiles.json")


def test_confirm_refused_by_the_command_returns_its_sentences(dirs):
    work, data, _ = dirs
    out = said("confirm", errors=["a.tsv: no term", "b.tsv: no description"])
    fake = Fake(work, {"confirm": lambda a: (1, out)})
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=fake)
    assert result.errors == ["a.tsv: no term", "b.tsv: no description"]
    assert not result.confirmed
    assert fake.words() == ["check", "confirm"]


@pytest.mark.parametrize("step", ["load", "export", "publish"])
def test_a_later_step_that_fails_is_its_sentence(dirs, step):
    work, data, _ = dirs
    out = said(step, errors=[f"The {step} command stopped with an error, OSError: disk full."])
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=Fake(work, {step: lambda a: (1, out)}))
    assert result.errors == [f"The {step} command stopped with an error, OSError: disk full."]
    assert result.confirmed and result.version == "1.1.0"


def test_a_later_step_that_prints_no_json_is_one_sentence(dirs):
    work, data, _ = dirs
    crash = crosswalk_steps.Output("", "Traceback (most recent call last):\n  File x\nOSError: disk full\n")
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=Fake(work, {"load": lambda a: (1, crash)}))
    assert result.errors == ["The command gave no result: OSError: disk full."]


def test_publish_runs_in_the_store_folder(dirs, monkeypatch):
    work, data, _ = dirs
    seen = []
    fake = Fake(work)
    monkeypatch.setattr(crosswalk_steps, "subprocess_runner", lambda cwd: (lambda argv: seen.append(cwd) or fake(argv)))
    db = work / "store" / "graph.db"
    crosswalk_steps.confirm(**confirm_args(work, data, db=db))
    assert [cwd for cwd, word in zip(seen, fake.words()) if word == "publish"] == [work / "store"]


def test_the_commands_run_on_evidentrons_python(dirs, monkeypatch):
    work, data, lock = dirs
    monkeypatch.setenv("EVIDENTRON_PYTHON", "/opt/evidentron/bin/python")
    fake = Fake(work)
    crosswalk_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake)
    assert fake.calls[0][:3] == ["/opt/evidentron/bin/python", "-m", "evidentron.graph"]
    assert crosswalk_steps.crosswalk_command()[0] == "/opt/evidentron/bin/python"
    assert crosswalk_steps.data_cli_command() == ["/opt/evidentron/bin/python", "-m", "evidentron.cli.data_cli"]
    monkeypatch.delenv("EVIDENTRON_PYTHON")
    monkeypatch.setattr(crosswalk_steps, "PROVISIONED_PYTHON", str(work / "nowhere" / "{user}" / "python"))
    assert crosswalk_steps.graph_command() == [sys.executable, "-m", "evidentron.graph"]


def test_without_evidentron_python_the_commands_run_on_the_provisioned_python(tmp_path, monkeypatch):
    """A person running crosswalk_admin.py in a shell has no EVIDENTRON_PYTHON: the commands then run on the Python
    the provisioning installs for that user, /home/<user>/venv/evidentron/bin/python, when it exists."""
    monkeypatch.delenv("EVIDENTRON_PYTHON", raising=False)
    monkeypatch.setattr(crosswalk_steps, "PROVISIONED_PYTHON", str(tmp_path / "{user}" / "venv" / "python"))
    provisioned = tmp_path / pwd.getpwuid(os.geteuid()).pw_name / "venv" / "python"
    assert crosswalk_steps.crosswalk_command()[0] == sys.executable            # not there yet
    provisioned.parent.mkdir(parents=True)
    provisioned.write_text("")
    assert crosswalk_steps.crosswalk_command() == [str(provisioned), "-m", "evidentron.crosswalk"]
    monkeypatch.setenv("EVIDENTRON_PYTHON", "/opt/evidentron/bin/python")    # the environment's still comes first
    assert crosswalk_steps.crosswalk_command()[0] == "/opt/evidentron/bin/python"


def test_the_provisioned_python_is_the_ansible_roles_default():
    assert crosswalk_steps.PROVISIONED_PYTHON == "/home/{user}/venv/evidentron/bin/python"


def test_confirm_keeps_the_confirmed_crosswalk_in_the_flat_folder(dirs):
    work, data, _ = dirs
    record = work.parent / "crosswalks"
    record.mkdir()
    args = confirm_args(work, data)
    fake = Fake(work)
    result = crosswalk_steps.confirm(**args, run=fake, record=record)
    assert result.errors == [] and result.confirmed
    assert (record / "00041.crosswalk.yaml").read_text() == args["crosswalk"].read_text()
    assert (record / "00041.profiles.json").read_text() == "{}"
    assert "--previous" not in fake.calls[1]


def test_confirm_measures_against_the_confirmed_copy_or_the_one_a_reset_kept(dirs):
    work, data, _ = dirs
    record = work.parent / "crosswalks"
    record.mkdir()
    (record / "00041.crosswalk.yaml.previous").write_text(CROSSWALK_YAML)
    fake = Fake(work)
    crosswalk_steps.confirm(**confirm_args(work, data), run=fake, record=record)
    assert argv_value(fake.calls[1], "--previous") == str(record / "00041.crosswalk.yaml.previous")
    fake = Fake(work)
    crosswalk_steps.confirm(**confirm_args(work, data), run=fake, record=record)     # now the flat copy is there
    assert argv_value(fake.calls[1], "--previous") == str(record / "00041.crosswalk.yaml")


def test_confirm_fails_with_publishs_sentence_when_the_documents_are_not_written(dirs):
    work, data, _ = dirs
    refused = f"No crosswalk of {DOI} is in the server's folder of crosswalks: it looks for 00041.crosswalk.yaml there."
    out = said("publish", refused=[{"doi": None, "sentence": f"The terms are published without units: {DOI}."},
                                   {"doi": "10.55458/neurolibre.00099", "sentence": f"No crosswalk of {DOI}9 is there."},
                                   {"doi": DOI.upper(), "sentence": refused}],
               exported=["10.55458/neurolibre.00099"], terms_url=f"{CDN}/terms/")
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=Fake(work, {"publish": lambda a: (0, out)}),
                                record=work.parent / "crosswalks")
    assert result.errors == [refused]
    assert result.confirmed and result.version == "1.1.0"
    assert result.addresses == {}
    out = said("publish", exported=[], terms_url=f"{CDN}/terms/")
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=Fake(work, {"publish": lambda a: (0, out)}))
    assert result.errors == [f"The FAIR² and Croissant documents of {DOI} were not written when the site was published."]


def test_a_load_that_names_no_paper_is_a_sentence(dirs):
    work, data, _ = dirs
    fake = Fake(work, {"load": lambda a: (0, said("load", fields=2, paper=None))})
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=fake)
    assert result.errors == ["The confirmed crosswalk names no paper, so its addresses are unknown."]
    assert "publish" not in fake.words()


# The terms of the paper's text.

def test_link_terms_gives_the_count_and_the_labels(dirs):
    work, _, _ = dirs
    fake = Fake(work)
    result = crosswalk_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake)
    assert (result.linked, result.labels, result.failure) == (2, ["crop yield", "`plot`"], None)
    argv = fake.calls[0]
    assert argv[:4] == [sys.executable, "-m", "evidentron.graph", "concepts"]
    assert argv_value(argv, "--paper") == DOI and argv_value(argv, "--backend") == "anthropic-api"
    assert "--json" in argv


def test_link_terms_that_fail_say_why(dirs):
    work, _, _ = dirs
    out = said("graph concepts", errors=[f"{DOI}: The model's answer was not JSON"])
    result = crosswalk_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=Fake(work, {"concepts": lambda a: (0, out)}))
    assert (result.linked, result.failure) == (None, "The model's answer was not JSON")
    out = said("graph concepts", errors=["The anthropic-api backend needs ANTHROPIC_API_KEY."])
    result = crosswalk_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=Fake(work, {"concepts": lambda a: (1, out)}))
    assert result.failure == "The anthropic-api backend needs ANTHROPIC_API_KEY."


# Loading the paper into the store.

def test_load_paper(dirs):
    work, _, _ = dirs
    fake = Fake(work)
    assert crosswalk_steps.load_paper(DOI, Path("/cdn/current"), work / "graph.db", run=fake) is None
    assert fake.calls[0][1:] == ["-m", "evidentron.graph", "/cdn/current", "--doi", DOI, "--db",
                                 str(work / "graph.db"), "--json"]
    out = said("graph", errors=[f"The paper {DOI} was not loaded from /cdn/current: no config.json."],
               papers=[{"doi": DOI, "loaded": False}])
    sentence = crosswalk_steps.load_paper(DOI, Path("/cdn/current"), work / "graph.db", run=lambda a: (1, out))
    assert sentence == f"The paper {DOI} was not loaded from /cdn/current: no config.json."


def test_a_load_that_did_not_load_the_paper_says_so(dirs):
    work, _, _ = dirs
    out = said("graph", papers=[{"doi": DOI, "loaded": False}])
    sentence = crosswalk_steps.load_paper(DOI, Path("/cdn"), work / "graph.db", run=lambda a: (0, out))
    assert sentence == f"The store has no row for {DOI} after the load."


# The runner: stdout apart, and a time limit per step.

def test_the_runner_keeps_stdout_apart(tmp_path):
    run = crosswalk_steps.subprocess_runner(tmp_path)
    code, output = run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert code == 0
    assert output.stdout.strip() == "out" and output.stderr.strip() == "err"


def test_a_command_that_cannot_start_is_a_result_with_its_sentence(tmp_path):
    code, output = crosswalk_steps.subprocess_runner(tmp_path)(["/no/such/python", "-m", "evidentron.crosswalk", "check"])
    result = crosswalk_steps.outcome(code, output)
    assert result["ok"] is False
    assert result["errors"] == ["The command could not be started: No such file or directory."]


@pytest.mark.parametrize("argv, seconds", [
    (["py", "-m", "evidentron.crosswalk", "profile"], 600),
    (["py", "-m", "evidentron.crosswalk", "explore", "--lock", "x"], 3600 + 3600),
    (["py", "-m", "evidentron.crosswalk", "explore"], 3600),
    (["py", "-m", "evidentron.graph", "/cdn", "--doi", "d"], 900),
    (["py", "-m", "evidentron.crosswalk", "check"], 900),
    (["py", "-m", "evidentron.crosswalk", "confirm"], 900),
    (["py", "-m", "evidentron.crosswalk", "load"], 900),
    (["py", "-m", "evidentron.crosswalk", "export"], 900),
    (["py", "-m", "evidentron.cli.data_cli", "publish"], 900),
])
def test_each_step_has_its_time_limit(argv, seconds):
    assert crosswalk_steps.time_limit(argv)[1] == seconds


def test_a_step_past_its_time_is_stopped_with_its_children(tmp_path, monkeypatch):
    monkeypatch.setattr(crosswalk_steps, "time_limit", lambda argv: ("check", 1))
    marker = tmp_path / "child.pid"
    script = ("import subprocess, sys, time; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open({str(marker)!r}, 'w').write(str(c.pid)); time.sleep(60)")
    started = time.monotonic()
    code, output = crosswalk_steps.subprocess_runner(tmp_path)([sys.executable, "-c", script])
    assert time.monotonic() - started < 30
    result = crosswalk_steps.outcome(code, output)
    assert result["ok"] is False
    assert result["errors"] == ["The check step took longer than 1 seconds and was stopped."]
    grandchild = int(marker.read_text())
    for _ in range(50):                    # the killed grandchild is gone once its parent, init, reaps it
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the step's child was left running")


# Starting over.

def test_reset_moves_the_crosswalk_aside_publishes_again_and_removes_the_work_copy(tmp_path):
    crosswalks, site = tmp_path / "crosswalks", tmp_path / "site"
    (crosswalks / "00041").mkdir(parents=True)
    (crosswalks / "00041" / "00041.crosswalk.yaml").write_text("the work copy")
    (crosswalks / "00041.crosswalk.yaml").write_text(CROSSWALK_YAML)
    (crosswalks / "00041.profiles.json").write_text("{}")
    (crosswalks / "00042.crosswalk.yaml").write_text("another paper")
    fake = Fake(tmp_path)
    seen = {}

    def publish(argv):
        seen["flat"] = (crosswalks / "00041.crosswalk.yaml").exists()
        return fake._publish(argv)
    fake.script["publish"] = publish
    result = crosswalk_steps.reset(DOI, crosswalks, tmp_path / "graph.db", site, run=fake)
    assert fake.words() == ["publish"] and argv_value(fake.calls[0], "--out") == str(site)
    assert "--json" in fake.calls[0]
    assert seen == {"flat": False}                       # published once the crosswalk is gone
    assert not (crosswalks / "00041.crosswalk.yaml").exists() and not (crosswalks / "00041.profiles.json").exists()
    assert (crosswalks / "00041.crosswalk.yaml.previous").read_text() == CROSSWALK_YAML
    assert not (crosswalks / "00041").exists()
    assert (crosswalks / "00042.crosswalk.yaml").read_text() == "another paper"
    assert result.removed and result.published
    assert result.said[-1] == ("The store keeps the paper's rows until the next load of the paper replaces them, "
                               "since Evidentron has no command to remove them.")
    assert all("\n" not in s for s in result.said)


def test_a_reset_that_stopped_says_so_and_is_marked(tmp_path, monkeypatch):
    crosswalks = tmp_path / "crosswalks"
    crosswalks.mkdir()
    (crosswalks / "00041.crosswalk.yaml").write_text(CROSSWALK_YAML)

    def refuse(src, dst):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(crosswalk_steps.os, "replace", refuse)
    fake = Fake(tmp_path)
    result = crosswalk_steps.reset(DOI, crosswalks, tmp_path / "graph.db", tmp_path / "site", run=fake)
    assert result.stopped and not result.removed and fake.calls == []
    assert result.said == ["The reset stopped: Permission denied."]


def test_the_reset_says_publishs_failure_inside_its_sentence(tmp_path):
    crosswalks = tmp_path / "crosswalks"
    crosswalks.mkdir()
    (crosswalks / "00041.crosswalk.yaml").write_text(CROSSWALK_YAML)
    out = said("publish", errors=["The crosswalk folder could not be read."])
    result = crosswalk_steps.reset(DOI, crosswalks, tmp_path / "graph.db", tmp_path / "site", run=lambda a: (1, out))
    assert "Evidentron's site could not be published again: the crosswalk folder could not be read." in result.said
    assert result.stopped
    assert not hasattr(crosswalk_steps, "_lower_first")


def test_reset_that_cannot_publish_says_why(tmp_path):
    crosswalks = tmp_path / "crosswalks"
    crosswalks.mkdir()
    (crosswalks / "00041.crosswalk.yaml").write_text(CROSSWALK_YAML)
    out = said("publish", errors=["The site could not be written: disk full."])
    result = crosswalk_steps.reset(DOI, crosswalks, tmp_path / "graph.db", tmp_path / "site", run=lambda a: (1, out))
    assert result.removed and not result.published
    assert "Evidentron's site could not be published again: the site could not be written: disk full." in result.said


def test_reset_of_a_paper_with_nothing_confirmed_says_so_and_publishes_nothing(tmp_path):
    fake = Fake(tmp_path)
    result = crosswalk_steps.reset(DOI, tmp_path / "crosswalks", tmp_path / "graph.db", tmp_path / "site", run=fake)
    assert result.said[0] == f"There is no confirmed crosswalk of {DOI} on the server."
    assert fake.calls == [] and not result.removed


# B10 review: a result the server made up is not a check's finding; the runner leaves no child behind.

@pytest.mark.parametrize("code, output", [
    (2, said("check", errors=["The command was not run: the following arguments are required: crosswalk."])),
    (1, crosswalk_steps.Output("", "Traceback\nImportError: x\n")),
])
def test_a_result_the_command_did_not_make_is_not_a_run(code, output):
    result = crosswalk_steps.outcome(code, output)
    assert result["ok"] is False and result["ran"] is False
    assert crosswalk_steps.outcome(0, said("check"))["ran"] is True
    assert crosswalk_steps.outcome(1, said("check", errors=["a.tsv: no term"]))["ran"] is True


def test_a_usage_error_is_the_commands_sentence():
    out = said("check", errors=["The command was not run: the following arguments are required: crosswalk."])
    result = crosswalk_steps.outcome(2, out)
    assert result["errors"] == ["The command was not run: the following arguments are required: crosswalk."]


def test_a_check_that_could_not_run_is_a_failure_not_errors(dirs):
    work, data, _ = dirs
    crosswalk, profiles = seed(work)
    crash = crosswalk_steps.Output("", "ImportError: no module\n")
    result = crosswalk_steps.check(crosswalk, profiles, data, run=Fake(work, {"check": lambda a: (1, crash)}))
    assert result.errors == [] and result.failure == "The command gave no result: ImportError: no module."
    result = crosswalk_steps.confirm(**confirm_args(work, data), run=Fake(work, {"check": lambda a: (1, crash)}))
    assert result.errors == ["The check could not be run: the command gave no result: ImportError: no module."]


def test_a_draft_whose_check_could_not_run_says_so(dirs):
    work, data, lock = dirs
    crash = crosswalk_steps.Output("", "ImportError: no module\n")
    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"check": lambda a: (1, crash)}))
    assert result.problems == [] and result.check_failure == "The command gave no result: ImportError: no module."


def test_an_exception_while_waiting_kills_the_step_and_its_children(tmp_path):
    import signal

    class Soft(Exception):
        pass

    def raise_soft(*a):
        raise Soft()
    marker = tmp_path / "child.pid"
    script = ("import subprocess, sys, time; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open({str(marker)!r}, 'w').write(str(c.pid)); time.sleep(60)")
    old = signal.signal(signal.SIGALRM, raise_soft)
    signal.alarm(2)
    try:
        with pytest.raises(Soft):
            crosswalk_steps.subprocess_runner(tmp_path)([sys.executable, "-c", script])
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    grandchild = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the step's child was left running")


def test_the_places_and_the_lock_have_one_home(tmp_path, monkeypatch):
    import myst_cdn
    assert crosswalk_steps.CROSSWALKS_PATH == crosswalk_steps.EVIDENTRON_PATH / "crosswalks"
    assert crosswalk_steps.GRAPH_DB_PATH == crosswalk_steps.EVIDENTRON_PATH / "graph.db"
    assert crosswalk_steps.STORE_LOCK_PATH == crosswalk_steps.EVIDENTRON_PATH / "store.lock"
    assert crosswalk_steps.SITE_PATH == myst_cdn.EVIDENTRON_SITE and crosswalk_steps.STORE_LOCK_WAIT == 1800
    monkeypatch.setattr(crosswalk_steps, "STORE_LOCK_PATH", tmp_path / "store.lock")
    monkeypatch.setattr(crosswalk_steps, "STORE_LOCK_WAIT", 0.2)
    with crosswalk_steps.store_lock():
        with pytest.raises(TimeoutError):              # a second holder waits, then gives up
            with crosswalk_steps.store_lock():
                pass
    assert myst_cdn.duration(1800) == "30 minutes" and myst_cdn.duration(0.2) == "0.2 seconds"


def test_the_task_time_limits_leave_room_for_the_steps_own_timeouts():
    """Each task's soft limit is the sum of its steps at their own limits plus STEP_MARGIN, so the step's own
    one-sentence timeout always fires before celery's soft limit."""
    t, wait = crosswalk_steps.TIME_LIMITS, crosswalk_steps.STORE_LOCK_WAIT
    draft = (crosswalk_steps.CLONE_TIMEOUT + 2 * wait + crosswalk_steps.OTHER_TIME_LIMIT + t["profile"] + t["explore"]
             + crosswalk_steps.EXPLORE_SLOT_WAIT + t["check"] + t["explore"] + t["check"] + crosswalk_steps.OTHER_TIME_LIMIT)
    check = t["check"]
    confirm = (wait + crosswalk_steps.OTHER_TIME_LIMIT + t["check"] + t["confirm"] + t["load"] + 2 * t["export"]
               + t["publish"] + crosswalk_steps.REBUILD_SECONDS)
    assert crosswalk_steps.STEP_MARGIN == 120
    for limit, steps in ((crosswalk_steps.DRAFT_SECONDS, draft), (crosswalk_steps.CHECK_SECONDS, check),
                         (crosswalk_steps.CONFIRM_SECONDS, confirm)):
        assert limit > steps
        assert limit == steps + crosswalk_steps.STEP_MARGIN


def test_the_archived_commit_comes_from_evidentron_and_is_none_when_the_deposit_says_nothing():
    found = lambda argv: (0, crosswalk_steps.Output(json.dumps({"doi": DOI, "commit": "7e005e7e02aa", "github": None,
                                                                "archive": None})))
    assert crosswalk_steps.archived_commit(DOI, run=found) == "7e005e7e02aa"
    unknown = lambda argv: (1, crosswalk_steps.Output(json.dumps({"doi": DOI, "commit": None, "error": "no archives"})))
    assert crosswalk_steps.archived_commit(DOI, run=unknown) is None
    assert crosswalk_steps.archived_commit(DOI, run=lambda argv: (0, crosswalk_steps.Output("not json"))) is None
    asked = []
    crosswalk_steps.archived_commit(DOI, run=lambda argv: asked.append(argv) or found(argv))
    assert asked[0][-2:] == ["archived-repository", DOI] and "evidentron.cli.data_cli" in asked[0]


# A draft on a paid model is paid for by the token: the server keeps what it cost and each turn's log.

def test_a_draft_keeps_its_turns_log_and_sums_what_each_exploration_cost(dirs):
    work, data, lock = dirs
    explores = []

    def explore(argv):
        explores.append(argv_value(argv, "--backend"))
        Path(argv_value(argv, "-o")).write_text(CROSSWALK_YAML)
        usage = {"turns": 10, "prompt_tokens": 50000, "cached_tokens": 40000, "answer_tokens": 2000}
        out = said("explore", crosswalk=argv_value(argv, "-o"), summary=SUMMARY, usage=usage,
                   drafted_by={"backend": argv_value(argv, "--backend"), "model": "a-model"},
                   **({} if len(explores) > 1 else {"ok": False, "errors": ["a.tsv: no term"]}))
        return (0 if len(explores) > 1 else 1), crosswalk_steps.Output(out, f"step 0: prompt 5000 tokens ({len(explores)})\n")

    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert explores == ["openai-compatible", "anthropic-api"]
    assert result.usage == {"turns": 20, "prompt_tokens": 100000, "cached_tokens": 80000, "answer_tokens": 4000}
    log = (work / "00041.explore.log").read_text()
    assert "== explore on openai-compatible" in log and "== explore on anthropic-api" in log
    assert "step 0: prompt 5000 tokens (1)" in log and "step 0: prompt 5000 tokens (2)" in log


def test_a_draft_says_what_links_it_to_other_papers_and_the_comment_shows_it(dirs):
    work, data, lock = dirs
    sentence = "1 of the crosswalk's 2 terms of substance is held by other papers: nitrate (1 paper)."

    def explore(argv):
        Path(argv_value(argv, "-o")).write_text(CROSSWALK_YAML)
        out = said("explore", crosswalk=argv_value(argv, "-o"), summary=SUMMARY,
                   reuse={"count": 1, "substance": ["nitrate", "phosphate"], "held": {"nitrate": ["10.9/a"]},
                          "sentence": sentence},
                   drafted_by={"backend": argv_value(argv, "--backend"), "model": "a-model"})
        return 0, crosswalk_steps.Output(out, "")

    result = crosswalk_steps.draft(DOI, data, None, work, BACKENDS, lock, run=Fake(work, {"explore": explore}))
    assert result.shared == sentence
    from crosswalk_messages import draft_comment
    comment = draft_comment(SUMMARY, None, None, "draft", [], [], shared=result.shared)
    assert "**What links it to other papers**\n" + sentence in comment
