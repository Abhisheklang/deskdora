"""Wave 4 regression tests for DD-A4: validate_plan and
classify_step must be total over typed inputs.  Malformed typed
steps are rejected with PlanError at validation, and the
classifier returns a fail-closed verdict instead of raising.
Verdicts for well-typed steps are unchanged, so the wave 3 S1
property (the confirmation gate never contradicts the autonomy
classification over well-typed steps) still holds."""
import pytest

from deskdora.autonomy import AutonomyPolicy, evaluate
from deskdora.planners import PlanError, validate_plan
from deskdora.safety import ConfirmationGate, classify_step, deny_all


@pytest.mark.parametrize("action", [[], {}, 123, None, True])
def test_validate_plan_rejects_typed_action(action):
    # The audit's minimised case: {"action": []} raised TypeError
    # (unhashable) instead of PlanError.
    with pytest.raises(PlanError):
        validate_plan([{"action": action}])


@pytest.mark.parametrize("field", ["name_contains", "exact_name",
                                   "label_contains", "app_contains",
                                   "role"])
@pytest.mark.parametrize("value", [123, ["x"], {"a": 1}, 9.5])
def test_validate_plan_rejects_typed_target_fields(field, value):
    step = {"action": "click", "target": {field: value}}
    with pytest.raises(PlanError):
        validate_plan([step])


def test_validate_plan_rejects_typed_target_on_type_and_scroll():
    with pytest.raises(PlanError):
        validate_plan([{"action": "type", "text": "hi",
                        "target": {"name_contains": 123}}])
    with pytest.raises(PlanError):
        validate_plan([{"action": "scroll",
                        "target": {"name_contains": 123}}])


def test_validate_plan_accepts_well_typed_steps():
    plan = [{"action": "click",
             "target": {"name_contains": "Save", "nth": 0}},
            {"action": "key", "key": "Return"},
            {"action": "done", "answer": "ok"}]
    assert validate_plan(plan) is plan


# ----------------------------------------------------------------------
# classify_step: total, and fail-closed on unprovable shapes
# ----------------------------------------------------------------------

@pytest.mark.parametrize("step,verdict", [
    ({"action": "click", "target": {"name_contains": 123}},
     "destructive"),
    ({"action": "click", "target": ["not", "a", "dict"]},
     "destructive"),
    ({"action": "click", "target": {"name_contains": ["x"]}},
     "destructive"),
    ({"action": "key", "key": 123}, "safe"),
    ({"action": "key", "key": ["Delete"]}, "safe"),
    ("not-a-step", "destructive"),
    (["action", "click"], "destructive"),
])
def test_classify_step_total_over_typed_inputs(step, verdict):
    assert classify_step(step) == verdict


def test_classify_step_well_typed_verdicts_unchanged():
    assert classify_step(
        {"action": "click",
         "target": {"name_contains": "Delete"}}) == "destructive"
    assert classify_step(
        {"action": "click",
         "target": {"name_contains": "Open"}}) == "safe"
    assert classify_step(
        {"action": "launch", "command": "xterm"}) == "destructive"
    assert classify_step(
        {"action": "key", "key": "Delete",
         "context": "files"}) == "destructive"
    assert classify_step(
        {"action": "key", "key": "Return"}) == "safe"


def test_autonomy_evaluate_total_over_typed_target():
    policy = AutonomyPolicy({})
    # Previously raised AttributeError on .lower().
    decision, _category, _sig = evaluate(
        policy, {"action": "click",
                 "target": {"name_contains": 123}})
    assert decision in ("allow", "confirm", "deny")
    decision, _category, _sig = evaluate(
        policy, {"action": "launch", "command": 123})
    assert decision in ("allow", "confirm", "deny")


def test_s1_gate_autonomy_agreement_well_typed():
    """The wave 3 S1 property, spot-checked over well-typed
    steps: whenever autonomy orders a step confirmed, a deny-all
    gate consulted with that verdict refuses it, and the gate's
    own fallback classification agrees with classify_step."""
    policy = AutonomyPolicy({})
    steps = [
        {"action": "launch", "command": "xfce4-terminal"},
        {"action": "click", "target": {"name_contains": "Buy"}},
        {"action": "click", "target": {"name_contains": "Pay"}},
        {"action": "click", "target": {"name_contains": "Open"}},
        {"action": "click_at", "x": 10, "y": 10},
        {"action": "key", "key": "Return"},
        {"action": "type", "text": "hello"},
        {"action": "wait", "seconds": 1},
    ]
    for step in steps:
        decision, _category, significant = evaluate(policy, step)
        gate = ConfirmationGate(deny_all)
        if decision == "confirm":
            assert gate.check(
                step, requires_confirmation=True) is False
            assert gate.history, "policy was not consulted"
        # Gate fallback (no autonomy verdict) agrees with the
        # shared classifier for every well-typed step.
        fallback = ConfirmationGate(deny_all)
        expected = classify_step(step) != "destructive"
        assert fallback.check(step) is expected
        assert significant == (decision == "confirm")
