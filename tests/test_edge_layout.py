"""Regression guards for a measured live failure: in the v3.2
videos the edge light never appeared on screen during runs, and
the floating window was camouflaged against the app's own black
background.

Root cause (edge light): _build_edge chose the Gtk.Box orientation
inverted — top/bottom assemblies were HORIZONTAL boxes, so the
core/glow/core2 strips sat side by side with zero natural width
instead of stacking from the screen edge inward; left/right were
the mirror image.  The window mapped, the state mapping ran, the
click-through shape applied — and the strips painted nothing.
Pixel checks on run-active frames of the shipped videos show no
lime pixel on any screen edge.

These checks pin the corrected layout in the source (the suite
runs without a GTK display, same approach as
test_edge_input_shape.py) so the inversion cannot quietly return.
"""
import os
import re

WINDOW_PY = os.path.join(os.path.dirname(__file__), "..", "src",
                         "deskdora", "window.py")


def _src():
    return open(WINDOW_PY).read()


def _build_edge_body():
    src = _src()
    start = src.index("def _build_edge")
    return src[start:src.index("def _x_display")]


def test_edge_box_orientation_stacks_inward():
    body = _build_edge_body()
    # left/right ("vertical" edges) stack HORIZONTALLY inward;
    # top/bottom stack VERTICALLY inward.
    assert ("Gtk.Orientation.HORIZONTAL if vertical"
            in body), ("edge strips must stack along the axis "
                       "pointing into the screen")
    assert ("Gtk.Orientation.VERTICAL if vertical"
            not in body), ("inverted orientation: strips collapse "
                           "to zero size and the border is invisible")


def test_edge_strips_have_visible_thickness():
    src = _src()
    cls = src[src.index("class EdgeLight"):]
    band = int(re.search(r"BAND = (\d+)", cls).group(1))
    core = int(re.search(r"CORE = (\d+)", cls).group(1))
    assert core >= 5, "the bright line must survive video compression"
    assert band >= 14, "the glow band must read as a glow, not a hairline"


def test_edge_pulse_never_fades_to_faint():
    src = _src()
    cls = src[src.index("class EdgeLight"):]
    m = re.search(r"alpha = ([\d.]+) \+ ([\d.]+) \* math\.sin", cls)
    assert m, "animated alpha pulse not found"
    floor = float(m.group(1)) - float(m.group(2))
    assert floor >= 0.70, f"pulse alpha floor {floor:.2f} is too faint"


def test_floating_card_contrasts_with_app_background():
    src = _src()
    css = src[src.index("OVERLAY_CSS"):src.index("OVERLAY_POS_PATH")]
    card = re.search(r"#overlay-card\{([^}]*)\}", css).group(1)
    # The app background is #040404; a near-black card with a 1px
    # 50%-alpha border vanished against it in the v3.2 videos.
    assert "border:2pxsolid#CEDE62" in card.replace(" ", ""), \
        "floating card needs a full-strength 2px lime border"
    assert "box-shadow" in card, \
        "floating card needs a shadow to lift it off dark backgrounds"
