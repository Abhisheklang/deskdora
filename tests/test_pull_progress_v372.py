"""Model downloads, installed models, and .gguf import (v3.7.2).

Three user orders, 2026-11-19:

1. "When the app downloads a model, there is no visible
   progress."  The server stream was proven live (a timed
   experiment: NDJSON chunks arrive at the sender's cadence),
   but the UI rendered ONE LAYER's bytes — Ollama reports per
   layer — so the bar restarted per layer and the numbers never
   described the model.  PullAggregator now sums per-digest
   progress into whole-model completed_all / total_all /
   percent (percent None — never invented — when no total is
   known), and one shared renderer draws bar + "X of Y · Z%" +
   status word for the wizard, the error page, and the import.

2. "The model picker does not let the user choose a model they
   have ALREADY downloaded."  The pickers now lead with an
   "Already downloaded — on this computer" group, live from
   /api/tags (the source — including models the curated
   registry doesn't know), and say in plain words when Ollama
   isn't running.

3. "A user may have a model they obtained OUTSIDE the app"
   (.gguf on disk).  DeskDora checks the file's header only —
   magic, declared name/architecture, size — then hands the
   file to Ollama's create API; Ollama loads and runs it.
"""
import io
import json
import os
import struct
import urllib.error

import pytest
from fastapi.testclient import TestClient

from deskdora import gguf, planners, server

ROOT = os.path.join(os.path.dirname(__file__), "..")
INDEX = os.path.join(ROOT, "src", "deskdora", "static", "index.html")


# ----------------------------------------------------------------------
# Fake Ollama transport (the existing tests' mocking pattern)
# ----------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload=None, lines=None):
        self._payload = payload
        self._lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return json.dumps(self._payload or {}).encode()


class FakeOllama:
    """urlopen replacement routing by URL; records requests."""

    def __init__(self, tags=None, pull_lines=None, create_lines=None,
                 down=False):
        self.tags = tags
        self.pull_lines = pull_lines or []
        self.create_lines = create_lines or []
        self.down = down
        self.requests = []  # (url, parsed-json-body|None)

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        body = None
        data = getattr(req, "data", None)
        if data:
            try:
                body = json.loads(data.decode())
            except Exception:
                body = None
        self.requests.append((url, body))
        if self.down:
            raise urllib.error.URLError("connection refused")
        if "/api/pull" in url:
            return FakeResp(lines=self.pull_lines)
        if "/api/create" in url:
            return FakeResp(lines=self.create_lines)
        if "/api/tags" in url:
            return FakeResp(payload={"models": self.tags or []})
        raise AssertionError("unexpected URL " + url)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    return TestClient(server.app)


def install_fake(monkeypatch, fake):
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


def ndjson_lines(text):
    return [json.loads(l) for l in text.splitlines() if l.strip()]


# ----------------------------------------------------------------------
# Aggregation math
# ----------------------------------------------------------------------

def test_aggregator_sums_across_layers():
    agg = planners.PullAggregator()
    agg.feed({"status": "downloading", "digest": "a",
              "total": 1000, "completed": 250})
    ev = agg.feed({"status": "downloading", "digest": "a",
                   "total": 1000, "completed": 500})
    assert (ev["completed_all"], ev["total_all"], ev["percent"]) \
        == (500, 1000, 50)
    ev = agg.feed({"status": "downloading", "digest": "b",
                   "total": 300, "completed": 150})
    # Layer a's finished progress is not lost when layer b starts.
    assert (ev["completed_all"], ev["total_all"]) == (650, 1300)
    assert ev["percent"] == 50
    ev = agg.feed({"status": "downloading", "digest": "b",
                   "total": 300, "completed": 300})
    assert (ev["completed_all"], ev["total_all"], ev["percent"]) \
        == (800, 1300, 62)


