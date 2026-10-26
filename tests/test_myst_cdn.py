"""The combined MyST CDN: layout, links, shared figures and the symlink swap."""

import json
import os

import pytest

from api import myst_cdn


def make_site(root, name, doi, pages, figures, bibliography=None):
    """A minimal `myst build` site: config.json, content/<page>.json, public/."""
    site = root / name / "_build" / "site"
    (site / "content").mkdir(parents=True)
    (site / "public").mkdir()
    project = {"title": name, "index": pages[0],
               "pages": [{"slug": p} for p in pages[1:]]}
    if doi:
        project["doi"] = doi
    if bibliography:
        project["bibliography"] = bibliography
    (site / "config.json").write_text(json.dumps({"version": 2, "projects": [project]}))
    for page in pages:
        others = [p for p in pages if p != page]
        body = {"slug": page, "mdast": {"children": [
            {"type": "link", "url": f"/{others[0]}" if others else "/"},
            {"type": "image", "url": "/fig-abc.png"},
            {"type": "link", "url": "https://example.org/x"},
        ]}, "dataUrl": f"/{page}.json"}
        (site / "content" / f"{page}.json").write_text(json.dumps(body))
    for figure, data in figures.items():
        (site / "public" / figure).write_bytes(data)
    return site


@pytest.fixture
def two_sites(tmp_path):
    return {
        "neurolibre.00021": make_site(tmp_path, "a", "10.55458/neurolibre.00021",
                                      ["index", "paper"], {"fig-abc.png": b"same"}),
        "neurolibre.00027": make_site(tmp_path, "b", "10.55458/neurolibre.00027",
                                      ["index"], {"fig-abc.png": b"same"}),
    }


def test_lists_every_paper_with_a_plain_slug(tmp_path, two_sites):
    out = tmp_path / "cdn"
    myst_cdn.build(two_sites, out)
    config = json.loads((out / "config.json").read_text())
    assert config["id"] == myst_cdn.SITE_ID
    assert [(p["slug"], p["doi"]) for p in config["projects"]] == [
        ("neurolibre-00021", "10.55458/neurolibre.00021"),
        ("neurolibre-00027", "10.55458/neurolibre.00027"),
    ]
    assert (out / "content" / "neurolibre-00021" / "paper.json").exists()


def test_doi_path_reaches_the_same_pages(tmp_path, two_sites):
    out = tmp_path / "cdn"
    myst_cdn.build(two_sites, out)
    by_doi = out / "content" / "10.55458" / "neurolibre.00021" / "paper.json"
    by_slug = out / "content" / "neurolibre-00021" / "paper.json"
    assert by_doi.read_text() == by_slug.read_text()
    # A relative link, so it still resolves after the build folder is renamed.
    assert not os.path.isabs(os.readlink(out / "content" / "10.55458" / "neurolibre.00021"))


def test_internal_links_carry_the_slug_and_figures_do_not(tmp_path, two_sites):
    out = tmp_path / "cdn"
    myst_cdn.build(two_sites, out)
    page = json.loads((out / "content" / "neurolibre-00021" / "index.json").read_text())
    link, image, external = page["mdast"]["children"]
    assert link["url"] == "/neurolibre-00021/paper"
    assert image["url"] == "/fig-abc.png"
    assert external["url"] == "https://example.org/x"
    assert page["dataUrl"] == "/neurolibre-00021/index.json"


def test_identical_figures_are_shared(tmp_path, two_sites):
    report = myst_cdn.build(two_sites, tmp_path / "cdn")
    assert report["clashes"] == []
    assert (tmp_path / "cdn" / "public" / "fig-abc.png").read_bytes() == b"same"


def test_a_name_with_different_bytes_is_reported_and_the_first_kept(tmp_path):
    sources = {
        "neurolibre.00021": make_site(tmp_path, "a", "10.55458/neurolibre.00021",
                                      ["index"], {"fig-abc.png": b"first"}),
        "neurolibre.00027": make_site(tmp_path, "b", "10.55458/neurolibre.00027",
                                      ["index"], {"fig-abc.png": b"second"}),
    }
    report = myst_cdn.build(sources, tmp_path / "cdn")
    assert report["clashes"] == ["fig-abc.png (neurolibre.00027)"]
    assert (tmp_path / "cdn" / "public" / "fig-abc.png").read_bytes() == b"first"


