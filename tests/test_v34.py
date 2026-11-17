"""v3.4: diagnostics (measured, never hard-coded), templates,
desktop notifications (one call per event, toggles enforced),
and history-derived time estimates (or honest absence)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from deskdora import diagnostics, estimates, notify, server
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Element, Observation
from deskdora.templates import TemplateStore, builtin_templates

client = TestClient(server.app)


# ----------------------------------------------------------------------
# diagnostics
# ----------------------------------------------------------------------

class FakeDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self, n_elements=0):
        self.n = n_elements

    def observe(self):
        els = [Element(idx=i, app="thunar", window="Files",
                       role="label", name=f"f{i}", label="", x=0,
                       y=0, w=5, h=5) for i in range(self.n)]
        return Observation(elements=els, windows=["Files"], texts={})


def test_session_xorg_from_env():
    info = diagnostics.session_info(
        {"DISPLAY": ":99", "XDG_SESSION_TYPE": "x11",
         "XDG_CURRENT_DESKTOP": "XFCE"})
    assert info["type"] == "Xorg"
    assert info["desktop"] == "XFCE"


def test_session_wayland_from_env():
    info = diagnostics.session_info(
        {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"})
    assert info["type"] == "Wayland"


def test_atspi_health_bands():
    rich = diagnostics.atspi_info(FakeDriver(60))
    assert rich["apps"][0]["health"] == "rich"
    sparse = diagnostics.atspi_info(FakeDriver(3))
    assert sparse["apps"][0]["health"] == "sparse"
    assert sparse["total_elements"] == 3


def test_atspi_unreachable_is_honest():
    class BadDriver(FakeDriver):
        def observe(self):
            raise RuntimeError("bus gone")
    info = diagnostics.atspi_info(BadDriver())
    assert info["reachable"] is False
    assert "bus gone" in info["error"]


def test_summary_text_mentions_everything():
    diag = diagnostics.collect(FakeDriver(2), dict(
        server.DEFAULT_CONFIG))
    text = diagnostics.summary_text(diag)
    for needle in ("Session:", "Compositor:", "AT-SPI:",
                   "Portals:", "Provider Built-in", "Edge light:"):
        assert needle in text, needle


# ----------------------------------------------------------------------
# notifications
# ----------------------------------------------------------------------

def _recorder():
    calls = []
    return calls, (lambda argv: calls.append(argv))


def test_notify_fires_when_enabled():
    calls, runner = _recorder()
    assert notify.notify("run_completed", {}, body="done",
                         runner=runner) is True
    assert len(calls) == 1
    assert "run completed" in calls[0][-2]


def test_notify_master_off_makes_no_call():
    calls, runner = _recorder()
    cfg = {"feature_notifications": False}
    assert notify.notify("run_completed", cfg, runner=runner) is False
    assert calls == []


def test_notify_event_off_makes_no_call():
    calls, runner = _recorder()
    cfg = {"notifications": {"events": {"run_failed": False}}}
    assert notify.notify("run_failed", cfg, runner=runner) is False
    assert calls == []
    assert notify.notify("run_completed", cfg, runner=runner) is True


def test_notify_unknown_event_is_silent():
    calls, runner = _recorder()
    assert notify.notify("explosion", {}, runner=runner) is False
    assert calls == []


# ----------------------------------------------------------------------
# estimates
# ----------------------------------------------------------------------

def _run(root, name, goal, seconds, steps, status="success"):
    d = os.path.join(root, name)
    os.makedirs(d)
    events = [
        {"kind": "run_start", "goal": goal, "ts": 1000.0},
        {"kind": "run_end", "status": status, "ts": 1000.0 + seconds,
         "steps_executed": steps},
    ]
    with open(os.path.join(d, "log.jsonl"), "w") as fh:
        fh.write("\n".join(json.dumps(e) for e in events))


def test_estimate_from_exact_history(tmp_path):
    root = str(tmp_path)
    _run(root, "r1", "Organise folder /a/b by type", 120, 40)
    _run(root, "r2", "Organise folder /a/b by type", 180, 50)
    est = estimates.estimate_for_goal(
        "Organise folder /a/b by type", root)
    assert est["based_on"] == 2
    assert est["seconds"] == 150
    assert est["steps"] == 45
    assert "based on 2 previous runs" in estimates.format_estimate(est)


def test_estimate_from_similar_signature(tmp_path):
    root = str(tmp_path)
    _run(root, "r1", "Organise folder /a/b by type", 100, 30)
    est = estimates.estimate_for_goal(
        "Organise folder /x/y by type", root)
    assert est is not None and est["match"] == "similar"


def test_estimate_absent_without_history(tmp_path):
    assert estimates.estimate_for_goal("anything", str(tmp_path)) \
        is None
    assert estimates.format_estimate(None) == "no estimate yet"


def test_eta_needs_a_few_steps():
    assert estimates.eta_seconds(60, 2, 100) is None
    assert estimates.eta_seconds(60, 10, 100) == 540
    assert estimates.eta_seconds(60, 100, 100) is None


# ----------------------------------------------------------------------
# templates
# ----------------------------------------------------------------------

def test_builtin_templates_cover_the_minimum():
    goals = " ".join(t["goal"] for t in builtin_templates("/home/x"))
    for needle in ("/home/x/Downloads", "/home/x/Pictures",
                   "table.pdf", "/home/x/Desktop", "trash"):
        assert needle in goals, needle


def test_personal_template_store_roundtrip(tmp_path):
    store = TemplateStore(str(tmp_path / "templates.json"))
    item = store.add("Morning tidy", "Organise folder /x by type")
    assert store.list()[0]["name"] == "Morning tidy"
    assert store.delete(item["id"]) is True
    assert store.list() == []
    assert store.delete("nope") is False


def test_templates_api(tmp_path, monkeypatch):
    monkeypatch.setattr(server.MANAGER, "templates",
                        TemplateStore(str(tmp_path / "t.json")))
    data = client.get("/api/templates").json()
    assert len(data["builtin"]) >= 5
    created = client.post("/api/templates",
                          json={"name": "Mine", "goal": "Do x"})
    assert created.status_code == 200
    tid = created.json()["id"]
    assert client.delete(f"/api/templates/{tid}").status_code == 200
    assert client.delete(f"/api/templates/{tid}").status_code == 404


def test_estimate_endpoint_no_history(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path))
    out = client.get("/api/estimate",
                     params={"goal": "Organise folder /a by type"})
    assert out.json()["estimate"] is None


def test_v34_config_defaults():
    cfg = server.DEFAULT_CONFIG
    assert cfg["feature_notifications"] is True
    assert cfg["feature_estimates"] is True
    assert cfg["notifications"]["events"]["run_completed"] is True
    assert cfg["notifications"]["events"]["connection_failed"] is True
