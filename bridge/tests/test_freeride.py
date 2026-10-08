#!/usr/bin/env python3
"""Tests for the daemon's pure free-ride credential handling.

The daemon NEVER refreshes the OAuth token — Claude Code (the token's owner) does
all refreshing; the daemon only reads whatever access token is currently stored and,
when it's dead, signals "No data" to the device. This replaces the old self-refresh
test suites (test_macos_refresh.py / test_windows_refresh.py), which covered machinery
that has been removed.

Run: python -m pytest bridge/tests/test_freeride.py -x -q
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from bridge.claude_usage_daemon import _decode_keychain_blob  # noqa: E402


# --- read path: hex-dumped Keychain recovery (kept from the macOS suite) -----

def test_decode_passthrough_plain_json():
    """A normal JSON blob is never valid hex, so it passes through untouched."""
    blob = '{"claudeAiOauth": {"accessToken": "x"}}'
    assert _decode_keychain_blob(blob) == blob


def test_decode_hex_dump_round_trips():
    """An embedded newline makes `security -w` emit hex; we decode it back."""
    original = '{"claudeAiOauth": {"accessToken": "x"}}\n'
    hex_dump = original.encode("utf-8").hex()  # what `security -w` would print
    assert _decode_keychain_blob(hex_dump) == original


def test_decode_odd_length_is_not_treated_as_hex():
    """Odd-length all-hex-looking strings aren't valid hex; pass through."""
    assert _decode_keychain_blob("abc") == "abc"


# --- regression guard: the daemon must NEVER touch the refresh endpoint ------

def test_daemons_have_no_refresh_path():
    """Pure free-ride contract: neither daemon may carry client-side refresh. Guards
    against re-introducing the rotation race / 429-bucket exhaustion we removed."""
    import bridge.claude_usage_daemon as macos
    forbidden = (
        "refresh_access_token", "_apply_refresh_response", "_note_refresh_failure",
        "_note_poll_success", "_credentials_expiry_seconds",
        "OAUTH_TOKEN_URL", "OAUTH_CLIENT_ID", "_refresh_failures", "_last_refresh_attempt",
    )
    for mod in (macos,):
        present = [s for s in forbidden if hasattr(mod, s)]
        assert not present, f"{mod.__name__} still exposes refresh machinery: {present}"
