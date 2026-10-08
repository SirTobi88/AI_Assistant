"""Codex's rate limits from its session logs -> the payload's "cx"."""
import asyncio
import json
import os

import pytest

from bridge import codex_limits as cl

NOW = 1_800_000_000
_REAL_READ = cl.read_codex_limits   # captured before conftest stubs it per test


@pytest.fixture
def real_reader(monkeypatch):
    """conftest stubs the reader out; these tests want the real one."""
    monkeypatch.setattr(cl, "read_codex_limits", _REAL_READ)
    cl._cache.clear()
    return _REAL_READ


def _event(ts, p_used=40.0, p_reset=NOW + 3600, s_used=70.0, s_reset=NOW + 86400,
           plan="plus", primary=True):
    rl = {"limit_id": "codex",
          "primary": {"used_percent": p_used, "window_minutes": 300, "resets_at": p_reset}
          if primary else None,
          "secondary": {"used_percent": s_used, "window_minutes": 10080, "resets_at": s_reset},
          "plan_type": plan}
    return json.dumps({"timestamp": ts, "type": "event_msg",
                       "payload": {"type": "token_count", "info": {}, "rate_limits": rl}})


def _rollout(home, day, name, lines, mtime):
    d = home / "sessions" / "2026" / "10" / day
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-{name}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def test_last_event_of_newest_file_wins(tmp_path, real_reader):
    _rollout(tmp_path, "05", "old", [_event("2026-10-05T10:00:00Z", p_used=90)], NOW - 900)
    _rollout(tmp_path, "06", "new", [
        _event("2026-10-06T10:00:00Z", p_used=10),
        '{"type":"response_item","payload":{}}',
        _event("2026-10-06T11:00:00Z", p_used=49.4),
        '{"type":"event_msg","payload":{"type":"agent_message"}}',
    ], NOW - 60)
    assert real_reader(tmp_path, now=NOW) == {"s": 49, "sr": 60, "w": 70, "wr": 1440, "pl": "plus"}


def test_null_primary_and_broken_lines_are_skipped(tmp_path, real_reader):
    _rollout(tmp_path, "06", "a", [
        _event("2026-10-06T10:00:00Z", p_used=33),
        _event("2026-10-06T11:00:00Z", primary=False),
        '{"timestamp":"2026-10-06T12:00:00Z","payload":{"rate_limits": {broken',
    ], NOW)
    assert real_reader(tmp_path, now=NOW)["s"] == 33


def test_fresh_session_without_turns_falls_back_to_older_file(tmp_path, real_reader):
    _rollout(tmp_path, "06", "busy", [_event("2026-10-06T10:00:00Z", p_used=12)], NOW - 600)
    _rollout(tmp_path, "06", "fresh", ['{"type":"session_meta","payload":{}}'], NOW)
    assert real_reader(tmp_path, now=NOW)["s"] == 12


def test_passed_reset_reads_as_empty_window(tmp_path, real_reader):
    _rollout(tmp_path, "06", "a", [_event("2026-10-06T10:00:00Z", p_used=95,
                                          p_reset=NOW - 1, s_reset=NOW + 61)], NOW)
    got = real_reader(tmp_path, now=NOW)
    assert (got["s"], got["sr"]) == (0, -1)
    assert (got["w"], got["wr"]) == (70, 2)       # 61 s -> rounds up to 2 min


def test_archived_sessions_count(tmp_path, real_reader):
    _rollout(tmp_path, "05", "old", [_event("2026-10-05T10:00:00Z", p_used=5)], NOW - 900)
    arch = tmp_path / "archived_sessions"
    arch.mkdir()
    f = arch / "rollout-x.jsonl"
    f.write_text(_event("2026-10-06T10:00:00Z", p_used=66) + "\n")
    assert real_reader(tmp_path, now=NOW)["s"] == 66


def test_long_file_is_read_from_the_end(tmp_path, real_reader, monkeypatch):
    monkeypatch.setattr(cl, "_CHUNK", 64)     # force many chunk boundaries
    filler = ['{"type":"response_item","payload":{"text":"%s"}}' % ("x" * 50)] * 40
    _rollout(tmp_path, "06", "a", [_event("2026-10-06T09:00:00Z", p_used=1)] + filler
             + [_event("2026-10-06T10:00:00Z", p_used=77)] + filler, NOW)
    assert real_reader(tmp_path, now=NOW)["s"] == 77


def test_no_codex_at_all(tmp_path, real_reader):
    assert real_reader(tmp_path / "missing", now=NOW) is None
    (tmp_path / "sessions").mkdir()
    assert real_reader(tmp_path, now=NOW) is None


def test_codex_home_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert cl.codex_home() == tmp_path
    monkeypatch.delenv("CODEX_HOME")
    assert cl.codex_home().name == ".codex"
