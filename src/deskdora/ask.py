"""Ask about my screen (v3.3): read-only Q&A over the desktop.

The user asks about the currently open windows; the answer comes
from the accessibility tree's text.  With an LLM planner the
question + a text digest of the screen go to that provider and
the reply is labelled with the provider/model that produced it.
In built-in mode the answer is a simple extractive lookup over
on-screen text — and says exactly that.  Nothing is sent anywhere
in built-in mode.  No actions are ever taken: this module only
observes.
"""
from __future__ import annotations

import re

_STOPWORDS = {
    "what", "is", "the", "on", "my", "screen", "show", "me", "tell",
    "about", "open", "window", "windows", "do", "does", "are",
    "there", "any", "a", "an", "in", "of", "to", "can", "you", "see",
    "currently", "right", "now", "which", "have", "has", "it",
    "this", "that", "please",
}

BUILTIN_NOTE = "Built-in planner — simple on-screen text lookup only."


def build_digest(observation, limit=6000):
    """A compact text digest of what's on screen: windows, and the
    readable text of their elements grouped by window."""
    lines = []
    if observation.windows:
        lines.append("Open windows: " + "; ".join(observation.windows))
    by_window = {}
    for el in observation.elements:
        text = (el.name or el.label or "").strip()
        if text:
            by_window.setdefault(el.window or "(no window)",
                                 []).append(text)
    for window, texts in by_window.items():
        unique = list(dict.fromkeys(texts))
        lines.append(f"\n[{window}]")
        lines.append(" · ".join(unique[:60]))
    digest = "\n".join(lines)
    return digest[:limit]


def _keywords(question):
    words = re.findall(r"[a-z0-9']+", (question or "").lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _extractive(question, observation):
    keys = _keywords(question)
    if not keys:
        wins = observation.windows or []
        if wins:
            return ("Open windows: " + "; ".join(wins) + ".\n"
                    + BUILTIN_NOTE)
        return ("No open windows are visible in the accessibility "
                "tree.\n" + BUILTIN_NOTE)
    hits = []
    for el in observation.elements:
        text = (el.name or el.label or "").strip()
        if text and any(k in text.lower() for k in keys):
            hits.append(f"• [{el.window or '?'} · {el.app or '?'}] "
                        f"{text}")
    hits = list(dict.fromkeys(hits))[:10]
    if not hits:
        return (f"Nothing on screen mentions "
                f"{', '.join(repr(k) for k in keys)}.\n"
                + BUILTIN_NOTE)
    return ("On screen:\n" + "\n".join(hits) + "\n" + BUILTIN_NOTE)


def answer_question(question, observation, provider_display,
                    completer=None):
    """Answer from the observation.  ``completer`` (optional) is a
    callable(system, user) -> str from the active LLM planner;
    when it exists and succeeds, its answer is returned labelled
    with the provider.  Otherwise the built-in extractive lookup
    answers — labelled as such."""
    if completer is not None:
        digest = build_digest(observation)
        try:
            text = completer(
                "You answer questions about the user's screen from "
                "the accessibility-tree digest provided.  Answer "
                "only from the digest; if it doesn't say, say so.  "
                "Be brief.",
                f"Screen digest:\n{digest}\n\nQuestion: {question}")
            if text and text.strip():
                return {"answer": text.strip(),
                        "provider": provider_display,
                        "mode": "llm"}
        except Exception:
            pass  # fall through to the honest local lookup
    return {"answer": _extractive(question, observation),
            "provider": provider_display, "mode": "builtin"}
