"""Caching a paper's data on the preview server: which datasets need downloading, which are already there, which
folders hold something else, and where each one goes on the BinderHub share.

qmrlab/mooc lists 14 datasets. The task used to look at the first one only: its folder existed, so a dataset added
later was never downloaded, and only that first dataset was copied to the BinderHub share, one level too high."""

import json

import pytest

import data_cache

ENTRY = {"src": "https://example.org/a.zip", "dst": "../data", "projectName": "book/a"}
MANIFEST = {
    "a": ENTRY,
    "b": {"src": "https://example.org/b.zip", "dst": "../data", "projectName": "book/b"},
}


def record(root, project, key, entry):
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{key}_repo2data_cache_record.json" if key else "repo2data_cache_record.json"
    (folder / name).write_text(json.dumps(entry))


# --- what each dataset needs -----------------------------------------------------------------------------------

def test_a_dataset_added_later_is_missing_and_the_others_are_present(tmp_path):
    record(tmp_path, "book/a", "a", ENTRY)
    plan = data_cache.plan(tmp_path, MANIFEST)
    assert plan.present == ["book/a"] and plan.missing == ["book/b"] and plan.conflicting == []


def test_a_folder_holding_a_different_dataset_is_a_conflict(tmp_path):
    record(tmp_path, "book/a", "a", dict(ENTRY, src="https://example.org/another-paper.zip"))
    assert data_cache.plan(tmp_path, MANIFEST).conflicting == ["book/a"]


def test_a_folder_without_a_record_is_a_conflict(tmp_path):
    (tmp_path / "book" / "a").mkdir(parents=True)          # data put there by hand, say
    assert data_cache.plan(tmp_path, MANIFEST).conflicting == ["book/a"]


def test_the_single_dataset_form_is_read_too(tmp_path):
    single = {"src": "https://example.org/a.zip", "dst": "../data", "projectName": "solo"}
    assert data_cache.plan(tmp_path, single).missing == ["solo"]
    record(tmp_path, "solo", None, single)
    assert data_cache.plan(tmp_path, single).present == ["solo"]


@pytest.mark.parametrize("manifest", [{}, {"a": {"src": "x"}}, {"a": "not a dataset"}])
def test_a_manifest_without_datasets_has_nothing_to_plan(tmp_path, manifest):
    with pytest.raises(ValueError):
        data_cache.plan(tmp_path, manifest)


def test_capital_letters_are_allowed(tmp_path):
    assert data_cache.plan(tmp_path, {"a": dict(ENTRY, projectName="qmrlab-mooc/06-MT-01-qMT")}).missing == [
        "qmrlab-mooc/06-MT-01-qMT"]


@pytest.mark.parametrize("name", ["book/../etc", "book a", "book/a.b", "/book", "book//a", "book/"])
def test_project_names_outside_the_allowed_form_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        data_cache.plan(tmp_path, {"a": dict(ENTRY, projectName=name)})


# --- where each dataset goes on the BinderHub share ------------------------------------------------------------

def test_each_dataset_keeps_its_place_under_the_share(tmp_path):
    data, nfs = tmp_path / "DATA", tmp_path / "NFS"
    targets = data_cache.nfs_targets(data, nfs, [str(data / "book" / "a"), str(data / "book" / "b")])
    assert targets == [(str(data / "book" / "a"), str(nfs / "book")), (str(data / "book" / "b"), str(nfs / "book"))]


def test_a_single_top_level_dataset_goes_to_the_top_of_the_share(tmp_path):
    data, nfs = tmp_path / "DATA", tmp_path / "NFS"
    assert data_cache.nfs_targets(data, nfs, [str(data / "solo")]) == [(str(data / "solo"), str(nfs))]


def test_a_path_outside_the_data_folder_is_refused(tmp_path):
    with pytest.raises(ValueError):
        data_cache.nfs_targets(tmp_path / "DATA", tmp_path / "NFS", [str(tmp_path / "elsewhere")])