def test_aggregator_no_total_means_no_invented_percent():
    agg = planners.PullAggregator()
    ev = agg.feed({"status": "pulling manifest"})
    assert ev["percent"] is None
    assert ev["total_all"] is None
    ev = agg.feed({"status": "verifying sha256 digest"})
    assert ev["percent"] is None


def test_aggregator_status_events_carry_running_totals():
    agg = planners.PullAggregator()
    agg.feed({"status": "downloading", "digest": "a",
              "total": 100, "completed": 100})
    ev = agg.feed({"status": "writing manifest"})
    assert ev["completed_all"] == 100
    assert ev["percent"] == 100


# ----------------------------------------------------------------------
# The pull endpoint stream carries byte-progress events
# ----------------------------------------------------------------------

PULL_LINES = [
    b'{"status": "pulling manifest"}\n',
    b'{"status": "downloading", "digest": "sha256:a", '
    b'"total": 1000, "completed": 500}\n',
    b'{"status": "downloading", "digest": "sha256:b", '
    b'"total": 300, "completed": 300}\n',
    b'{"status": "success"}\n',
]


def test_pull_stream_carries_aggregated_progress(api, monkeypatch):
    install_fake(monkeypatch, FakeOllama(pull_lines=PULL_LINES))
    res = api.post("/api/providers/ollama/pull",
                   json={"model": "qwen2.5:1.5b"})
    assert res.status_code == 200
    events = ndjson_lines(res.text)
    assert events[0]["status"] == "pulling manifest"
    assert events[0]["percent"] is None
    mid = events[2]
    assert mid["completed_all"] == 800
    assert mid["total_all"] == 1300
    assert mid["percent"] == 62
    assert events[-1]["status"] == "success"


def test_pull_stream_reports_failure_as_error_event(api,
                                                     monkeypatch):
    install_fake(monkeypatch, FakeOllama(down=True))
    res = api.post("/api/providers/ollama/pull",
                   json={"model": "qwen2.5:1.5b"})
    events = ndjson_lines(res.text)
    assert len(events) == 1
    assert "not reachable" in events[0]["error"]


# ----------------------------------------------------------------------
# Installed models: live from /api/tags, registry-independent
# ----------------------------------------------------------------------

TAGS = [{"name": "qwen2.5:1.5b", "size": 986061087},
        {"name": "my-own-import:7b", "size": 4200000000}]


def test_installed_endpoint_lists_tags(api, monkeypatch):
    install_fake(monkeypatch, FakeOllama(tags=TAGS))
    body = api.get("/api/providers/ollama/installed").json()
    assert body["reachable"] is True
    names = [m["name"] for m in body["models"]]
    # The registry-unknown model is there: tags is the source.
    assert names == ["qwen2.5:1.5b", "my-own-import:7b"]
    assert body["models"][0]["size"] == 986061087


def test_installed_endpoint_ollama_off_is_a_state_not_an_error(
        api, monkeypatch):
    install_fake(monkeypatch, FakeOllama(down=True))
    res = api.get("/api/providers/ollama/installed")
    assert res.status_code == 200
    assert res.json() == {"reachable": False, "models": []}


def test_installed_models_planner_raises_when_down(monkeypatch):
    install_fake(monkeypatch, FakeOllama(down=True))
    planner = planners.OllamaPlanner(host="http://127.0.0.1:11434")
    with pytest.raises(planners.PlanError):
        planner.installed_models()


# ----------------------------------------------------------------------
# GGUF header: synthetic fixture, header-only reading
# ----------------------------------------------------------------------

def _gguf_bytes(kvs, extra=b""):
    out = [gguf.MAGIC, struct.pack("<I", 3), struct.pack("<Q", 42),
           struct.pack("<Q", len(kvs))]
    for key, vtype, payload in kvs:
        kb = key.encode()
        out.append(struct.pack("<Q", len(kb)))
        out.append(kb)
        out.append(struct.pack("<I", vtype))
        out.append(payload)
    out.append(extra)
    return b"".join(out)


