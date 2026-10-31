"""Secrets are removed from build logs before the preview server shows them, live or afterwards.

Build logs are public (the link goes to the author by email, and the viewer needs no password). Until August 2026
the logs carried the Jupyter server's token in plain text; a notebook can print its environment, and error output
can quote a URL with a token in it. Whatever wrote the log, the server removes these before sending it."""

import os

import pytest

import live_log

TOKEN = "53ef131ace4ec5f40ec062b7c448f588c6515a0d"


@pytest.mark.parametrize("text", [
    f'  export JUPYTER_TOKEN="{TOKEN}"',
    f"JUPYTER_TOKEN: {TOKEN}",
    f"{{'JUPYTER_TOKEN': '{TOKEN}', 'HOME': '/home/jovyan'}}",
    f"http://127.0.0.1:8888/?token={TOKEN}",
    f"ws://localhost:8888/api/kernels/abc/channels?session_id=x&token={TOKEN}",
    f'jupyter server --IdentityProvider.token="{TOKEN}" --ServerApp.port="8888"',
    f"--ServerApp.token={TOKEN}",
    f"Authorization: token {TOKEN}",
    f"headers={{'Authorization': 'Bearer {TOKEN}'}}",
])
def test_the_jupyter_token_is_removed(text):
    assert TOKEN not in live_log.redact(text)
    assert "<redacted>" in live_log.redact(text)


@pytest.mark.parametrize("secret, text", [
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "cloning with ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz", "GH_TOKEN=github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"),
    ("s3cr3t-pass", "https://builder:s3cr3t-pass@registry.example.org/v2/"),
    ("hunter2", "password = hunter2"),
    ("sk-abc123def456", "OPENAI_API_KEY=sk-abc123def456"),
])
def test_other_credentials_are_removed(secret, text):
    assert secret not in live_log.redact(text)


@pytest.mark.parametrize("text", [
    "📖 Built 2 T1 mapping.md in 1.55 s.",
    "Starting WebSocket: ws://localhost:8888/api/kernels/8d3f2590-0b7e-431b-b7e8-46917b530e06",
    "ValueError: Invalid property specified for object of type plotly.graph_objs.heatmap.ColorBar: 'titlefont'",
    'export JUPYTER_TOKEN="<redacted>"  # see hub.jh_token',
    "the token budget of the model",
])
def test_ordinary_build_output_is_left_alone(text):
    assert live_log.redact(text) == text


def test_the_live_stream_removes_secrets(tmp_path):
    path = live_log.path_for(tmp_path, "25c73a15-a292-463e-9e8c-5fda511e5d82")
    live_log.start(path, f"http://127.0.0.1:8888/?token={TOKEN}")
    live_log.finish(path, "success", None)
    body = "".join(live_log.events(path, lambda: live_log.outcome(path), sleep=lambda s: None, poll=0))
    assert TOKEN not in body


def test_the_log_viewer_removes_secrets(tmp_path, monkeypatch):
    here = os.getcwd()
    os.chdir(os.path.join(os.path.dirname(__file__), "..", "api"))
    try:
        import neurolibre_preview_api as api
        import neurolibre_common_api as common
    finally:
        os.chdir(here)
    logs = tmp_path / "logs"
    (logs / "myst" / "o" / "r").mkdir(parents=True)
    (logs / "myst" / "o" / "r" / "1.log").write_text(f'  export JUPYTER_TOKEN="{TOKEN}"\nBuilt')
    monkeypatch.setattr(common, "DATA_ROOT_PATH", str(tmp_path))
    monkeypatch.setitem(common.common_config, "LOGS_FOLDER", "logs")

    body = api.app.test_client().get("/api/logs/myst/o/r/1.log").get_data(as_text=True)

    assert TOKEN not in body and "Built" in body
