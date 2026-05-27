"""Shared utilities used by the workflow Executors.

These helpers are intentionally private (underscore-prefixed) to discourage
out-of-package use. They are pure functions plus one module-level
constant; nothing here owns external state.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from agent_framework import AgentResponse, Message

# Sliding window for the conversation passed to the classifier.
# 4 turns ≈ 8 messages (user + assistant pairs). Enough for typical
# coreference cases without ballooning prompt tokens.
HISTORY_TURNS = 4


def render_template(template: str, **vars: object) -> str:
    """Substitute ``{{name}}`` placeholders in *template* with ``str(value)``.

    Deliberately simpler than ``str.format`` so the JSON snippets the
    templates contain (e.g. ``{"key": "value"}``) don't have to be escaped.
    """
    out = template
    for key, value in vars.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def track_usage(tracker: dict[str, Any], step: str, response: AgentResponse) -> None:
    """Aggregate per-step token counts from *response* into *tracker*.

    The tracker is a plain dict ``{step_name: {usage_key: int}}`` so it can
    be serialized for App Insights without converting types.
    """
    usage = getattr(response, "usage_details", None) or {}
    bucket = tracker.setdefault(step, defaultdict(int))
    for key, value in usage.items():
        if isinstance(value, int):
            bucket[key] += value


def extract_structured_text(response: AgentResponse) -> str:
    """Pull the JSON payload out of a structured ``response_format`` reply.

    The Foundry client returns assistant messages whose content may include
    prose around the JSON object. We walk content blocks from the end,
    locate the first ``{``, and let the JSON decoder grab exactly one
    object. Returns ``response.text`` verbatim when no JSON is found, so
    callers always get *something* to feed pydantic.
    """
    decoder = json.JSONDecoder()
    for msg in reversed(getattr(response, "messages", []) or []):
        if getattr(msg, "role", None) != "assistant":
            continue
        for content in reversed(getattr(msg, "contents", None) or []):
            data = content.to_dict() if hasattr(content, "to_dict") else {}
            if data.get("type") != "text":
                continue
            text = (data.get("text") or "").strip()
            idx = text.find("{")
            if idx < 0:
                continue
            try:
                obj, _end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            return json.dumps(obj)
    return response.text


def build_messages(system_text: str, user_text: str) -> list[Message]:
    """Construct the standard 2-message (system + user) prompt."""
    return [
        Message(role="system", contents=[system_text]),
        Message(role="user", contents=[user_text]),
    ]


def windowed_history(history: list[Message], turns: int) -> list[Message]:
    """Return the last *turns* user/assistant pairs of *history*.

    A "turn" here is one user message plus its corresponding assistant
    reply. We keep the very last user message even if ``turns=0`` so the
    classifier always has at least the current question.
    """
    if turns <= 0 or not history:
        # Always keep at least the last user message
        for i in range(len(history) - 1, -1, -1):
            if history[i].role == "user":
                return [history[i]]
        return list(history)

    # Walk back, counting user messages until we've collected ``turns + 1``
    # of them (turns prior + the latest one).
    kept: list[Message] = []
    user_count = 0
    for m in reversed(history):
        kept.append(m)
        if m.role == "user":
            user_count += 1
            if user_count > turns:
                kept.pop()  # we went one too far
                break
    return list(reversed(kept))


__all__ = [
    "HISTORY_TURNS",
    "build_messages",
    "extract_structured_text",
    "render_template",
    "track_usage",
    "windowed_history",
]
