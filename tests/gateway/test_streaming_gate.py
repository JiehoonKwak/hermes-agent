"""Tests for gateway token-streaming enablement gates."""

from types import SimpleNamespace

from gateway.run import _resolve_gateway_streaming_enabled


def _source(chat_type: str) -> SimpleNamespace:
    return SimpleNamespace(chat_type=chat_type)


def _streaming_config(enabled: bool = True, transport: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, transport=transport)


def test_dm_only_enables_telegram_dm_topic():
    assert (
        _resolve_gateway_streaming_enabled(
            "dm_only", _streaming_config(), _source("dm"), "telegram"
        )
        is True
    )


def test_dm_only_enables_telegram_private_alias():
    assert (
        _resolve_gateway_streaming_enabled(
            "private_only", _streaming_config(), _source("private"), "telegram"
        )
        is True
    )


def test_dm_only_disables_telegram_group_and_forum():
    assert (
        _resolve_gateway_streaming_enabled(
            "dm_only", _streaming_config(), _source("group"), "telegram"
        )
        is False
    )
    assert (
        _resolve_gateway_streaming_enabled(
            "dm_only", _streaming_config(), _source("forum"), "telegram"
        )
        is False
    )


def test_dm_only_respects_transport_off():
    assert (
        _resolve_gateway_streaming_enabled(
            "dm_only",
            _streaming_config(transport="off"),
            _source("dm"),
            "telegram",
        )
        is False
    )


def test_none_follows_global_streaming_config():
    assert (
        _resolve_gateway_streaming_enabled(
            None, _streaming_config(enabled=True, transport="auto"), _source("group"), "telegram"
        )
        is True
    )
    assert (
        _resolve_gateway_streaming_enabled(
            None, _streaming_config(enabled=False, transport="auto"), _source("dm"), "telegram"
        )
        is False
    )


def test_boolean_override_keeps_existing_semantics():
    assert (
        _resolve_gateway_streaming_enabled(
            True, _streaming_config(enabled=False), _source("group"), "telegram"
        )
        is True
    )
    assert (
        _resolve_gateway_streaming_enabled(
            False, _streaming_config(enabled=True), _source("dm"), "telegram"
        )
        is False
    )
