"""The weft steps: each composes the evidentron commands, tested with a fake that writes what a command would."""

import sys
from pathlib import Path

import pytest

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
        return {
            "profile": self._profile, "explore": self._explore, "check": self._check,
            "confirm": lambda a: (0, "The version is now 1.1.0, dated 2026-10-26.\n"),
        }.get(key, lambda a: (0, ""))(argv)

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
    assert weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake) == 4
    argv = fake.calls[0]
    assert argv[:3] == [sys.executable, "-m", "evidentron.graph"]
    assert argv_value(argv, "--paper") == DOI
    assert argv_value(argv, "--backend") == "anthropic-api"


def test_link_terms_without_a_count_is_zero(dirs):
    work, _, _ = dirs
    fake = Fake(work, {"other": lambda a: (1, f"{DOI}: The model's answer was not JSON\n")})
    assert weft_steps.link_terms(DOI, work / "graph.db", "anthropic-api", run=fake) == 0


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
