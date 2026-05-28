"""Tests for :mod:`app.history`.

Covers the in-memory provider, the structural Protocol check, and the
``get_history_provider`` factory's wiring decisions. The Cosmos branch
is exercised with a mocked builder — we do not stand up an emulator
here; the real Cosmos integration is covered by the end-to-end smoke
test that lands together with the FastAPI wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_framework import Message

from app.history import (
    HistoryProvider,
    InMemoryHistoryProvider,
    get_history_provider,
)
from app.settings import Settings


def _msg(role: str, text: str) -> Message:
    return Message(role=role, contents=[text])


# --------------------------------------------------------------------------
# InMemoryHistoryProvider
# --------------------------------------------------------------------------


async def test_in_memory_round_trip() -> None:
    provider = InMemoryHistoryProvider()

    await provider.save_messages(
        "s1", [_msg("user", "hi"), _msg("assistant", "hello")]
    )
    messages = await provider.get_messages("s1")

    assert [m.text for m in messages] == ["hi", "hello"]


async def test_in_memory_get_messages_unknown_session_returns_empty() -> None:
    provider = InMemoryHistoryProvider()

    assert await provider.get_messages("nope") == []


async def test_in_memory_get_messages_none_session_returns_empty() -> None:
    provider = InMemoryHistoryProvider()

    assert await provider.get_messages(None) == []


async def test_in_memory_save_messages_none_session_is_noop() -> None:
    provider = InMemoryHistoryProvider()

    # Should not raise even though the session key is missing.
    await provider.save_messages(None, [_msg("user", "x")])

    assert await provider.list_sessions() == []


async def test_in_memory_appends_across_calls() -> None:
    provider = InMemoryHistoryProvider()

    await provider.save_messages("s1", [_msg("user", "1")])
    await provider.save_messages("s1", [_msg("assistant", "2")])

    assert [m.text for m in await provider.get_messages("s1")] == ["1", "2"]


async def test_in_memory_get_messages_returns_defensive_copy() -> None:
    provider = InMemoryHistoryProvider()
    await provider.save_messages("s1", [_msg("user", "1")])

    snapshot = await provider.get_messages("s1")
    snapshot.append(_msg("user", "leak"))

    # The mutation must not bleed back into provider state.
    assert [m.text for m in await provider.get_messages("s1")] == ["1"]


async def test_in_memory_clear_drops_session() -> None:
    provider = InMemoryHistoryProvider()
    await provider.save_messages("s1", [_msg("user", "1")])

    await provider.clear("s1")

    assert await provider.get_messages("s1") == []


async def test_in_memory_clear_none_drops_everything() -> None:
    provider = InMemoryHistoryProvider()
    await provider.save_messages("s1", [_msg("user", "1")])
    await provider.save_messages("s2", [_msg("user", "2")])

    await provider.clear(None)

    assert await provider.list_sessions() == []


async def test_in_memory_close_is_idempotent() -> None:
    provider = InMemoryHistoryProvider()
    await provider.close()
    await provider.close()  # second close must also be a no-op


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


def test_in_memory_satisfies_history_provider_protocol() -> None:
    assert isinstance(InMemoryHistoryProvider(), HistoryProvider)


def test_random_object_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), HistoryProvider)


# --------------------------------------------------------------------------
# Factory wiring
# --------------------------------------------------------------------------


def test_factory_returns_in_memory_when_in_memory_true() -> None:
    settings = Settings()

    provider = get_history_provider(settings, in_memory=True)

    assert isinstance(provider, InMemoryHistoryProvider)


def test_factory_returns_in_memory_when_no_credential_no_flag(caplog: Any) -> None:
    settings = Settings()

    with caplog.at_level("INFO", logger="app.history.factory"):
        provider = get_history_provider(settings)

    assert isinstance(provider, InMemoryHistoryProvider)
    assert any(
        "falling back to in-memory provider" in rec.message
        for rec in caplog.records
    )


def test_factory_requires_credential_when_in_memory_false() -> None:
    settings = Settings()

    with pytest.raises(ValueError, match="async credential"):
        get_history_provider(settings, in_memory=False)


def test_factory_builds_cosmos_when_credential_supplied() -> None:
    settings = Settings()
    credential = MagicMock()
    fake_provider = MagicMock()

    with patch(
        "app.history.cosmos.build_cosmos_history_provider",
        return_value=fake_provider,
    ) as mock_build:
        provider = get_history_provider(settings, credential=credential)

    assert provider is fake_provider
    mock_build.assert_called_once_with(settings, credential)


def test_factory_forwards_cosmos_overrides() -> None:
    settings = Settings()
    credential = MagicMock()

    with patch(
        "app.history.cosmos.build_cosmos_history_provider",
        return_value=MagicMock(),
    ) as mock_build:
        get_history_provider(
            settings, credential=credential, load_messages=True
        )

    mock_build.assert_called_once_with(settings, credential, load_messages=True)


# --------------------------------------------------------------------------
# Cosmos wrapper (no network — patches the SDK)
# --------------------------------------------------------------------------


def test_build_cosmos_history_provider_applies_workflow_defaults() -> None:
    """The wrapper pins load/store flags to ``False`` so the workflow
    drives persistence explicitly (PLAN.md D11)."""
    from app.history.cosmos import build_cosmos_history_provider

    settings = Settings()
    credential = MagicMock()

    with patch(
        "agent_framework_azure_cosmos.CosmosHistoryProvider"
    ) as mock_cls:
        build_cosmos_history_provider(settings, credential)

    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["endpoint"] == settings.azure_cosmos_endpoint
    assert kwargs["database_name"] == settings.azure_cosmos_database_name
    assert kwargs["container_name"] == settings.azure_cosmos_container_name
    assert kwargs["credential"] is credential
    assert kwargs["load_messages"] is False
    assert kwargs["store_inputs"] is False
    assert kwargs["store_outputs"] is False


def test_build_cosmos_history_provider_overrides_win() -> None:
    from app.history.cosmos import build_cosmos_history_provider

    settings = Settings()
    credential = MagicMock()

    with patch(
        "agent_framework_azure_cosmos.CosmosHistoryProvider"
    ) as mock_cls:
        build_cosmos_history_provider(
            settings, credential, load_messages=True, store_outputs=True
        )

    kwargs = mock_cls.call_args.kwargs
    assert kwargs["load_messages"] is True
    assert kwargs["store_outputs"] is True
