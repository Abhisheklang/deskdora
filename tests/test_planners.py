import pytest

from deskdora.planners import (BuiltinPlanner, PlanError, validate_plan)


def test_validate_accepts_good_plan():
    plan = [{"action": "launch", "command": "thunar"},
            {"action": "click", "element": 3},
            {"action": "type", "text": "hi"},
            {"action": "key", "key": "Return"},
            {"action": "done"}]
    assert validate_plan(plan, element_count=5) == plan


@pytest.mark.parametrize("bad", [
    [],
    [{"action": "rm_rf"}],
    [{"action": "click"}],
    [{"action": "click", "element": -1}],
    [{"action": "click", "element": 99}],
    [{"action": "type"}],
    [{"action": "key", "key": ""}],
    [{"action": "wait", "seconds": 99}],
    [{"action": "extract", "pattern": "([", "name": "x"}],
    [{"action": "click", "target": {"bogus": 1}}],
    [{"action": "build_sheet", "source": "disk", "save_as": "x"}],
    ["not-a-dict"],
])
def test_validate_rejects_bad_plans(bad):
    with pytest.raises(PlanError):
        validate_plan(bad, element_count=10)


@pytest.fixture
def builtin(tmp_path):
    planner = BuiltinPlanner({
        "form": "python3 /fixtures/form_app.py",
        "site_url": "http://127.0.0.1:8901/index.html",
        "browser": "chromium",
    })
    return planner


def test_builtin_form_values(builtin):
    plan = builtin.plan(
        "Fill the form: name=Ada Lovelace, email=ada@example.org, "
        "city=Pune and submit", None)
    typed = [s["text"] for s in plan if s["action"] == "type"]
    assert typed == ["Ada Lovelace", "ada@example.org", "Pune"]
    clicks = [s for s in plan if s["action"] == "click"]
    assert clicks and clicks[-1]["target"]["name_contains"] == "Submit"


def test_builtin_article(builtin):
    plan = builtin.plan(
        "Open the article about Zorania and report its capital", None)
    extract = [s for s in plan if s["action"] == "extract"]
    assert extract and extract[0]["name"] == "capital"


def test_builtin_sheet(builtin):
    plan = builtin.plan(
        "Create a spreadsheet from /tmp/invoice.pdf saved as out.gnumeric",
        None)
    actions = [s["action"] for s in plan]
    assert "read_clipboard" in actions and "build_sheet" in actions


def test_builtin_organise_only_present_types(builtin, tmp_path):
    messy = tmp_path / "messy"
    messy.mkdir()
    (messy / "a.png").write_text("x")
    (messy / "b.txt").write_text("x")
    plan = builtin.plan(f"Organise folder {messy} by type", None)
    typed = [s.get("text", "") for s in plan if s["action"] == "type"]
    assert "*.png" in typed and "*.txt" in typed
    assert "*.mp3" not in typed  # no audio files seeded -> no audio steps


def test_builtin_unknown_goal_fails_honestly(builtin):
    with pytest.raises(PlanError):
        builtin.plan("Defragment the hard drive", None)


def test_builtin_chain(builtin):
    plan = builtin.plan("Open terminal then type echo hi then press Return",
                        None)
    actions = [s["action"] for s in plan]
    assert actions[0] == "launch" and "type" in actions and "key" in actions


def test_validate_click_at():
    ok = [{"action": "click_at", "x": 640, "y": 450},
          {"action": "done", "answer": "x"}]
    assert validate_plan(ok) is ok
    for bad in ({"action": "click_at", "x": -1, "y": 5},
                {"action": "click_at", "x": 5},
                {"action": "click_at", "x": "5", "y": 5}):
        with pytest.raises(PlanError):
            validate_plan([bad])


def test_parse_table_single_space_pdf_copy():
    from deskdora.agent import parse_table
    raw = ("DeskDora Test Invoice\nItem Qty Price\nApples 12 30\n"
           "Bread 5 25\nMilk 8 42\nEggs 30 6\nRice 10 55")
    rows = parse_table(raw)
    assert ["Item", "Qty", "Price"] in rows
    assert ["Apples", "12", "30"] in rows
    assert ["Rice", "10", "55"] in rows
    assert parse_table("") == []
    assert parse_table("singleword") == []
