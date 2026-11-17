"""DeskDora's own API key (v3.1): one revocable key that programmatic
clients (scripts, other tools, the MCP server on another host) present
to the HTTP API.

Only a SHA-256 hash of the key is stored, in the per-user data
directory — the full key is shown exactly once, at generation time.
Verification is a constant-time digest comparison.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

KEY_PREFIX = "ddk_"


def _digest(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKeyStore:
    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("sha256"):
                return data
        except Exception:
            pass
        return None

    def generate(self):
        """Create a fresh key (replacing any existing one) and return
        the full key — the only time it is ever visible."""
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        record = {"sha256": _digest(key), "last4": key[-4:],
                  "created_at": time.time()}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(record, fh, indent=2)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return key

    def revoke(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def verify(self, presented):
        record = self._read()
        if not record or not presented:
            return False
        return hmac.compare_digest(_digest(presented.strip()),
                                   record["sha256"])

    def status(self):
        record = self._read()
        if not record:
            return {"configured": False, "last4": "", "created_at": None}
        return {"configured": True, "last4": record.get("last4", ""),
                "created_at": record.get("created_at")}


def presented_key(headers):
    """Extract a presented API key from request headers:
    ``X-Api-Key: <key>`` or ``Authorization: Bearer <key>``."""
    key = headers.get("x-api-key")
    if key:
        return key.strip()
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""
