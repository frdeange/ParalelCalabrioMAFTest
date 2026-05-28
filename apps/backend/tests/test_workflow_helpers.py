"""Tests for the pure helpers in :mod:`app.workflow._helpers`.

These cover every public helper in the module — ``render_template``,
``windowed_history``, ``track_usage``, ``extract_structured_text`` and
``build_messages`` — without touching an LLM.
"""

from __future__ import annotations

from collections import defaultdict

from agent_framework import AgentResponse, Message

from app.workflow._helpers import (
    build_messages,
    extract_structured_text,
    render_template,
    track_usage,
    windowed_history,
)


def _msg(role: str, text: str) -> Message:
    return Message(role=role, contents=[text])


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def test_render_template_substitutes_double_curly_placeholders() -> None:
    out = render_template("hello {{name}}, bu={{bu}}", name="Eli", bu=42)
    assert out == "hello Eli, bu=42"


def test_render_template_leaves_unknown_placeholders_intact() -> None:
    out = render_template("a={{a}} b={{b}}", a=1)
    assert out == "a=1 b={{b}}"


def test_render_template_does_not_interpret_single_braces() -> None:
    # JSON-like content with single braces must round-trip untouched.
    template = 'plan = {"sql": "SELECT 1", "buId": {{buId}}}'
    out = render_template(template, buId=7)
    assert out == 'plan = {"sql": "SELECT 1", "buId": 7}'


def test_render_template_with_no_vars_returns_input() -> None:
    assert render_template("nothing to do") == "nothing to do"


# ---------------------------------------------------------------------------
# windowed_history
# ---------------------------------------------------------------------------


def test_windowed_history_empty_returns_empty() -> None:
    assert windowed_history([], turns=4) == []


def test_windowed_history_turns_zero_keeps_only_last_user_message() -> None:
    history = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
    ]
    out = windowed_history(history, turns=0)
    assert [m.text for m in out] == ["u2"]


def test_windowed_history_turns_zero_no_user_returns_full_history() -> None:
    # Edge case: history with only assistant messages -> fall through to copy.
    history = [_msg("assistant", "a1"), _msg("assistant", "a2")]
    out = windowed_history(history, turns=0)
    assert [m.text for m in out] == ["a1", "a2"]


def test_windowed_history_keeps_last_n_user_turns_plus_their_assistants() -> None:
    history = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
        _msg("user", "u3"),
        _msg("assistant", "a3"),
        _msg("user", "u4"),  # latest
    ]
    # turns=2 means: walk back until we've seen ``turns`` user messages
    # (i.e. the latest u4 plus u3); a2 trails along because it precedes
    # u3 in reverse order. The original ``_windowed_history`` rejects the
    # third user message as "one too far" and pops it before breaking.
    out = windowed_history(history, turns=2)
    texts = [m.text for m in out]
    assert texts == ["a2", "u3", "a3", "u4"]


def test_windowed_history_when_history_shorter_than_window_returns_all() -> None:
    history = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
    ]
    out = windowed_history(history, turns=10)
    assert [m.text for m in out] == ["u1", "a1", "u2"]


# ---------------------------------------------------------------------------
# track_usage
# ---------------------------------------------------------------------------


def test_track_usage_aggregates_token_counts_per_step() -> None:
    tracker: dict[str, defaultdict[str, int]] = {}
    r1 = AgentResponse(messages=[_msg("assistant", "x")])
    r1.usage_details = {"input_tokens": 10, "output_tokens": 5}  # type: ignore[assignment]
    r2 = AgentResponse(messages=[_msg("assistant", "y")])
    r2.usage_details = {"input_tokens": 3, "output_tokens": 7}  # type: ignore[assignment]

    track_usage(tracker, "intent", r1)
    track_usage(tracker, "intent", r2)

    assert dict(tracker["intent"]) == {"input_tokens": 13, "output_tokens": 12}


def test_track_usage_ignores_non_int_values() -> None:
    tracker: dict[str, defaultdict[str, int]] = {}
    resp = AgentResponse(messages=[_msg("assistant", "x")])
    resp.usage_details = {  # type: ignore[assignment]
        "input_tokens": 4,
        "model": "gpt-4o",  # str -> must be skipped
        "latency": 0.5,  # float -> must be skipped
    }

    track_usage(tracker, "sql_builder", resp)

    assert dict(tracker["sql_builder"]) == {"input_tokens": 4}


def test_track_usage_with_missing_usage_details_is_noop() -> None:
    tracker: dict[str, defaultdict[str, int]] = {}
    resp = AgentResponse(messages=[_msg("assistant", "x")])
    # ``usage_details`` defaults to None — the helper must not crash.

    track_usage(tracker, "query_executor", resp)

    # Bucket is created on first call (setdefault) but stays empty.
    assert dict(tracker["query_executor"]) == {}


# ---------------------------------------------------------------------------
# extract_structured_text
# ---------------------------------------------------------------------------


def test_extract_structured_text_returns_json_object_when_embedded_in_prose() -> None:
    prose = 'Here is the plan: {"sql": "SELECT 1", "tables_used": []} -- done.'
    resp = AgentResponse(messages=[_msg("assistant", prose)])

    out = extract_structured_text(resp)

    # Returned value must be valid JSON parseable back into the same object.
    import json

    assert json.loads(out) == {"sql": "SELECT 1", "tables_used": []}


def test_extract_structured_text_walks_messages_from_end() -> None:
    # An earlier assistant message with junk JSON must be ignored in favour
    # of the latest one with a parseable object.
    msgs = [
        _msg("assistant", "garbage"),
        _msg("assistant", '{"intent": "data_query"}'),
    ]
    resp = AgentResponse(messages=msgs)

    import json

    assert json.loads(extract_structured_text(resp)) == {"intent": "data_query"}


def test_extract_structured_text_skips_non_assistant_messages() -> None:
    msgs = [
        _msg("user", '{"sneaky": true}'),
        _msg("assistant", '{"ok": 1}'),
    ]
    resp = AgentResponse(messages=msgs)

    import json

    assert json.loads(extract_structured_text(resp)) == {"ok": 1}


def test_extract_structured_text_falls_back_to_text_when_no_json() -> None:
    resp = AgentResponse(messages=[_msg("assistant", "no braces here")])

    # No ``{`` found — caller gets ``response.text`` verbatim.
    assert extract_structured_text(resp) == resp.text


def test_extract_structured_text_falls_back_when_invalid_json() -> None:
    # ``{`` is present but the body never parses; the helper must NOT raise.
    resp = AgentResponse(messages=[_msg("assistant", "prefix { not really json")])

    out = extract_structured_text(resp)
    assert out == resp.text


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


def test_build_messages_returns_system_then_user_pair() -> None:
    msgs = build_messages("you are helpful", "what time is it?")

    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[0].text == "you are helpful"
    assert msgs[1].text == "what time is it?"