def _s(text):
    b = text.encode()
    return struct.pack("<Q", len(b)) + b


GGUF_FIXTURE = _gguf_bytes([
    ("general.architecture", 8, _s("llama")),
    ("general.name", 8, _s("Tiny Test Model")),
    ("general.file_type", 4, struct.pack("<I", 15)),
    ("some.scores", 9, struct.pack("<I", 6)
     + struct.pack("<Q", 3) + struct.pack("<fff", 0.5, 0.25, 1.0)),
], extra=b"\x00" * 100000)  # weights-ish tail: never parsed


@pytest.fixture
def gguf_file(tmp_path):
    path = tmp_path / "tiny-test-model.gguf"
    path.write_bytes(GGUF_FIXTURE)
    return str(path)


def test_read_header_extracts_declared_metadata(gguf_file):
    info = gguf.read_header(gguf_file)
    assert info["name"] == "Tiny Test Model"
    assert info["architecture"] == "llama"
    assert info["version"] == 3
    assert info["size_bytes"] == len(GGUF_FIXTURE)


def test_check_model_file_accepts_the_fixture(gguf_file):
    info = gguf.check_model_file(gguf_file)
    assert info["name"] == "Tiny Test Model"


def test_check_rejects_missing_file(tmp_path):
    with pytest.raises(gguf.GgufError) as exc:
        gguf.check_model_file(str(tmp_path / "nope.gguf"))
    assert "No file exists" in str(exc.value)


