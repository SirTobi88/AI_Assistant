"""Shared fixtures for the daemon tests."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_codex_logs(monkeypatch):
    """Keep the machine's own ~/.codex out of the payloads under test.

    The daemons add Codex's numbers to every write; tests that assert an
    exact payload would otherwise depend on whether Codex ran here lately.
    Tests about Codex itself pass their own CODEX_HOME / patch this back.
    """
    from bridge import codex_limits
    monkeypatch.setattr(codex_limits, "read_codex_limits", lambda *a, **k: None)
