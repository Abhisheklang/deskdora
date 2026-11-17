"""Linux / Wayland driver: AT-SPI perception + xdg-desktop-portal.

Wayland deliberately gives applications no global input injection and
no silent screen capture, so this driver goes through the sanctioned
channel — **xdg-desktop-portal**:

* Perception uses AT-SPI exactly like the X11 driver: the
  accessibility bus is session-wide and works under Wayland for
  GTK/Qt applications.
* Screenshots use ``org.freedesktop.portal.Screenshot``.
* Keyboard/pointer input uses ``org.freedesktop.portal.RemoteDesktop``
  (CreateSession → SelectDevices → Start → Notify*).  The compositor
  asks the user for consent when the session starts; a denial is a
  hard, honest capability failure, never a fallback to X11 tricks.
* The clipboard uses ``wl-paste``/``wl-copy`` when installed.
* There is **no** Wayland API to raise another app's window or to ask
  which window is focused, so ``activate_window`` returns False and
  ``active_window_title`` returns "" — the agent's focus guard falls
  back to the AT-SPI focused element in that case (see agent.py).

Whether any of this *works* depends on the compositor and the portal
backend actually running (GNOME's backend implements RemoteDesktop;
the wlroots backend historically implements Screenshot but not
RemoteDesktop).  ``probe()`` measures the real level — portal present,
screenshot granted, input devices granted — and reports it verbatim.
Nothing in this module claims more than a probe has shown.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

from .. import actions, perception
from .base import CapabilityError, DesktopDriver, DriverError

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
IFACE_SCREENSHOT = "org.freedesktop.portal.Screenshot"
IFACE_REMOTE = "org.freedesktop.portal.RemoteDesktop"
IFACE_CAST = "org.freedesktop.portal.ScreenCast"
IFACE_REQUEST = "org.freedesktop.portal.Request"

# evdev keycodes for the keysym names DeskDora's plans use (xdotool
# spelling).  Portal RemoteDesktop speaks evdev keycodes, not keysyms.
KEYCODES = {
    "escape": 1, "backspace": 14, "tab": 15, "return": 28, "enter": 28,
    "space": 57, "home": 102, "end": 107, "page_up": 104,
    "page_down": 109, "insert": 110, "delete": 111,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "ctrl": 29, "control": 29, "ctrl_l": 29, "ctrl_r": 97,
    "shift": 42, "shift_l": 42, "shift_r": 54,
    "alt": 56, "alt_l": 56, "alt_r": 100,
    "super": 125, "super_l": 125, "meta": 125,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "minus": 12, "equal": 13, "bracketleft": 26, "bracketright": 27,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
    "comma": 51, "period": 52, "slash": 53,
}
for _i, _ch in enumerate("1234567890"):
    KEYCODES[_ch] = 2 + _i
for _ch, _code in zip("qwertyuiop", range(16, 26)):
    KEYCODES[_ch] = _code
for _ch, _code in zip("asdfghjkl", range(30, 39)):
    KEYCODES[_ch] = _code
for _ch, _code in zip("zxcvbnm", range(44, 51)):
    KEYCODES[_ch] = _code

# Characters typeable via a single keycode (+Shift).  Anything else is
# an honest CapabilityError — portal keycode input has no "type this
# Unicode string" call.
_TYPEABLE = {c: (KEYCODES[c], False) for c in
             "abcdefghijklmnopqrstuvwxyz1234567890"}
_TYPEABLE.update({c.upper(): (KEYCODES[c], True)
                  for c in "abcdefghijklmnopqrstuvwxyz"})
_TYPEABLE.update({
    " ": (57, False), "\n": (28, False), "\t": (15, False),
    "-": (12, False), "_": (12, True), "=": (13, False), "+": (13, True),
    ",": (51, False), "<": (51, True), ".": (52, False), ">": (52, True),
    "/": (53, False), "?": (53, True), ";": (39, False), ":": (39, True),
    "'": (40, False), '"': (40, True), "[": (26, False), "{": (26, True),
    "]": (27, False), "}": (27, True), "\\": (43, False), "|": (43, True),
    "`": (41, False), "~": (41, True),
    "!": (2, True), "@": (3, True), "#": (4, True), "$": (5, True),
    "%": (6, True), "^": (7, True), "&": (8, True), "*": (9, True),
    "(": (10, True), ")": (11, True),
})

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112


class PortalError(DriverError):
    pass


class PortalBus:
    """Interface between the driver and the portal.  The real
    implementation (GioPortalBus) speaks D-Bus; tests inject a fake
    so the request construction and response handling are verified
    without a compositor.

    Signatures are stated explicitly at every call site: D-Bus is
    strict about them (an object path is ``o``, never ``s``)."""

    def name_has_owner(self, name):  # pragma: no cover - interface
        raise NotImplementedError

    def call(self, interface, method, signature, params):  # pragma: no cover
        """Plain synchronous method call on the portal object.
        ``params`` is a tuple of Python values (dicts become a{sv}).
        Returns the unwrapped result tuple."""
        raise NotImplementedError

    def call_fd(self, interface, method, signature, params):  # pragma: no cover
        """Like call(), for methods whose reply carries a Unix fd
        (OpenPipeWireRemote).  Returns (result_tuple, fd)."""
        raise NotImplementedError

    def request(self, interface, method, signature, params, timeout=60):
        """Portal request-style call: returns (response_code, results)
        from the Request Response signal."""
        raise NotImplementedError

    def await_request(self, request_path, timeout=60):  # pragma: no cover
        """Wait for the Response signal of an already-issued request
        (portal 1.18 returns a Request path from CreateSession)."""
        raise NotImplementedError

    def create_session(self, interface, timeout=30):  # pragma: no cover
        """Create a portal session, subscribing to the Response
        *before* issuing the call (the reply can race a
        subscribe-after-call).  Returns the session handle."""
        raise NotImplementedError


def _variant(value):
    from gi.repository import GLib
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("u", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, dict):
        return GLib.Variant("a{sv}", {k: _variant(v) for k, v in value.items()})
    raise PortalError(f"cannot encode portal option {value!r}")


class GioPortalBus(PortalBus):
    """Real D-Bus transport via Gio (synchronous calls + a pumped
    main-context wait for Request Response signals)."""

    def __init__(self):
        try:
            from gi.repository import Gio
        except ImportError as exc:
            raise PortalError(f"PyGObject (gi) is required: {exc}")
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def name_has_owner(self, name):
        try:
            res = self._conn.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                _params("(s)", (name,)), None, 0, 5000, None)
            return bool(res.unpack()[0])
        except Exception:
            return False

    def call(self, interface, method, signature, params):
        variant = _params(signature, params)
        res = self._conn.call_sync(PORTAL_BUS, PORTAL_PATH, interface,
                                   method, variant, None, 0, 30000, None)
        return res.unpack()

    def call_fd(self, interface, method, signature, params):
        import os as _os
        variant = _params(signature, params)
        res, fd_list = self._conn.call_with_unix_fd_list_sync(
            PORTAL_BUS, PORTAL_PATH, interface, method, variant, None, 0,
            30000, None, None)
        unpacked = res.unpack()
        if fd_list is None or fd_list.get_length() == 0:
            raise PortalError(f"{interface}.{method} returned no fd")
        return unpacked, _os.dup(fd_list.get(unpacked[0]))

    def _request_path(self, token):
        unique = self._conn.get_unique_name() or ""
        return ("/org/freedesktop/portal/desktop/request/"
                + unique.lstrip(":").replace(".", "_") + "/" + token)

    def _subscribe_response(self, request_path):
        outcome = {}

        def on_response(_conn, _sender, _path, _iface, _member, parameters):
            code, results = parameters.unpack()
            outcome["code"] = code
            outcome["results"] = results

        sub = self._conn.signal_subscribe(
            PORTAL_BUS, IFACE_REQUEST, "Response", request_path, None, 0,
            on_response)
        return outcome, sub

    def _wait_response(self, outcome, sub, timeout, what):
        from gi.repository import GLib
        try:
            ctx = GLib.MainContext.default()
            deadline = time.time() + timeout
            while "code" not in outcome and time.time() < deadline:
                # Non-blocking pumps + sleep: iteration(True) can
                # block past the deadline when the peer stays silent.
                while ctx.iteration(False):
                    pass
                time.sleep(0.05)
            if "code" not in outcome:
                raise PortalError(
                    f"timed out after {timeout}s waiting for the "
                    f"portal response to {what} "
                    "(consent dialog unanswered?)")
        finally:
            self._conn.signal_unsubscribe(sub)
        return outcome["code"], outcome.get("results", {})

    def request(self, interface, method, signature, params, timeout=60):
        token = "deskdora" + uuid.uuid4().hex[:12]
        params = tuple(params)
        options = dict(params[-1]) if params and isinstance(params[-1], dict) else {}
        options["handle_token"] = token
        params = params[:-1] + (options,)
        # Subscribe BEFORE the call: the Response can legally arrive
        # before a subscribe-after-call would be installed.
        outcome, sub = self._subscribe_response(self._request_path(token))
        variant = _params(signature, params)
        res = self._conn.call_sync(PORTAL_BUS, PORTAL_PATH, interface,
                                   method, variant, None, 0, 30000, None)
        _returned = res.unpack()[0]
        return self._wait_response(outcome, sub, timeout,
                                   f"{interface}.{method}")

    def await_request(self, request_path, timeout=60):
        outcome, sub = self._subscribe_response(request_path)
        return self._wait_response(outcome, sub, timeout, request_path)

    def create_session(self, interface, timeout=30):
        token = "deskdora" + uuid.uuid4().hex[:12]
        options = {
            "handle_token": token,
            "session_handle_token": "deskdora" + uuid.uuid4().hex[:12],
        }
        outcome, sub = self._subscribe_response(self._request_path(token))
        (returned,) = self.call(interface, "CreateSession", "(a{sv})",
                                (options,))
        if "/request/" not in returned:
            self._conn.signal_unsubscribe(sub)
            return returned
        code, results = self._wait_response(outcome, sub, timeout,
                                            f"{interface}.CreateSession")
        if code != 0:
            raise CapabilityError(
                f"{interface} session refused (response code {code})")
        handle = results.get("session_handle")
        if not handle:
            raise PortalError(
                f"{interface} session response had no handle: {results!r}")
        return handle


def _params(signature, params):
    from gi.repository import GLib
    converted = []
    for p in params:
        if isinstance(p, dict):
            converted.append({k: _variant(v) for k, v in p.items()})
        else:
            converted.append(p)
    return GLib.Variant(signature, tuple(converted))


class LinuxWaylandDriver(DesktopDriver):
    id = "linux-wayland"
    display_name = "Linux (Wayland / AT-SPI + xdg-desktop-portal)"
    implemented = frozenset({
        "observe", "refind", "click", "double_click", "type_text",
        "key", "scroll", "launch", "screenshot", "read_clipboard",
    })
    verified_on_hardware = False
    hardware_note = ("Implemented against xdg-desktop-portal; the level "
                     "achieved depends on the compositor's portal "
                     "backend and is reported by probe(), never assumed.")

    def __init__(self, bus=None):
        self._bus = bus
        self._session = None          # RemoteDesktop session handle
        self._devices = 0             # granted device bitmask
        self._streams = []            # PipeWire stream node ids
        self._cast = None             # (session, node_id, fd) for capture
        self._probe_cache = None
        self.last_screenshot_via = None

    # -- transport -------------------------------------------------------
    def bus(self):
        if self._bus is None:
            self._bus = GioPortalBus()
        return self._bus

    def _create_session(self, interface):
        """Create a portal session (direct handle on older portals,
        Request-mediated on 1.18 — the bus handles both)."""
        return self.bus().create_session(interface)

    # -- perception (AT-SPI works the same under Wayland) ------------------
    def observe(self):
        return perception.observe()

    def refind(self, element):
        return perception.refind(element)

    # -- launch / clipboard -------------------------------------------------
    def launch(self, command):
        actions.launch(command, wait=0)
        return command

    def read_clipboard(self):
        if not shutil.which("wl-paste"):
            raise CapabilityError(
                "wl-paste (wl-clipboard) is not installed; the Wayland "
                "driver has no other clipboard channel")
        res = subprocess.run(["wl-paste", "--no-newline"],
                             capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            raise DriverError(f"wl-paste failed: {res.stderr.strip()}")
        return res.stdout

    # -- screenshot: Screenshot portal, ScreenCast fallback ------------------
    def screenshot(self, path):
        try:
            return self._screenshot_portal(path)
        except Exception as exc:
            # The Screenshot interface is missing entirely when the
            # running backend only implements impl Screenshot v1
            # (xdg-desktop-portal >= 1.18 requires v2 — measured with
            # xdg-desktop-portal-wlr 0.7.1).  The ScreenCast portal is
            # the other sanctioned capture channel; fall back to it.
            if "UnknownMethod" not in str(exc) \
                    and "No such interface" not in str(exc):
                raise
        return self._screenshot_screencast(path)

    def _screenshot_portal(self, path):
        code, results = self.bus().request(
            IFACE_SCREENSHOT, "Screenshot", "(sa{sv})", ("", {}),
            timeout=30)
        if code != 0:
            raise CapabilityError(
                f"screenshot portal refused (response code {code})")
        uri = results.get("uri", "")
        if not uri.startswith("file://"):
            raise DriverError(f"screenshot portal returned no file uri: {results!r}")
        src = urllib.request.url2pathname(urllib.parse.urlparse(uri).path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        shutil.copyfile(src, path)
        self.last_screenshot_via = "Screenshot portal"
        return path

    def _ensure_cast(self):
        if self._cast is not None:
            return self._cast
        bus = self.bus()
        session = self._create_session(IFACE_CAST)
        code, _ = bus.request(IFACE_CAST, "SelectSources", "(oa{sv})",
                              (session, {"types": 1,        # MONITOR
                                         "multiple": False,
                                         "cursor_mode": 1}),  # embedded
                              timeout=60)
        if code != 0:
            raise CapabilityError(
                f"ScreenCast source selection refused (code {code})")
        code, results = bus.request(IFACE_CAST, "Start", "(osa{sv})",
                                    (session, "", {}), timeout=60)
        if code != 0:
            raise CapabilityError(
                f"ScreenCast session refused at Start (code {code})")
        streams = results.get("streams", [])
        if not streams:
            raise CapabilityError("ScreenCast granted no streams")
        node = streams[0][0]
        (_idx, fd) = bus.call_fd(IFACE_CAST, "OpenPipeWireRemote",
                                 "(oa{sv})", (session, {}))
        self._cast = (session, node, fd)
        return self._cast

    def _screenshot_screencast(self, path):
        _session, node, fd = self._ensure_cast()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except Exception as exc:
            raise CapabilityError(
                f"ScreenCast capture needs GStreamer (gir1.2-gstreamer): "
                f"{exc}")
        Gst.init(None)
        desc = (f"pipewiresrc fd={fd} path={node} num-buffers=1 "
                f"! videoconvert ! pngenc ! filesink location={path}")
        pipe = Gst.parse_launch(desc)
        pipe.set_state(Gst.State.PLAYING)
        try:
            msg = pipe.get_bus().timed_pop_filtered(
                15 * Gst.SECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS)
        finally:
            pipe.set_state(Gst.State.NULL)
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            raise DriverError(f"ScreenCast frame capture failed: {err}")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise DriverError("ScreenCast capture produced no frame")
        self.last_screenshot_via = "ScreenCast portal + PipeWire"
        return path

    # -- RemoteDesktop input session ------------------------------------------
    def _ensure_input_session(self):
        if self._session is not None:
            return
        bus = self.bus()
        session_handle = self._create_session(IFACE_REMOTE)
        bus.call(IFACE_REMOTE, "SelectDevices", "(oa{sv})",
                 (session_handle, {
                     "types": 3,          # KEYBOARD | POINTER
                     "persist_mode": 0,   # do not persist
                 }))
        code, results = bus.request(IFACE_REMOTE, "Start", "(osa{sv})",
                                    (session_handle, "", {}), timeout=120)
        if code != 0:
            raise CapabilityError(
                f"RemoteDesktop session refused at Start "
                f"(response code {code}) — input consent not granted")
        self._session = session_handle
        self._devices = int(results.get("devices", 0))
        self._streams = [s[0] for s in results.get("streams", [])]
        if not self._devices:
            raise CapabilityError(
                "RemoteDesktop session started but granted no devices")

    def _notify(self, method, signature, *args):
        self._ensure_input_session()
        self.bus().call(IFACE_REMOTE, method, signature,
                        (self._session, {}) + args)

    def _move(self, x, y):
        self._ensure_input_session()
        if not self._streams:
            raise CapabilityError(
                "RemoteDesktop granted no screen stream, so absolute "
                "pointer motion is impossible with this portal backend")
        self._notify("NotifyPointerMotionAbsolute", "(oa{sv}udd)",
                     self._streams[0], float(x), float(y))

    def click(self, x, y, button=BTN_LEFT):
        self._move(x, y)
        self._notify("NotifyPointerButton", "(oa{sv}iu)", button, 1)
        self._notify("NotifyPointerButton", "(oa{sv}iu)", button, 0)

    def double_click(self, x, y):
        self.click(x, y)
        self.click(x, y)

    def scroll(self, x, y, direction="down"):
        self._move(x, y)
        dy = 3.0 if direction == "down" else -3.0
        self._notify("NotifyPointerAxis", "(oa{sv}dd)", 0.0, dy)

    def _key_event(self, keycode, pressed):
        self._notify("NotifyKeyboardKeycode", "(oa{sv}uu)",
                     keycode, 1 if pressed else 0)

    def key(self, combo):
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        codes = []
        for part in parts:
            code = KEYCODES.get(part)
            if code is None:
                raise CapabilityError(
                    f"no evdev keycode mapping for key {part!r} "
                    f"(combo {combo!r})")
            codes.append(code)
        for code in codes:
            self._key_event(code, True)
        for code in reversed(codes):
            self._key_event(code, False)

    def type_text(self, text):
        for ch in text:
            entry = _TYPEABLE.get(ch)
            if entry is None:
                raise CapabilityError(
                    f"character {ch!r} cannot be typed via portal "
                    "keycodes (no Unicode text injection in the "
                    "RemoteDesktop portal)")
            code, shift = entry
            if shift:
                self._key_event(KEYCODES["shift"], True)
            self._key_event(code, True)
            self._key_event(code, False)
            if shift:
                self._key_event(KEYCODES["shift"], False)

    # -- focus: no Wayland API exists; say so --------------------------------
    def active_window_title(self):
        return ""

    def activate_window(self, title):
        return False

    # -- probe -----------------------------------------------------------------
    def probe(self):
        """Measure what this session actually allows.  Returns a dict
        of verbatim results; callers must report it as-is."""
        if self._probe_cache is not None:
            return self._probe_cache
        out = {
            "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
            "wayland_display": bool(os.environ.get("WAYLAND_DISPLAY")),
            "current_desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
            "portal_name_owned": False,
            "atspi_elements": None,
            "screenshot": "not attempted",
            "remote_desktop_devices": None,
            "errors": [],
        }
        try:
            out["portal_name_owned"] = self.bus().name_has_owner(PORTAL_BUS)
        except Exception as exc:
            out["errors"].append(f"portal bus: {exc}")
        try:
            obs = perception.observe()
            out["atspi_elements"] = len(obs.elements)
        except Exception as exc:
            out["errors"].append(f"atspi: {exc}")
        if out["portal_name_owned"]:
            try:
                import tempfile
                path = os.path.join(tempfile.mkdtemp(), "probe.png")
                self.screenshot(path)
                out["screenshot"] = (
                    f"ok via {self.last_screenshot_via} "
                    f"({os.path.getsize(path)} bytes)")
            except Exception as exc:
                out["screenshot"] = f"failed: {exc}"
            try:
                self._ensure_input_session()
                out["remote_desktop_devices"] = self._devices
            except Exception as exc:
                out["errors"].append(f"remote_desktop: {exc}")
        self._probe_cache = out
        return out

    def capabilities(self):
        caps = super().capabilities()
        caps["runtime"] = self._probe_cache or "not probed in this process"
        return caps