def test_check_rejects_wrong_extension(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(GGUF_FIXTURE)
    with pytest.raises(gguf.GgufError) as exc:
        gguf.check_model_file(str(path))
    assert ".gguf" in str(exc.value)


def test_check_rejects_bad_magic(tmp_path):
    path = tmp_path / "fake.gguf"
    path.write_bytes(b"NOPE" + GGUF_FIXTURE[4:])
    with pytest.raises(gguf.GgufError) as exc:
        gguf.check_model_file(str(path))
    assert "isn't a GGUF" in str(exc.value)


def test_check_rejects_truncated_header(tmp_path):
    path = tmp_path / "half.gguf"
    path.write_bytes(GGUF_FIXTURE[:20])
    with pytest.raises(gguf.GgufError):
        gguf.check_model_file(str(path))


def test_model_name_from_filename():
    assert gguf.model_name_from_filename(
        "/home/u/Downloads/My Model (Q4).gguf") == "my-model-q4"
    assert gguf.model_name_from_filename(
        "llama-3.1_8b.gguf") == "llama-3.1_8b"
    assert gguf.model_name_from_filename("") == "local-model"


def test_validate_model_name():
    assert gguf.validate_model_name("My-Model") == "my-model"
    assert gguf.validate_model_name("m:7b") == "m:7b"
    for bad in ("", "has space", "bang!", "/abs/path"):
        with pytest.raises(gguf.GgufError):
            gguf.validate_model_name(bad)


# ----------------------------------------------------------------------
# Import: check endpoint, create request shape, success stream
# ----------------------------------------------------------------------

def test_import_check_endpoint(api, gguf_file):
    res = api.post("/api/providers/ollama/import/check",
                   json={"path": gguf_file})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Tiny Test Model"
    assert body["architecture"] == "llama"
    assert body["suggested_name"] == "tiny-test-model"
    assert body["size_bytes"] == len(GGUF_FIXTURE)


def test_import_check_rejects_bad_file(api, tmp_path):
    res = api.post("/api/providers/ollama/import/check",
                   json={"path": str(tmp_path / "nope.gguf")})
    assert res.status_code == 400
    assert "No file exists" in res.json()["detail"]


CREATE_LINES = [
    b'{"status": "creating model layer"}\n',
    b'{"status": "writing manifest"}\n',
    b'{"status": "success"}\n',
]


def test_import_builds_the_create_request(api, monkeypatch,
                                          gguf_file):
    fake = install_fake(
        monkeypatch, FakeOllama(create_lines=CREATE_LINES))
    res = api.post("/api/providers/ollama/import",
                   json={"path": gguf_file})
    assert res.status_code == 200
    events = ndjson_lines(res.text)
    assert events[-1]["status"] == "success"
    create_calls = [(u, b) for u, b in fake.requests
                    if "/api/create" in u]
    assert len(create_calls) == 1
    body = create_calls[0][1]
    assert body["name"] == "tiny-test-model"
    assert body["modelfile"] == f"FROM {gguf_file}"
    assert body["stream"] is True


def test_import_honours_an_edited_name(api, monkeypatch, gguf_file):
    fake = install_fake(
        monkeypatch, FakeOllama(create_lines=CREATE_LINES))
    res = api.post("/api/providers/ollama/import",
                   json={"path": gguf_file, "name": "my-name"})
    assert res.status_code == 200
    body = [b for u, b in fake.requests if "/api/create" in u][0]
    assert body["name"] == "my-name"


def test_import_rejects_bad_file_before_calling_ollama(
        api, monkeypatch, tmp_path):
    fake = install_fake(
        monkeypatch, FakeOllama(create_lines=CREATE_LINES))
    res = api.post("/api/providers/ollama/import",
                   json={"path": str(tmp_path / "nope.gguf")})
    assert res.status_code == 400
    assert fake.requests == []


def test_imported_model_lands_in_the_installed_list(
        api, monkeypatch, gguf_file):
    fake = install_fake(monkeypatch, FakeOllama(
        tags=[], create_lines=CREATE_LINES))
    api.post("/api/providers/ollama/import",
             json={"path": gguf_file})
    # Ollama now holds it (the fake's tags change, as the real
    # server's would); the picker source lists it.
    fake.tags = [{"name": "tiny-test-model", "size": 123}]
    body = api.get("/api/providers/ollama/installed").json()
    assert [m["name"] for m in body["models"]] == \
        ["tiny-test-model"]


# ----------------------------------------------------------------------
# UI source guards
# ----------------------------------------------------------------------

def _read_index():
    with open(INDEX) as fh:
        return fh.read()


def test_ui_shared_progress_renderer():
    html = _read_index()
    assert "function makePullProgress(" in html
    assert "function streamNdjson(" in html
    assert "completed_all" in html
    assert "ev.percent" in html
    # Both pull consumers use the shared pair — no per-layer
    # rendering left in either.
    assert html.count("streamNdjson(\"/api/providers/ollama/pull\"") \
        == 2
    assert "ev.completed || 0" not in html


def test_ui_downloaded_group_in_wizard_and_settings():
    html = _read_index()
    assert html.count("Already downloaded — on this computer") >= 2
    assert "Ollama isn't running — start it to see " in html
    assert "· on this computer" in html
    assert "wizFillModelOptions" in html
    assert "wizLoadInstalled" in html
    assert "regroupSettingsOllamaModels" in html
    assert html.count("/api/providers/ollama/installed") >= 2
    # Selecting a downloaded model hides the download block.
    assert "wizSyncDownloadBlock" in html
    assert 'id="wizInstalledNote"' in html
    assert "Already on this computer — no download needed." in \
        " ".join(html.split())


def test_ui_import_controls_and_header_copy():
    html = _read_index()
    for el in ("wizImportPath", "wizImportName", "wizImportBtn",
               "wizImportInfo", "wizImportProgWrap",
               "wizImportProg", "wizImportMsg"):
        assert f'id="{el}"' in html, el
    # The division of labour, in the UI copy itself.
    assert "DeskDora checks the file's header; Ollama loads " \
           "and runs" in " ".join(html.split())
    assert "The file stays on this computer." in html
    assert "/api/providers/ollama/import/check" in html
    assert "/api/providers/ollama/import\"" in html
    assert "wizCheckImportFile" in html
    assert "wizImportModel" in html
