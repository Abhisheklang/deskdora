"""Wave 3 regression tests for vibe V6."""
import pytest

from deskdora.planners import PlanError, validate_plan


def test_validate_plan_rejects_bool_ints():
    with pytest.raises(PlanError):
        validate_plan([{"action": "click", "element": True}])
    with pytest.raises(PlanError):
        validate_plan([{"action": "click_at", "x": True, "y": 5}])
    with pytest.raises(PlanError):
        validate_plan([{"action": "wait", "seconds": True}])
    # Real ints still pass.
    validate_plan([{"action": "click", "element": 1},
                   {"action": "done"}])


def test_validate_plan_caps_type_text():
    with pytest.raises(PlanError):
        validate_plan([{"action": "type", "text": "x" * 10001}])
    validate_plan([{"action": "type", "text": "x" * 100}])
