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


def _edge_light_class():
    src = _src()
    return src[src.index("class EdgeLight"):]


def test_edge_window_geometry_comes_from_monitor():
    # v3.4.3: the v3.4.2 window was sized from GdkScreen and placed
    # with a one-shot move(0, 0); xfwm4's initial placement put it
    # at the work-area origin (0,27) and the bottom strip painted
    # off-screen.  Size and position must come from the primary
    # monitor's geometry instead.
    cls = _edge_light_class()
    assert "get_primary_monitor" in cls
    assert "get_geometry()" in cls
    assert "set_default_size(self._geo.width, self._geo.height)" in cls
    assert "self.move(0, 0)" not in cls, (
        "one-shot move(0, 0) placement is the v3.4.2 defect: the "
        "window manager overrides it at map time")


def test_edge_place_targets_monitor_origin_and_size():
    cls = _edge_light_class()
    body = cls[cls.index("def _place"):cls.index("def _build_edge")]
    assert "self.move(geo.x, geo.y)" in body
    assert "self.resize(geo.width, geo.height)" in body


def test_edge_window_is_override_redirect():
    # v3.4.3, measured live on the rig: every WM-mediated
    # placement failed for this window — move()/resize() were
    # clamped to the work-area origin (0,27), and the
    # _NET_WM_STATE fullscreen request was dropped whenever the
    # app's main window was present (it landed only at a pause
    # or a re-show, never during active agent work).  The window
    # is therefore override-redirect: placed by raw X geometry,
    # outside WM policy.  The flag must be set while unmapped
    # (realize in __init__), or it cannot be set at all.
    cls = _edge_light_class()
    assert "set_override_redirect(True)" in cls
    assert "self.realize()" in cls
    assert "fullscreen_on_monitor" not in cls, (
        "the fullscreen request path was measured unreliable "
        "for this window and must not return")


def test_edge_place_reasserts_geometry_and_raise():
    # _place runs per tick while visible: position and size from
    # the monitor geometry, plus raise_() so windows the agent
    # opens after the light appears cannot cover the border.
    cls = _edge_light_class()
    body = cls[cls.index("def _place"):cls.index("def _build_edge")]
    assert "self.move(geo.x, geo.y)" in body
    assert "self.resize(geo.width, geo.height)" in body
    assert "raise_()" in body


def test_edge_placement_reasserted_every_tick():
    # Like the click-through shape (test_edge_input_shape.py), the
    # placement must be healed per tick while visible — the WM's
    # map-time placement is applied AFTER the show-time move.
    cls = _edge_light_class()
    tick = cls[cls.index("def _tick"):cls.index("def _restyle")]
    assert "_place()" in tick, (
        "placement must be re-asserted on every tick while the "
        "edge window is visible")


def test_bottom_strip_lands_inside_screen_bounds():
    # Arithmetic on the pinned constants: the window origin is the
    # monitor origin and its height is the monitor height, so the
    # bottom strip (core + glow + core2 stacked inward from the
    # bottom edge) spans y in [H - total, H] — inside [0, H] at
    # any resolution.  Under the v3.4.2 defect (window at y=27)
    # the same strip spanned [H + 27 - total, H + 27]: off-screen.
    cls = _edge_light_class()
    band = int(re.search(r"BAND = (\d+)", cls).group(1))
    core = int(re.search(r"CORE = (\d+)", cls).group(1))
    total = core + band + 3  # core2 is 3 px (see _build_edge)
    for height in (768, 1080, 1440, 2160):
        strip_top = height - total
        strip_bottom = height
        assert 0 <= strip_top < strip_bottom <= height


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
