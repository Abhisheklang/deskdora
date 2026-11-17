#!/usr/bin/env python3
"""DeskDora's native application window.

DeskDora is an app, not a web page: this module opens a real GTK
window (WebKit2GTK embedded) that displays the local control UI served
by the DeskDora service.  There is no browser chrome, no URL bar, and
no browser involved — the window *is* the application.

Run via the installed launcher (`deskdora-launcher`, the desktop
entry's Exec), which starts the service first and waits until it
answers before opening the window.
"""
import os
import sys

# --- rendering guards -------------------------------------------------------
# WebKitGTK's DMABUF renderer needs a GPU/DRI device; on virtual
# desktops (Xvfb, VMs without /dev/dri) it renders a blank window.
# Only when there is no DRI render node do we ask WebKit/Mesa for the
# software path, and only when the user hasn't set these themselves —
# a real GPU desktop is left completely untouched.
if not os.path.exists("/dev/dri/renderD128"):
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

import json  # noqa: E402
import math  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

from deskdora.overlay import (edge_state, overlay_enabled,  # noqa: E402
                              overlay_state)

DEFAULT_URL = "http://127.0.0.1:8765/"
APP_TITLE = "DeskDora"
ICON_CANDIDATES = (
    "/usr/share/icons/hicolor/scalable/apps/deskdora.svg",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "static", "logo.svg"),
)


