"""Ask about my screen, plain-words routines, teach-from-history
parameterisation, and the run report export (v3.3)."""
import json
import os

import pytest

from deskdora.ask import answer_question, build_digest
from deskdora.perception import Element, Observation
from deskdora.report import render_report
from deskdora.routines import (describe_routine, parameterize_goal,
                               render_goal)


def obs():
    elements = [
        Element(idx=0, app="thunar", window="Downloads", role="label",
                name="invoice-march.pdf", label="", x=0, y=0, w=10,
                h=10),
        Element(idx=1, app="gnumeric", window="budget.gnumeric",
                role="label", name="Total due $420", label="", x=0,
                y=0, w=10, h=10),
    ]
    return Observation(elements=elements,
                       windows=["Downloads", "budget.gnumeric"],
                       texts={})


# ----------------------------------------------------------------------
# ask about my screen
# ----------------------------------------------------------------------

def test_ask_builtin_extractive_and_honest():
    out = answer_question("what is the total due?", obs(), "Built-in")
    assert out["mode"] == "builtin"
    assert "Total due $420" in out["answer"]
    assert "simple on-screen text lookup only" in out["answer"]


def test_ask_builtin_lists_windows():
    out = answer_question("what windows are open?", obs(), "Built-in")
    assert "Downloads" in out["answer"]
    assert "budget.gnumeric" in out["answer"]


def test_ask_builtin_no_match_says_so():
    out = answer_question("is there a zebra?", obs(), "Built-in")
    assert "Nothing on screen mentions" in out["answer"]


def test_ask_llm_answer_labelled_with_provider():
    out = answer_question(
        "summarise", obs(), "OpenAI · gpt-4o-mini",
        completer=lambda system, user: "Two windows are open.")
    assert out["mode"] == "llm"
    assert out["provider"] == "OpenAI · gpt-4o-mini"
    assert out["answer"] == "Two windows are open."


def test_ask_llm_failure_falls_back_to_builtin():
    def boom(system, user):
        raise RuntimeError("provider down")
    out = answer_question("total due?", obs(), "OpenAI · gpt-4o-mini",
                          completer=boom)
    assert out["mode"] == "builtin"
    assert "Total due $420" in out["answer"]


def test_digest_groups_by_window():
    digest = build_digest(obs())
    assert "invoice-march.pdf" in digest
    assert "[Downloads]" in digest


# ----------------------------------------------------------------------
# teach a routine: parameterisation
# ----------------------------------------------------------------------

def test_parameterize_organise_goal():
    goal = "Organise folder /home/test/Downloads by type"
    template, params = parameterize_goal(goal)
    assert template == "Organise folder {folder} by type"
    assert params == [{"name": "folder",
                       "default": "/home/test/Downloads"}]
    routine = {"goal": goal, "goal_template": template,
               "params": params}
    assert render_goal(routine) == goal
    assert render_goal(routine, {"folder": "/tmp/other"}) == \
        "Organise folder /tmp/other by type"


def test_parameterize_pdf_goal_with_filename():
    goal = ("Create a spreadsheet from /home/test/docs/invoice.pdf "
            "saved as deskdora-report.gnumeric")
    template, params = parameterize_goal(goal)
    names = {p["name"] for p in params}
    assert "path" in names and "filename" in names
    routine = {"goal": goal, "goal_template": template,
               "params": params}
    assert render_goal(routine) == goal


def test_render_goal_without_template_is_the_goal():
    assert render_goal({"goal": "plain goal"}) == "plain goal"


# ----------------------------------------------------------------------
# routines in plain words
# ----------------------------------------------------------------------

def test_describe_every_morning_organise_downloads():
    draft = describe_routine("every morning organise my Downloads")
    assert draft["schedule"] == "0 9 * * *"
    assert draft["goal"] == (
        "Organise folder "
        + os.path.expanduser("~/Downloads") + " by type")


def test_describe_weekly_tidy_desktop():
    draft = describe_routine("every Monday tidy my Desktop")
    assert draft["schedule"] == "0 9 * * 1"
    assert "Desktop" in draft["goal"]


def test_describe_daily_at_time():
    draft = describe_routine("daily at 18:30 organise my Documents")
    assert draft["schedule"] == "30 18 * * *"


def test_describe_folder_trigger():
    draft = describe_routine(
        "organise my Downloads when files land in ~/Downloads")
    assert draft["trigger"] == {
        "folder": os.path.expanduser("~/Downloads")}
    assert draft["schedule"] is None


def test_describe_refuses_without_schedule():
    with pytest.raises(ValueError, match="when it should run"):
        describe_routine("organise my Downloads")


def test_describe_refuses_gibberish():
    with pytest.raises(ValueError):
        describe_routine("every morning")


# ----------------------------------------------------------------------
# run report export
# ----------------------------------------------------------------------

def test_report_is_self_contained(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "001-step0.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    events = [
        {"kind": "run_start", "goal": "Organise <b>carefully</b>",
         "planner_mode": "ollama:qwen2.5:1.5b", "source": "ui",
         "ts": 1000.0},
        {"kind": "plan", "steps": [
            {"action": "launch", "command": "thunar"},
            {"action": "done", "answer": "ok"}], "ts": 1001.0},
        {"kind": "step_start", "index": 0, "ts": 1002.0},
        {"kind": "step_result", "index": 0, "ok": True,
         "detail": "launched thunar", "ts": 1004.0,
         "screenshot": "001-step0.png"},
        {"kind": "step_result", "index": 1, "ok": True,
         "detail": "done", "ts": 1005.0},
        {"kind": "file_op", "op": "move", "src": "/a/x.txt",
         "dst": "/a/Text/x.txt", "ts": 1004.5},
        {"kind": "run_end", "status": "success", "answer": "done",
         "steps_executed": 2, "ts": 1006.0},
    ]
    (run_dir / "log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events))
    html = render_report(str(run_dir))
    assert "Ollama · qwen2.5:1.5b" in html
    assert "Organise &lt;b&gt;carefully&lt;/b&gt;" in html
    assert "data:image/png;base64," in html
    assert "Save as PDF" in html
    assert "/a/Text/x.txt" in html
    assert "http://" not in html and "https://" not in html
