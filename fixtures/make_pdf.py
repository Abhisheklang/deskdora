#!/usr/bin/env python3
"""Generate the gate-task PDF: a small invoice-style table.

Writes a minimal, valid PDF by hand (standard Helvetica, uncompressed
content stream) so the fixture has zero build dependencies.  The table
values are the ground truth the spreadsheet task is asserted against.
"""
import sys

ROWS = [
    ("Item", "Qty", "Price"),
    ("Apples", "12", "30"),
    ("Bread", "5", "25"),
    ("Milk", "8", "42"),
    ("Eggs", "30", "6"),
    ("Rice", "10", "55"),
]


def build_pdf(path, rows=None):
    rows = rows or ROWS
    lines = []
    y = 760
    lines.append("BT /F1 18 Tf 56 %d Td (DeskDora Test Invoice) Tj ET" % y)
    y -= 40
    for row in rows:
        text = "%-14s %-8s %s" % row
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append("BT /F1 13 Tf 56 %d Td (%s) Tj ET" % (y, escaped))
        y -= 24
    stream = "\n".join(lines).encode("latin-1")

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    objects.append(b"<< /Length " + str(len(stream)).encode() +
                   b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_at))
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    print(f"wrote {path} ({len(out)} bytes)")


if __name__ == "__main__":
    build_pdf(sys.argv[1] if len(sys.argv) > 1 else "invoice.pdf")
