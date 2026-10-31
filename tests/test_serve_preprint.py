"""serve_preprint.py, written next to the static site in the book archive on Zenodo, so a reader can serve the
preprint locally years later. Production builds made since neurolibre.00041 v2 carry the version in their base path
(/10.55458/neurolibre.00052.v1/...); the script stripped only /10.55458/neurolibre.00052, so it served nothing for
them. It must serve both forms."""

import importlib.util
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "api" / "templates" / "serve_preprint.py.template"


def script(tmp_path, base):
    site = tmp_path / "html"
    (site / "build").mkdir(parents=True)
    (site / "index.html").write_text(f'<a href="{base}/methods">Methods</a><script src="{base}/build/app.js"></script>')
    (site / "methods.html").write_text("the methods page")
    (site / "build" / "app.js").write_text("console.log(1)")
    source = TEMPLATE.read_text().format(journal_name="NeuroLibre", doi_prefix="10.55458", doi_suffix="neurolibre",
                                         issue_id=52, commit_fork="abc123")
    path = tmp_path / "serve_preprint.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("serve_preprint", path)
    module = importlib.util.module_from_spec(spec)
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        spec.loader.exec_module(module)          # must not start serving on import
    finally:
        os.chdir(here)
    return module


@pytest.mark.parametrize("base", ["/10.55458/neurolibre.00052", "/10.55458/neurolibre.00052.v1"])
def test_pages_and_assets_are_served_under_the_base_path(tmp_path, base):
    module = script(tmp_path, base)
    assert module.site_base() == base
    here = os.getcwd()
    os.chdir(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.CustomHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert urllib.request.urlopen(f"{url}{base}/methods").read() == b"the methods page"
        assert urllib.request.urlopen(f"{url}{base}/build/app.js").read() == b"console.log(1)"
        assert b"Methods" in urllib.request.urlopen(f"{url}{base}/").read()
    finally:
        server.shutdown()
        os.chdir(here)
