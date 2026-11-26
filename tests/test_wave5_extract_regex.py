"""Wave 5 regression tests for EXTRACT-REGEX: the extract step ran
a planner-supplied regex with no complexity or time bound, so
(a+)+$ over a 29-char haystack took 36.65 s through the real
Agent._exec_primitive path.  Nested-quantifier patterns are now
refused at validation and again at execution, and every search
runs under a wall-clock budget.  Benign patterns keep working."""
import time

import pytest

from deskdora.agent import Agent, StepFailure
from deskdora.planners import (PlanError, extract_pattern_problem,
                               validate_plan)


# ----------------------------------------------------------------------
# Validation: nested quantifiers refused, benign patterns accepted
# ----------------------------------------------------------------------

@pytest.mark.parametrize("pattern", [
    r"(a+)+$",
    r"(a*)*",
    r"(a+){2,}",
    r"(x|y+)*",
    r"((a+))+",
    r"(a?)+$",
    r"(\w+\s?)*$",
])
def test_validate_rejects_nested_quantifier_patterns(pattern):
    with pytest.raises(PlanError):
        validate_plan([{"action": "extract", "pattern": pattern,
                        "name": "x"}], element_count=0)
    assert extract_pattern_problem(pattern) is not None


@pytest.mark.parametrize("pattern", [
    r"capital is (\w+)",
    r"total: ([0-9]+)",
    r"(foo|bar)+ baz",
    r"a+b*c?d",
    r"[0-9]{2,4}-[0-9]{2}",
    r"file\.txt",
    r"(?:Mr|Mrs)\. (\w+)",
    r"price \$([0-9]+(?:\.[0-9]{2})?)",
    r"\[Draft\] (v[0-9]+)",
    r"a{2,3}b",
    r"(ab)+cd",
    r"colou?r",
])
def test_validate_accepts_benign_patterns(pattern):
    plan = [{"action": "extract", "pattern": pattern, "name": "x"}]
    assert validate_plan(plan, element_count=0) == plan
    assert extract_pattern_problem(pattern) is None


def test_validate_still_rejects_uncompilable_pattern():
    with pytest.raises(PlanError):
        validate_plan([{"action": "extract", "pattern": "([",
                        "name": "x"}], element_count=0)


# ----------------------------------------------------------------------
# Execution through the real Agent._exec_primitive path
# ----------------------------------------------------------------------

class _Obs:
    def __init__(self, text):
        self.texts = {"w": text}


class _Driver:
    def __init__(self, text):
        self._text = text

    def observe(self):
        return _Obs(self._text)


class _Kill:
    def is_tripped(self):
        return False


def _agent(text):
    ag = Agent.__new__(Agent)
    ag.d = _Driver(text)
    ag.answers = {}
    ag.kill = _Kill()
    ag.cancel_event = None
    return ag


def test_exec_refuses_catastrophic_pattern_fast():
    ag = _agent("a" * 28 + "!")
    start = time.time()
    with pytest.raises(StepFailure) as exc:
        ag._exec_primitive({"action": "extract",
                            "pattern": r"(a+)+$", "name": "x"})
    assert time.time() - start < 5
    assert "refused" in str(exc.value)


def test_exec_budget_bounds_unbounded_backtracking():
    # (a|aa)+$ has no nested quantifier, so it passes the shape
    # check; the wall-clock budget is what bounds it.
    ag = _agent("a" * 28 + "!")
    start = time.time()
    with pytest.raises(StepFailure) as exc:
        ag._exec_primitive({"action": "extract",
                            "pattern": r"(a|aa)+$", "name": "x"})
    elapsed = time.time() - start
    assert elapsed < 10
    assert "timed out" in str(exc.value) or "not found" in str(exc.value)


def test_exec_benign_pattern_still_extracts():
    ag = _agent("The capital is Paris today")
    out = ag._exec_primitive({"action": "extract",
                              "pattern": r"capital is (\w+)",
                              "name": "capital"})
    assert ag.answers["capital"] == "Paris"
    assert "Paris" in out


def test_exec_benign_no_match_still_step_failure():
    ag = _agent("nothing to see here")
    with pytest.raises(StepFailure):
        ag._exec_primitive({"action": "extract",
                            "pattern": r"total: ([0-9]+)",
                            "name": "total"})
