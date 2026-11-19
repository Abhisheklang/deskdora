"""GGUF model files: header inspection + import naming (v3.7.2).

A user may already have a model file on disk — a ``.gguf``
downloaded from Hugging Face or anywhere else.  DeskDora can
point Ollama at it (``POST /api/create`` with a Modelfile whose
only line is ``FROM <path>``), after which it is a normal local
model.  DeskDora itself NEVER reads or loads the weights: it
reads only the file's header — magic bytes and the declared
metadata — so the user can see the file was verified before the
import, and Ollama does all the loading and running.

GGUF header layout (little-endian throughout):
``magic "GGUF"`` · u32 version · u64 tensor_count · u64
metadata_kv_count · then that many key/value pairs (key: u64
length + bytes; value: u32 type tag + payload).  The reader is
bounded: it stops as soon as the name/architecture keys are
found and never reads past a fixed cap, however large the file.
"""
from __future__ import annotations

import os
import re
import struct

MAGIC = b"GGUF"
MAX_HEADER_BYTES = 8 * 1024 * 1024  # metadata lives at the front

# GGUF metadata value types.
_T_UINT8, _T_INT8, _T_UINT16, _T_INT16, _T_UINT32, _T_INT32, \
    _T_FLOAT32, _T_BOOL, _T_STRING, _T_ARRAY, _T_UINT64, \
    _T_INT64, _T_FLOAT64 = range(13)

_FIXED_SIZES = {_T_UINT8: 1, _T_INT8: 1, _T_UINT16: 2, _T_INT16: 2,
                _T_UINT32: 4, _T_INT32: 4, _T_FLOAT32: 4, _T_BOOL: 1,
                _T_UINT64: 8, _T_INT64: 8, _T_FLOAT64: 8}


class GgufError(ValueError):
    """A plain-words reason the file cannot be imported."""


class _Reader:
    """A bounded cursor over the front of the file."""

    def __init__(self, fh):
        self._fh = fh
        self.read_bytes = 0

    def take(self, n):
        if self.read_bytes + n > MAX_HEADER_BYTES:
            raise GgufError("The file's header is too large to "
                            "inspect safely.")
        data = self._fh.read(n)
        if len(data) != n:
            raise GgufError("The file ends inside its header — "
                            "it looks truncated or corrupt.")
        self.read_bytes += n
        return data

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8))[0]

    def string(self):
        return self.take(self.u64()).decode("utf-8", "replace")


def _skip_value(reader, vtype):
    """Advance past one metadata value; return it only if it is a
    plain string (the only kind the import cares about)."""
    if vtype == _T_STRING:
        return reader.string()
    if vtype == _T_ARRAY:
        elem_type = reader.u32()
        count = reader.u64()
        if elem_type == _T_STRING:
            for _ in range(count):
                reader.string()
        elif elem_type in _FIXED_SIZES:
            reader.take(_FIXED_SIZES[elem_type] * count)
        else:
            raise GgufError("The file's header uses a metadata "
                            "type DeskDora cannot read.")
        return None
    if vtype in _FIXED_SIZES:
        reader.take(_FIXED_SIZES[vtype])
        return None
    raise GgufError("The file's header uses a metadata type "
                    "DeskDora cannot read.")


def read_header(path):
    """Read ONLY the header of the .gguf at ``path``.

    Returns {"name", "architecture", "version", "size_bytes"} —
    name/architecture are the file's own declared metadata, or
    None when the header doesn't declare them.  Raises GgufError
    with a plain-words reason for anything that is not a GGUF
    file.  The weights are never touched: the read stops after
    the metadata block at the front of the file.
    """
    with open(path, "rb") as fh:
        reader = _Reader(fh)
        if reader.take(4) != MAGIC:
            raise GgufError("This file isn't a GGUF model file "
                            "(its first bytes aren't the GGUF "
                            "marker). Only .gguf files work with "
                            "the local engine.")
        version = reader.u32()
        reader.u64()  # tensor count — not needed for the check
        kv_count = reader.u64()
        name = architecture = None
        for _ in range(kv_count):
            key = reader.string()
            value = _skip_value(reader, reader.u32())
            if key == "general.name":
                name = value
            elif key == "general.architecture":
                architecture = value
            if name and architecture:
                break
    return {"name": name, "architecture": architecture,
            "version": version,
            "size_bytes": os.path.getsize(path)}


def check_model_file(path):
    """Validate a user-supplied model file for import.

    Returns the read_header() info dict.  Raises GgufError with
    the plain-words reason (missing file, wrong extension, not
    GGUF) — the messages are written for the UI verbatim.
    """
    path = (path or "").strip()
    if not path:
        raise GgufError("Enter the path to a .gguf model file.")
    if not os.path.isfile(path):
        raise GgufError(f"No file exists at {path}. Check the "
                        "path and try again.")
    if not path.lower().endswith(".gguf"):
        raise GgufError("Only .gguf model files work with the "
                        "local engine — this file's name doesn't "
                        "end in .gguf.")
    try:
        return read_header(path)
    except GgufError:
        raise
    except OSError as exc:
        raise GgufError(f"The file couldn't be read: {exc}")


def model_name_from_filename(path):
    """A valid Ollama model name from a file's name: the stem,
    lowercased, with anything outside [a-z0-9._-] collapsed to
    dashes.  'My Model (Q4).gguf' -> 'my-model-q4'."""
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    name = re.sub(r"[^a-z0-9._-]+", "-", stem.lower())
    name = name.strip("-.")
    return name or "local-model"


def validate_model_name(name):
    """The name the user edited must be a legal Ollama name."""
    name = (name or "").strip()
    if not name:
        raise GgufError("Give the imported model a name.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*(:[a-z0-9._-]+)?",
                        name.lower()):
        raise GgufError("That name won't work as a model name — "
                        "use lowercase letters, numbers, dots, "
                        "dashes or underscores.")
    return name.lower()