def test_bibliography_paths_are_reduced_to_file_names(tmp_path):
    sources = {"neurolibre.00004": make_site(
        tmp_path, "a", "10.55458/neurolibre.00004", ["index"], {},
        bibliography=["/Users/someone/build/paper.bib"])}
    myst_cdn.build(sources, tmp_path / "cdn")
    project = json.loads((tmp_path / "cdn" / "config.json").read_text())["projects"][0]
    assert project["bibliography"] == ["paper.bib"]


def test_a_site_without_a_doi_takes_it_from_the_sources(tmp_path):
    sources = {"neurolibre.00021": make_site(tmp_path, "a", None, ["index"], {})}
    myst_cdn.build(sources, tmp_path / "cdn")
    project = json.loads((tmp_path / "cdn" / "config.json").read_text())["projects"][0]
    assert (project["slug"], project["doi"]) == ("neurolibre-00021", "10.55458/neurolibre.00021")


def test_a_missing_site_fails_the_build(tmp_path):
    with pytest.raises(FileNotFoundError):
        myst_cdn.build({"neurolibre.00021": tmp_path / "nowhere"}, tmp_path / "cdn")


def add_all(sites, root):
    for name, site in sites.items():
        myst_cdn.add_site(name, "v1", site, root)


def test_publish_swaps_the_link_and_keeps_one_previous_build(tmp_path, two_sites):
    root = tmp_path / "cdn"
    add_all(two_sites, root)
    for _ in range(3):
        myst_cdn.publish(root)
    builds = sorted((root / "builds").iterdir())
    assert len(builds) == 2
    assert os.path.realpath(root / "current") == str(builds[-1].resolve())
    assert (root / "current" / "config.json").exists()
    # Relative, so the tree can be moved or mounted elsewhere.
    assert not os.path.isabs(os.readlink(root / "current"))


def test_publish_refuses_to_replace_a_real_folder(tmp_path, two_sites):
    root = tmp_path / "cdn"
    add_all(two_sites, root)
    (root / "current").mkdir()
    with pytest.raises(RuntimeError):
        myst_cdn.publish(root)


def test_site_builds_are_stored_by_doi_and_version(tmp_path, two_sites):
    root = tmp_path / "cdn"
    site = myst_cdn.add_site("neurolibre.00021", "v1", two_sites["neurolibre.00021"], root)
    assert site == root / "sites" / "10.55458" / "neurolibre.00021.v1"
    # Hard-linked, not copied.
    assert (site / "public" / "fig-abc.png").stat().st_ino == \
        (two_sites["neurolibre.00021"] / "public" / "fig-abc.png").stat().st_ino


def test_the_highest_version_is_the_current_one(tmp_path):
    root = tmp_path / "cdn"
    for version in ("v1", "v2", "v10"):
        myst_cdn.add_site("neurolibre.00041", version,
                          make_site(tmp_path, version, "10.55458/neurolibre.00041", ["index"], {}), root)
    myst_cdn.add_site("neurolibre.00050", "v1",
                      make_site(tmp_path, "other", "10.55458/neurolibre.00050", ["index"], {}), root)
    assert {n: s.name for n, s in myst_cdn.current_sites(root).items()} == {
        "neurolibre.00041": "neurolibre.00041.v10",
        "neurolibre.00050": "neurolibre.00050.v1",
    }


def test_adding_a_version_again_replaces_it(tmp_path):
    root = tmp_path / "cdn"
    first = make_site(tmp_path, "first", "10.55458/neurolibre.00041", ["index"], {"a.png": b"1"})
    second = make_site(tmp_path, "second", "10.55458/neurolibre.00041", ["index"], {"b.png": b"2"})
    myst_cdn.add_site("neurolibre.00041", "v2", first, root)
    site = myst_cdn.add_site("neurolibre.00041", "v2", second, root)
    assert sorted(p.name for p in (site / "public").iterdir()) == ["b.png"]
    assert [p.name for p in (root / "sites" / "10.55458").iterdir()] == ["neurolibre.00041.v2"]


def test_a_version_must_look_like_v_and_a_number(tmp_path, two_sites):
    with pytest.raises(ValueError):
        myst_cdn.add_site("neurolibre.00021", "1", two_sites["neurolibre.00021"], tmp_path / "cdn")


