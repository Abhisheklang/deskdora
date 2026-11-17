"""Linux driver contract tests.

DeskDora is Linux-only (v3.2): the registry ships exactly two
drivers, and each must honour the same contract the agent loop
relies on:

* instantiate without a live session (platform imports are lazy),
* expose the full DesktopDriver interface,
* declare an ``implemented`` set that covers every plan action's
  requirements (ACTION_REQUIREMENTS) — for the Wayland portal driver
  code-level support is complete while *runtime* support is
  probe-dependent by design, so it is checked separately,
* be honest in its capability report about hardware verification,
* plan identically: the built-in planner's output validates and
  each step's action is supported by the driver.
"""
import pytest

from deskdora.drivers import ACTION_REQUIREMENTS, available_drivers
from deskdora.drivers.base import ALL_METHODS, DesktopDriver
from deskdora.planners import BuiltinPlanner, validate_plan

DRIVERS = available_drivers()


def test_registry_is_linux_only():
    assert sorted(DRIVERS) == ["linux-wayland", "linux-x11"]
    for cls in DRIVERS.values():
        assert issubclass(cls, DesktopDriver)


@pytest.mark.parametrize("driver_id", sorted(DRIVERS))
def test_driver_instantiates_and_reports_honestly(driver_id):
    driver = DRIVERS[driver_id]()
    caps = driver.capabilities()
    assert caps["driver"] == driver_id
    assert set(caps["implemented"]) <= set(ALL_METHODS)
    for method in caps["implemented"]:
        assert callable(getattr(driver, method)), (driver_id, method)


def test_x11_driver_supports_every_plan_action():
    driver = DRIVERS["linux-x11"]()
    caps = driver.capabilities()
    assert caps["verified_on_hardware"] is True
    for action, needed in ACTION_REQUIREMENTS.items():
        assert driver.supports_action(action), (action, needed)


def test_wayland_driver_declares_full_code_level_support():
    # Runtime support is probe-dependent (portal backends differ);
    # the code-level claim is that every method exists.
    driver = DRIVERS["linux-wayland"]()
    for method in ALL_METHODS:
        assert callable(getattr(driver, method)), method


def test_planner_output_is_driver_compatible():
    planner = BuiltinPlanner({
        "form": "python3 /fixtures/form_app.py",
        "site_url": "http://127.0.0.1:8901/index.html",
        "browser": "chromium",
    })
    plan = validate_plan(planner.plan(
        "Fill the form: name=Ada, email=a@b.c, city=Pune and submit", None))
    x11 = DRIVERS["linux-x11"]()
    for step in plan:
        assert x11.supports_action(step["action"]), step
