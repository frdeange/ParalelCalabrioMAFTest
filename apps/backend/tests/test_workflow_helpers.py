"""Tests for the pure helpers in :mod:`app.workflow._helpers`.

These cover ``render_template`` (placeholder substitution) and
``windowed_history`` (turn-counting), which are the only non-trivial
algorithms in the workflow package — everything else delegates to an
agent we can't unit-test without a live LLM.
"""

from __future__ import annotations

from agent_framework import Message

from app.workflow._helpers import render_template, windowed_history


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
