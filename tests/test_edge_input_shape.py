"""Regression guard for a measured live failure: the edge light's
fullscreen window had its empty X11 input shape applied only once
(on map), and GTK re-applied its own full-window shape right
after — the mapped window then swallowed every click on the
desktop (a video recording died on exactly this: queue resume and
rail clicks stopped landing once the border appeared).

The fix re-applies the empty ShapeInput shape on every tick while
the window is visible.  These checks pin that behaviour in the
source so the one-shot version cannot quietly return."""
import os

WINDOW_PY = os.path.join(os.path.dirname(__file__), "..", "src",
                         "deskdora", "window.py")


def _edge_light_source():
    src = open(WINDOW_PY).read()
    start = src.index("class EdgeLight")
    return src[start:]


def test_shape_applied_in_tick_not_only_on_map():
    cls = _edge_light_source()
    tick = cls[cls.index("def _tick"):]
    tick = tick[:tick.index("def _restyle")]
    assert "_apply_click_through" in tick, (
        "the empty input shape must be re-applied on every tick "
        "while the edge window is visible")


def test_shape_call_uses_empty_shapeinput():
    cls = _edge_light_source()
    body = cls[cls.index("def _apply_click_through"):]
    body = body[:body.index("# -- state")]
    assert "XShapeCombineRectangles" in body
    assert "c_int(2)" in body          # ShapeInput
    assert "None, ctypes.c_int(0)" in body  # 0 rectangles = empty


def test_shape_call_uses_instance_xid_methods():
    # The original bug: GdkX11.Window.get_xid / GdkX11.Display
    # .get_xdisplay do not exist in this GDK build (X11Window /
    # X11Display), so every call raised AttributeError and the
    # shape was never applied.  Only instance methods are safe.
    cls = _edge_light_source()
    body = cls[cls.index("def _apply_click_through"):]
    body = body[:body.index("# -- state")]
    assert "GdkX11.Window" not in body
    assert "GdkX11.Display" not in body
    assert "gdk_win.get_xid()" in body
    assert ".get_xdisplay()" in body
