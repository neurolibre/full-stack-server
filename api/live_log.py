"""The live log of a MyST build, and the check that keeps /api/logs inside the logs folder.

While a build runs, the worker writes its own progress and mystmd's output to logs/myst/live/<task id>.log. The
preview server streams that file to the author's browser as server-sent events, so the build can be followed
from the link in the start email. When the build ends, the worker writes <task id>.json next to it with the
outcome and the address of the full log, and the stream ends with that.

The file is named after the Celery task, a random UUID, so the link cannot be guessed. Nothing here may fail a
build: a write that does not work is logged and skipped.
"""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

LIVE_FOLDER = ("myst", "live")
MAX_AGE_DAYS = 7
READ_CHUNK = 256 * 1024

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Secrets a build log can contain, whatever wrote it: the Jupyter server's token (logs before August 2026 printed
# it; a notebook can print its environment; error output can quote a URL that carries it), credentials in URLs,
# GitHub and API tokens, and NAME=value settings whose name says they are secret. Each pattern keeps the label
# and replaces only the value, so the log still says what was there. Long hex strings are not removed as such:
# commit hashes look the same and must stay readable.
REDACTED = "<redacted>"
_NOT_REDACTED = r"(?!<redacted>)"
_SECRETS = [
    # scheme://user:password@host
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + REDACTED + "@"),
    # ?token=... &access_token=... in URLs
    (re.compile(r"([?&](?:token|access_token|auth_token|api_key|apikey|key|password)=)" + _NOT_REDACTED + r"[^&\s\"'#]+",
                re.IGNORECASE), r"\1" + REDACTED),
    # Authorization: token|Bearer|Basic ...
    (re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?(?:token|bearer|basic)\s+)" + _NOT_REDACTED + r"[^\s\"']+",
                re.IGNORECASE), r"\1" + REDACTED),
    # --IdentityProvider.token="..." --ServerApp.token=... c.ServerApp.token = '...'
    (re.compile(r"(\.token\s*=\s*[\"']?)" + _NOT_REDACTED + r"[^\"'\s]+"), r"\1" + REDACTED),
    # JUPYTER_TOKEN="..."  GH_TOKEN: ...  'OPENAI_API_KEY': '...'  (environment-style names, upper case)
    (re.compile(r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY)[A-Z0-9_]*)"
                r"([\"']?\s*[=:]\s*[\"']?)" + _NOT_REDACTED + r"[^\s\"',}]+"), r"\1\2" + REDACTED),
    # password = ...  api_key: ...  (lower case settings)
    (re.compile(r"\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
                r"([\"']?\s*[=:]\s*[\"']?)" + _NOT_REDACTED + r"[^\s\"',}]+", re.IGNORECASE), r"\1\2" + REDACTED),
    # GitHub tokens and OpenAI-style keys, wherever they appear
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), REDACTED),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{10,}"), REDACTED),
]


def redact(text):
    """text with the secrets a build log can contain replaced by <redacted>."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def path_for(logs_root, task_id):
    """The live log of task_id under logs_root. Anything but a task id in its usual form is refused."""
    try:
        canonical = str(uuid.UUID(task_id))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"Not a task id: {task_id!r}")
    if canonical != task_id:
        raise ValueError(f"Not a task id: {task_id!r}")
    return Path(logs_root).joinpath(*LIVE_FOLDER) / f"{task_id}.log"


def _outcome_path(path):
    return Path(path).with_suffix(".json")


def start(path, text):
    """Begin the live log with text, replacing any earlier file of the same task."""
    _write(path, "w", text)


def append(path, text):
    """Add text to the live log, as whole lines."""
    _write(path, "a", text)


def _write(path, mode, text):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode) as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except OSError as e:
        logging.warning(f"Could not write the live log {path}: {e}")


def finish(path, status, log_url):
    """Record how the build ended and where its full log is, which ends the stream."""
    try:
        target = _outcome_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"status": status, "log_url": log_url}))
    except OSError as e:
        logging.warning(f"Could not record the outcome of the live log {path}: {e}")


def outcome(path):
    """{'status', 'log_url'} once the build has ended, else None."""
    try:
        return json.loads(_outcome_path(path).read_text())
    except (OSError, ValueError):
        return None


def read_new(path, offset):
    """The text written after offset (at most READ_CHUNK bytes, whole lines only), and the offset after it."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(READ_CHUNK)
    except OSError:
        return "", offset
    if not data:
        return "", offset
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) < READ_CHUNK:
            return "", offset                      # a line still being written: wait for its end
        end = len(data) - 1                        # one very long line: send it in pieces
    data = data[:end + 1]
    return data.decode("utf-8", errors="replace"), offset + len(data)


def _clean(text):
    """Text as the browser gets it: terminal colour codes removed first (they could split a secret), then secrets."""
    return redact(_ANSI.sub("", text))


def _event(name, data, event_id=None):
    lines = [f"event: {name}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def events(path, finished, offset=0, clock=time.monotonic, sleep=time.sleep,
           poll=1.0, keepalive=15.0, max_seconds=6 * 3600):
    """Server-sent events for the live log at path, starting after offset.

    'log' events carry new text, without terminal colour codes or secrets (see redact), and the offset reached as their id, so a
    browser that reconnects (EventSource sends the last id back) continues where it stopped. A comment line
    goes out when nothing was sent for `keepalive` seconds, so proxies keep the connection open. When
    finished() returns the outcome, the rest of the file is sent and a 'done' event carries the outcome.
    After max_seconds a 'timeout' event ends the stream, so a build whose worker died does not hold a
    connection forever.
    """
    started = last_sent = clock()
    while True:
        text, offset = read_new(path, offset)
        if text:
            yield _event("log", _clean(text), offset)
            last_sent = clock()
            continue                               # more may be waiting already
        result = finished()
        if result is not None:
            text, offset = read_new(path, offset)
            while text:
                yield _event("log", _clean(text), offset)
                text, offset = read_new(path, offset)
            yield _event("done", result)
            return
        if clock() - started >= max_seconds:
            yield _event("timeout", {"seconds": max_seconds})
            return
        if clock() - last_sent >= keepalive:
            yield ": keepalive\n\n"
            last_sent = clock()
        sleep(poll)


def remove_old(logs_root, max_age_days=MAX_AGE_DAYS):
    """Remove live logs (and their outcomes) last written more than max_age_days ago."""
    folder = Path(logs_root).joinpath(*LIVE_FOLDER)
    if not folder.is_dir():
        return
    cutoff = time.time() - max_age_days * 86400
    for item in folder.iterdir():
        try:
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError as e:
            logging.warning(f"Could not remove the old live log {item}: {e}")


def resolve_log_file(logs_root, file_path):
    """The real path of file_path under logs_root. A path that leads outside it, by '..', an absolute path or
    a symbolic link, is refused."""
    root = os.path.realpath(str(logs_root))
    target = os.path.realpath(os.path.join(root, file_path))
    if os.path.isabs(file_path) or os.path.commonpath([root, target]) != root or target == root:
        raise ValueError(f"Not a log file: {file_path!r}")
    return target
