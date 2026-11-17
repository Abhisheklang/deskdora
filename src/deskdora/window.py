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

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    url = argv[0] if argv else DEFAULT_URL
    DeskDoraWindow(url)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