class DeskDoraWindow(Gtk.Window):
    def __init__(self, url):
        super().__init__(title=APP_TITLE)
        self.url = url
        self.retries = 0
        self.set_default_size(1280, 840)
        self.set_size_request(860, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self._set_icon()

        self.view = WebKit2.WebView()
        settings = self.view.get_settings()
        settings.set_property("enable-developer-extras", False)
        settings.set_property("media-playback-requires-user-gesture", False)
        self.view.connect("load-failed", self._on_load_failed)
        self.view.load_uri(url)
        self.add(self.view)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()

    def _set_icon(self):
        # Prefer the themed icon installed with the package; fall back
        # to the bundled logo so dev runs still show the brand mark.
        try:
            self.set_icon_name("deskdora")
            if self.get_icon_name() == "deskdora":
                return
        except Exception:
            pass
        for path in ICON_CANDIDATES:
            if os.path.exists(path):
                try:
                    self.set_icon_from_file(path)
                    return
                except Exception:
                    continue

    def _on_load_failed(self, _view, _frame, _uri, _error):
        # The launcher waits for the service before opening us, but a
        # slow first start can still race.  Retry a few times instead
        # of leaving the user with WebKit's error page.
        if self.retries >= 12:
            return False  # give up retrying; show the error page
        self.retries += 1
        GLib.timeout_add(750, self._reload)
        return True  # handled: suppress the default error page

    def _reload(self):
        self.view.load_uri(self.url)
        return False


# --- floating control overlay (v3.2) ----------------------------------------
# A small always-on-top control window that lives on the desktop
# while the agent works.  It is a pure view + remote control over
# the service: it polls /api/status and its buttons POST to the same
# /api/kill, /api/pause, /api/resume and /api/reset endpoints the
# companion uses — there is no second control path.
#
# Focus safety: the window never accepts keyboard focus
# (accept_focus False, focus_on_map False), so clicking Freeze can
# never become the target of the agent's next typing step.  On a
# window manager that focuses it anyway, the agent's focus guard
# sees an unexpected active window and fails the step safe instead
# of typing into the wrong place — the designed behaviour.
OVERLAY_CSS = """
#overlay-card{background:#0A0B0A;border:1px solid rgba(206,222,98,.5);
  border-radius:14px;padding:12px 14px}
#ov-brand{color:#9E9F9F;font-family:monospace;font-size:9px;
  letter-spacing:2px}
#ov-dot{color:#CEDE62;font-size:11px}
#ov-status{color:#F4F4F4;font-family:monospace;font-size:9px;
  letter-spacing:1px}
#ov-goal{color:#F4F4F4;font-size:13px;font-weight:600}
#ov-planner{color:#5C5C5B;font-family:monospace;font-size:9px;
  letter-spacing:1px}
#ov-pct{color:#F4F4F4;font-size:24px;font-weight:700}
#ov-steps{color:#9E9F9F;font-family:monospace;font-size:10px;
  letter-spacing:1px}
#ov-bar trough{background:#1B1C19;border-radius:99px;min-height:6px}
#ov-bar progress{background:#CEDE62;border-radius:99px;min-height:6px}
button.ov-btn{font-family:monospace;font-size:10px;letter-spacing:1px;
  padding:6px 12px;border-radius:99px;background:none;
  border:1px solid rgba(92,92,91,.55);color:#F4F4F4;box-shadow:none}
button.ov-btn:hover{border-color:#CEDE62;color:#CEDE62}
button.ov-stop{border-color:#ECD06F;color:#ECD06F}
button.ov-stop:hover{background:#ECD06F;color:#040404}
button.ov-resume{background:#CEDE62;border-color:#CEDE62;color:#040404;
  font-weight:700}
button.ov-resume:hover{background:#DAF64C;color:#040404}
"""
OVERLAY_POS_PATH = os.path.expanduser(
    "~/.local/share/deskdora/overlay.json")


class StatusHub:
    """One poller for /api/status, shared by the desktop overlays."""

    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        self.status = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                with urllib.request.urlopen(
                        self.base + "/api/status", timeout=2) as resp:
                    self.status = json.load(resp)
            except Exception:
                self.status = None
            time.sleep(0.8)


class ControlOverlay(Gtk.Window):
    WIDTH = 308

    def __init__(self, hub):
        super().__init__(title="DeskDora Control")
        self.base = hub.base
        self._hub = hub
        self._positioned = False
        self._save_source = None
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_size_request(self.WIDTH, -1)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)

        provider = Gtk.CssProvider()
        provider.load_from_data(OVERLAY_CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        card.set_name("overlay-card")
        self.add(card)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        dot = Gtk.Label(label="●")
        dot.set_name("ov-dot")
        brand = Gtk.Label(label="DESKDORA")
        brand.set_name("ov-brand")
        self.status_label = Gtk.Label(label="")
        self.status_label.set_name("ov-status")
        self.status_label.set_hexpand(True)
        self.status_label.set_halign(Gtk.Align.END)
        head.pack_start(dot, False, False, 0)
        head.pack_start(brand, False, False, 0)
        head.pack_start(self.status_label, True, True, 0)
        card.pack_start(head, False, False, 0)

        self.goal_label = Gtk.Label(label="")
        self.goal_label.set_name("ov-goal")
        self.goal_label.set_line_wrap(True)
        self.goal_label.set_max_width_chars(38)
        self.goal_label.set_halign(Gtk.Align.START)
        card.pack_start(self.goal_label, False, False, 0)

        self.planner_label = Gtk.Label(label="")
        self.planner_label.set_name("ov-planner")
        self.planner_label.set_halign(Gtk.Align.START)
        card.pack_start(self.planner_label, False, False, 0)

        prog = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.pct_label = Gtk.Label(label="")
        self.pct_label.set_name("ov-pct")
        self.steps_label = Gtk.Label(label="")
        self.steps_label.set_name("ov-steps")
        self.steps_label.set_valign(Gtk.Align.CENTER)
        prog.pack_start(self.pct_label, False, False, 0)
        prog.pack_start(self.steps_label, False, False, 0)
        card.pack_start(prog, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.bar.set_name("ov-bar")
        card.pack_start(self.bar, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        self.btn_stop = self._button("STOP", "ov-stop", "/api/kill")
        self.btn_freeze = self._button("FREEZE", "", "/api/pause")
        self.btn_resume = self._button("RESUME", "ov-resume",
                                       "/api/resume")
        self.btn_reset = self._button("RESET", "", "/api/reset")
        for b in (self.btn_stop, self.btn_freeze, self.btn_resume,
                  self.btn_reset):
            btns.pack_start(b, False, False, 0)
        card.pack_start(btns, False, False, 2)

        # Dragging by the card moves the window; the position is
        # remembered across runs.
        self.connect("button-press-event", self._on_press)
        self.connect("configure-event", self._on_configure)
        self._load_position()
        GLib.timeout_add(350, self._apply)

    # -- plumbing ------------------------------------------------------
    def _button(self, label, css_class, path):
        btn = Gtk.Button(label=label)
        btn.get_style_context().add_class("ov-btn")
        if css_class:
            btn.get_style_context().add_class(css_class)
        btn.set_can_focus(False)
        btn.connect("clicked", lambda _b: self._post(path))
        return btn

    def _post(self, path):
        def send():
            try:
                req = urllib.request.Request(
                    self.base + path, data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(req, timeout=3).read()
            except Exception:
                pass
        threading.Thread(target=send, daemon=True).start()

    # -- view ------------------------------------------------------------
    def _apply(self):
        # Features panel: the floating window's master switch.  Off
        # means the window never appears, whatever the agent does.
        cfg = (self._hub.status or {}).get("config") or {}
        if not overlay_enabled(cfg):
            if self.get_visible():
                self.hide()
            return True
        st = overlay_state(self._hub.status)
        if not st["visible"]:
            if self.get_visible():
                self.hide()
            return True
        self.goal_label.set_text(st["goal"] or "Working")
        self.planner_label.set_text(st["planner_display"] or "")
        self.status_label.set_text(st["status_text"][:34])
        if st["percent"] is None:
            self.pct_label.set_text("")
            self.bar.set_fraction(0)
        else:
            self.pct_label.set_text(f"{st['percent']}%")
            self.bar.set_fraction(max(0.0, min(1.0,
                                               st["percent"] / 100.0)))
        self.steps_label.set_text(st["steps_text"])
        self.btn_stop.set_visible(st["can_stop"])
        self.btn_freeze.set_visible(st["can_freeze"])
        self.btn_resume.set_visible(st["can_resume"])
        self.btn_reset.set_visible(st["can_reset"])
        if not self.get_visible():
            self.show_all()
            self._place_once()
        return True

    # -- position ----------------------------------------------------------
    def _place_once(self):
        if self._positioned:
            return
        self._positioned = True
        if getattr(self, "_saved_pos", None):
            self.move(*self._saved_pos)
            return
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor()
            geo = monitor.get_geometry()
            w, h = self.get_size()
            self.move(geo.x + geo.width - w - 22,
                      geo.y + geo.height - h - 58)
        except Exception:
            pass

    def _load_position(self):
        try:
            with open(OVERLAY_POS_PATH) as fh:
                pos = json.load(fh)
            self._saved_pos = (int(pos["x"]), int(pos["y"]))
        except Exception:
            self._saved_pos = None

    def _on_press(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self.begin_move_drag(event.button, int(event.x_root),
                                 int(event.y_root), event.time)
        return False

    def _on_configure(self, _widget, _event):
        if not self.get_visible():
            return False
        if self._save_source is not None:
            GLib.source_remove(self._save_source)

        def save():
            self._save_source = None
            try:
                x, y = self.get_position()
                os.makedirs(os.path.dirname(OVERLAY_POS_PATH),
                            exist_ok=True)
                with open(OVERLAY_POS_PATH, "w") as fh:
                    json.dump({"x": x, "y": y}, fh)
            except Exception:
                pass
            return False

        self._save_source = GLib.timeout_add(700, save)
        return False


# --- edge light (v3.2) ------------------------------------------------------
# A neon border around the screen edges while the agent works, in
# the spirit of phone edge-notification lighting.  The window is
# fullscreen, transparent and CLICK-THROUGH: its X11 input shape is
# set to an empty region (XShape), so it can never intercept a
# click or a key, never takes focus, and the agent's own work is
# unaffected by it.
#
# Rendering is GTK CSS, not Cairo: the shipped runtime has no
# pycairo, and PyGObject cannot marshal a cairo context without it
# (measured — the draw signal raises TypeError).  Four gradient
# strips (core line + fading glow) are restyled by the state tick;
# like any transparency effect it wants a compositor (XFCE's or
# GNOME's) for the soft falloff.
#
# State mapping (deskora.overlay.edge_state, shared with the
# floating window): running = lime, awaiting confirmation = amber
# fast pulse, paused = amber steady, finished/idle = off.  The
# style (off|single|double|flow) is the user's Settings choice;
# "off" keeps the window hidden at all times — no animation runs.
LIME = (0.808, 0.871, 0.384)        # #CEDE62
LIME_HOT = (0.855, 0.965, 0.298)    # #DAF64C
GREEN = (0.45, 0.83, 0.32)
TEAL = (0.22, 0.78, 0.60)
AMBER = (0.925, 0.816, 0.435)       # #ECD06F
AMBER_DEEP = (0.93, 0.60, 0.22)
LIME_CYCLE = (LIME, GREEN, TEAL, LIME_HOT)
AMBER_CYCLE = (AMBER, AMBER_DEEP, (0.90, 0.72, 0.30), AMBER)


def _css_color(rgb, alpha=1.0):
    return (f"rgba({int(rgb[0] * 255)},{int(rgb[1] * 255)},"
            f"{int(rgb[2] * 255)},{alpha:.3f})")


def _cycle_color(cycle, t):
    n = len(cycle)
    pos = (t % 1.0) * n
    a = cycle[int(pos) % n]
    b = cycle[(int(pos) + 1) % n]
    f = pos - int(pos)
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


class EdgeLight(Gtk.Window):
    """CSS-rendered neon border.  Layout: a Gtk.Overlay holding four
    edge assemblies (top/bottom/left/right); each assembly is a core
    strip (the bright line at the very edge) plus a glow strip whose
    gradient fades inward.  Styles restyle the same widgets."""

    BAND = 14      # glow band thickness, px
    CORE = 3       # bright line thickness, px

    def __init__(self, hub):
        super().__init__(title="DeskDora Edge")
        self.set_name("edge-root")
        self._hub = hub
        self._state = {"visible": False, "mode": "off",
                       "style": "single", "animated": False}
        self._phase = 0.0
        self._pulse_t = 0.0
        self._css_cache = None
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        screen = self.get_screen()
        self.set_default_size(screen.get_width(), screen.get_height())
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            screen, self.provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        overlay = Gtk.Overlay()
        base = Gtk.Box()
        base.set_name("edge-base")
        overlay.add(base)
        self.strips = {}
        for edge in ("top", "bottom", "left", "right"):
            assembly = self._build_edge(edge)
            overlay.add_overlay(assembly)
            overlay.set_overlay_pass_through(assembly, True)
        self.add(overlay)
        self.connect("map-event", self._apply_click_through)
        GLib.timeout_add(60, self._tick)

    def _build_edge(self, edge):
        vertical = edge in ("left", "right")
        box = Gtk.Box(
            orientation=(Gtk.Orientation.VERTICAL if vertical
                         else Gtk.Orientation.HORIZONTAL))
        box.set_name(f"edge-{edge}")
        if edge == "top":
            box.set_valign(Gtk.Align.START)
        elif edge == "bottom":
            box.set_valign(Gtk.Align.END)
        elif edge == "left":
            box.set_halign(Gtk.Align.START)
        else:
            box.set_halign(Gtk.Align.END)

        core = Gtk.Box()
        core.set_name(f"edge-{edge}-core")
        glow = Gtk.Box()
        glow.set_name(f"edge-{edge}-glow")
        core2 = Gtk.Box()
        core2.set_name(f"edge-{edge}-core2")
        if vertical:
            core.set_size_request(self.CORE, -1)
            glow.set_size_request(self.BAND, -1)
            core2.set_size_request(2, -1)
        else:
            core.set_size_request(-1, self.CORE)
            glow.set_size_request(-1, self.BAND)
            core2.set_size_request(-1, 2)
        # Order from the screen edge inward depends on the edge.
        order = ((core, glow, core2) if edge in ("top", "left")
                 else (core2, glow, core))
        for widget in order:
            box.pack_start(widget, False, False, 0)
        self.strips[edge] = (core, glow, core2)
        return box

    def _apply_click_through(self, *_args):
        # Empty X11 input shape (ShapeInput, 0 rectangles): every
        # click and key passes straight through to the window
        # beneath.  This is the whole safety story of the edge
        # light — it is paint, not a surface.  X11-only; on a
        # non-X11 backend the window still never requests focus.
        # GTK re-applies its own (full-window) input shape after
        # map/configure, which silently undid a one-shot call —
        # measured live: the mapped window reported 1 full-screen
        # input rectangle and swallowed every desktop click.  So
        # the shape is re-applied on EVERY tick while visible:
        # any reset is healed within one tick (60 ms).
        try:
            import ctypes
            from gi.repository import GdkX11
            gdk_win = self.get_window()
            if gdk_win is None:
                return False
            xid = GdkX11.Window.get_xid(gdk_win)
            display = GdkX11.Display.get_xdisplay(self.get_display())
            xext = ctypes.CDLL("libXext.so.6")
            xext.XShapeCombineRectangles(
                ctypes.c_void_p(display), ctypes.c_ulong(xid),
                ctypes.c_int(2), ctypes.c_int(0), ctypes.c_int(0),
                None, ctypes.c_int(0), ctypes.c_int(0),
                ctypes.c_int(0))
            x11 = ctypes.CDLL("libX11.so.6")
            x11.XFlush(ctypes.c_void_p(display))
        except Exception:
            pass
        return False

    # -- state ---------------------------------------------------------
    def _tick(self):
        status = self._hub.status
        setting = "single"
        motion = True
        if status:
            cfg = status.get("config") or {}
            setting = cfg.get("edge_light", "single")
            # Features panel: UI animations off = the border holds
            # steady instead of pulsing/flowing.
            motion = cfg.get("feature_animations", True) is not False
        st = edge_state(status, setting)
        if not motion:
            st = {**st, "animated": False}
        changed = (st["visible"] != self._state["visible"]
                   or st["mode"] != self._state["mode"]
                   or st["style"] != self._state["style"])
        self._state = st
        if not st["visible"]:
            if self.get_visible():
                self.hide()
            return True
        if not self.get_visible():
            self.show_all()
            self.move(0, 0)
            changed = True
        self._apply_click_through()
        if st["animated"]:
            speed = 2.3 if st["mode"] == "amber-fast" else 1.0
            self._phase = (self._phase + 0.004 * speed) % 1.0
            self._pulse_t += 0.06 * speed
            self._restyle()
        elif changed:
            self._restyle()
        return True

    def _restyle(self):
        st = self._state
        amber = st["mode"].startswith("amber")
        base = AMBER if amber else LIME
        cycle = AMBER_CYCLE if amber else LIME_CYCLE
        if st["mode"] == "amber-steady":
            alpha = 0.95
        elif st["animated"]:
            speed = 2.3 if st["mode"] == "amber-fast" else 1.0
            alpha = 0.74 + 0.26 * math.sin(self._pulse_t * speed)
        else:
            alpha = 0.95
        style = st["style"]
        rules = ["#edge-root, #edge-base { background: transparent; }"]
        for edge in ("top", "bottom", "left", "right"):
            vertical = edge in ("left", "right")
            # gradient direction: fade from the edge inward
            inward = {"top": "to bottom", "bottom": "to top",
                      "left": "to right", "right": "to left"}[edge]
            along = "to bottom" if vertical else "to right"
            if style == "flow":
                stops = ", ".join(
                    f"{_css_color(_cycle_color(cycle, self._phase + k / 5.0), alpha)} {k * 25}%"
                    for k in range(5))
                core_bg = f"linear-gradient({along}, {stops})"
                glow_bg = (f"linear-gradient({inward}, "
                           f"{_css_color(base, alpha * 0.5)} 0%, "
                           f"transparent 100%)")
                core2_bg = "transparent"
            else:
                core_bg = _css_color(base, alpha)
                glow_bg = (f"linear-gradient({inward}, "
                           f"{_css_color(base, alpha * 0.45)} 0%, "
                           f"transparent 100%)")
                core2_bg = (_css_color(base, alpha * 0.65)
                            if style == "double" else "transparent")
            rules.append(
                f"#edge-{edge}-core {{ background: {core_bg}; }}")
            rules.append(
                f"#edge-{edge}-glow {{ background: {glow_bg}; }}")
            rules.append(
                f"#edge-{edge}-core2 {{ background: {core2_bg}; }}")
        css = "\n".join(rules)
        if css != self._css_cache:
            self._css_cache = css
            self.provider.load_from_data(css.encode())


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    url = argv[0] if argv else DEFAULT_URL
    hub = StatusHub(url)
    DeskDoraWindow(url)
    ControlOverlay(hub)
    EdgeLight(hub)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
