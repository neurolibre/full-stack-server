"""The MyST builds /api/books lists: found by their folders (<owner>/<repo>/<commit>/_build/html/index.html), not by a
<commit>.tar.gz next to them, which builds no longer write. MyST paths have no provider part, unlike Jupyter Book's."""

import common

A = "078af409c5863856b418a79fe3358a4f760e397b"
B = "6d25646e7e4df7449624be04a916638f6b5b39bd"
URL = "https://preview.example.org/myst"


def build(root, owner, repo, commit, archive=False):
    folder = root / owner / repo / commit / "_build" / "html"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<html>")
    if archive:
        (root / owner / repo / f"{commit}.tar.gz").write_bytes(b"x")


def test_each_saved_build_is_listed_with_its_address(tmp_path):
    build(tmp_path, "qmrlab", "mooc", A)
    build(tmp_path, "qmrlab", "mooc", B, archive=True)
    books = {b["commit_hash"]: b for b in common.myst_books(tmp_path, URL)}
    assert sorted(books) == sorted([A, B])
    assert books[A]["book_url"] == f"{URL}/qmrlab/mooc/{A}/_build/html/"
    assert books[A]["user_name"] == "qmrlab" and books[A]["repo_name"] == "mooc"
    assert books[A]["repo_link"] == "https://github.com/qmrlab/mooc"
    assert books[A]["format_type"] == "myst"
    assert books[A]["download_link"] is None
    assert books[B]["download_link"] == f"{URL}/qmrlab/mooc/{B}.tar.gz"


def test_working_folders_and_unfinished_saves_are_not_builds(tmp_path):
    build(tmp_path, "qmrlab", "mooc", "latest")
    build(tmp_path, "qmrlab", "mooc", f"{A}.saving")
    assert common.myst_books(tmp_path, URL) == []
