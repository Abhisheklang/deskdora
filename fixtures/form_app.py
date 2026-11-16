#!/usr/bin/env python3
"""DeskDora test fixture: a small GTK form application.

Three labelled fields (Name, Email, City) and a Submit button.  On submit
it writes the entered values as JSON to the output path (argv[1], default
~/deskdora-form-result.json) and shows a confirmation label.  Used by the
gate/benchmark form task; it is a fixture, not part of the product.
"""
import json
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/deskdora-form-result.json")
FIELDS = ["Name", "Email", "City"]


class FormWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="DeskDora Test Form")
        self.set_default_size(360, 240)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12,
                        margin_top=16, margin_bottom=16,
                        margin_start=16, margin_end=16)
        self.entries = {}
        for row, field_name in enumerate(FIELDS):
            label = Gtk.Label(label=field_name, xalign=0)
            entry = Gtk.Entry()
            label.set_mnemonic_widget(entry)  # creates LABELLED_BY relation
            self.entries[field_name] = entry
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
        submit = Gtk.Button(label="Submit")
        submit.connect("clicked", self.on_submit)
        grid.attach(submit, 1, len(FIELDS), 1, 1)
        self.status = Gtk.Label(label="", xalign=0)
        grid.attach(self.status, 0, len(FIELDS) + 1, 2, 1)
        self.add(grid)

    def on_submit(self, _button):
        data = {name.lower(): entry.get_text()
                for name, entry in self.entries.items()}
        with open(OUT, "w") as fh:
            json.dump(data, fh)
        self.status.set_text("Submission saved. Thank you!")


win = FormWindow()
win.connect("destroy", Gtk.main_quit)
win.show_all()
Gtk.main()