def test_add_and_publish_serves_the_new_paper(tmp_path, two_sites):
    root = tmp_path / "cdn"
    myst_cdn.add_and_publish("neurolibre.00021", "v1", two_sites["neurolibre.00021"], root)
    report = myst_cdn.add_and_publish("neurolibre.00027", "v1", two_sites["neurolibre.00027"], root)
    assert report["projects"] == 2
    assert (root / "current" / "content" / "neurolibre-00027" / "index.json").exists()


def make_extras(root):
    """Evidentron's static site: terms, exports (under a DOI with a slash), metadata, an index."""
    extras = root / "site"
    (extras / "terms").mkdir(parents=True)
    (extras / "terms" / "index.html").write_text("<html>terms</html>")
    (extras / "wefts" / "10.55458" / "neurolibre.00004").mkdir(parents=True)
    (extras / "wefts" / "10.55458" / "neurolibre.00004" / "fair2.json").write_text("{}")
    (extras / "dois").mkdir()
    (extras / "dois" / "x.json").write_text("{}")
    (extras / "index.json").write_text("[]")
    return extras


def test_the_build_carries_evidentrons_site_at_the_same_paths(tmp_path, two_sites):
    extras = make_extras(tmp_path)
    out = tmp_path / "cdn"
    report = myst_cdn.build(two_sites, out, extras=extras)
    assert report["extras"] is True
    assert (out / "terms" / "index.html").read_text() == "<html>terms</html>"
    assert (out / "wefts" / "10.55458" / "neurolibre.00004" / "fair2.json").exists()
    assert (out / "dois" / "x.json").exists()
    assert (out / "index.json").exists()


def test_without_evidentrons_site_the_build_goes_on_and_says_so(tmp_path, two_sites):
    missing = tmp_path / "nowhere"
    report = myst_cdn.build(two_sites, tmp_path / "cdn", extras=missing)
    assert report["projects"] == 2
    assert report["extras"] is False
    assert report["notes"] == [
        "Evidentron's site was not found, so the CDN carries no terms or exports yet."]
    assert not (tmp_path / "cdn" / "terms").exists()


def test_publish_passes_the_extras_folder_to_the_build(tmp_path, two_sites):
    root = tmp_path / "cdn-root"
    for name, site in two_sites.items():
        myst_cdn.add_site(name, "v1", site, root)
    report = myst_cdn.publish(root, extras=make_extras(tmp_path))
    assert report["extras"] is True
    assert (root / "current" / "terms" / "index.html").exists()


def test_publish_and_add_carry_evidentrons_site_by_default(tmp_path, two_sites, monkeypatch):
    monkeypatch.setattr(myst_cdn, "EVIDENTRON_SITE", make_extras(tmp_path))
    root = tmp_path / "cdn-root"
    report = myst_cdn.add_and_publish("neurolibre.00021", "v1", two_sites["neurolibre.00021"], root)
    assert report["extras"] is True
    assert (root / "current" / "terms" / "index.html").exists()
    report = myst_cdn.publish(root)
    assert report["extras"] is True


def test_extras_none_means_none(tmp_path, two_sites, monkeypatch):
    monkeypatch.setattr(myst_cdn, "EVIDENTRON_SITE", make_extras(tmp_path))
    root = tmp_path / "cdn-root"
    report = myst_cdn.add_and_publish("neurolibre.00021", "v1", two_sites["neurolibre.00021"], root, extras=None)
    assert report["extras"] is False and "notes" not in report


def test_rebuild_publishes_under_the_lock_with_evidentrons_site(tmp_path, two_sites, monkeypatch):
    import fcntl
    monkeypatch.setattr(myst_cdn, "EVIDENTRON_SITE", make_extras(tmp_path))
    root = tmp_path / "cdn-root"
    for name, site in two_sites.items():
        myst_cdn.add_site(name, "v1", site, root)
    held = []
    real = myst_cdn.publish

    def publish(r, extras=None):
        with open(r / ".lock", "w") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real(r, extras=extras)
    monkeypatch.setattr(myst_cdn, "publish", publish)
    report = myst_cdn.rebuild(root)
    assert held == [True]
    assert report["projects"] == 2 and report["extras"] is True
