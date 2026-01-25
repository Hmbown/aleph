"""Tests for message trimming functionality."""

from __future__ import annotations

from unittest.mock import MagicMock

from aleph.core import Aleph


class TestMessageTrimming:
    """Tests that message trimming preserves system prompts."""

    def _make_mock_aleph(self, context_limit: int = 1000, output_limit: int = 500) -> Aleph:
        """Create a mock Aleph instance with configurable limits."""
        aleph = Aleph.__new__(Aleph)
        aleph.provider = MagicMock()
        aleph.provider.get_context_limit.return_value = context_limit
        aleph.provider.get_output_limit.return_value = output_limit
        # Simple token counting: ~4 chars per token
        aleph.provider.count_tokens.side_effect = lambda text, model: len(text) // 4
        return aleph

    def test_trim_keeps_system(self) -> None:
        """System message should always be preserved."""
        aleph = self._make_mock_aleph(context_limit=100, output_limit=50)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User 1"},
            {"role": "assistant", "content": "Asst 1"},
            {"role": "user", "content": "User 2"},
        ]

        aleph._trim_messages(messages, model="test")

        # System message should be first
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "System prompt"

    def test_trim_removes_middle_messages(self) -> None:
        """Middle messages should be removed when over limit."""
        # With ~4 chars per token and context_limit=100, output_limit=50,
        # target = max(1000, 100-50) = 1000 tokens (~4000 chars)
        # But we need to make messages large enough to exceed this
        aleph = self._make_mock_aleph(context_limit=500, output_limit=100)

        # Create a lot of large messages to exceed the limit
        messages = [{"role": "system", "content": "System prompt " * 50}]  # ~600 chars = 150 tokens
        for i in range(20):
            messages.append({"role": "user", "content": f"User message {i} " * 50})
            messages.append({"role": "assistant", "content": f"Assistant response {i} " * 50})

        original_len = len(messages)
        aleph._trim_messages(messages, model="test")

        # Should have trimmed some messages
        assert len(messages) < original_len
        # System should still be first
        assert messages[0]["role"] == "system"

    def test_no_trim_when_under_limit(self) -> None:
        """Messages should not be trimmed when under limit."""
        aleph = self._make_mock_aleph(context_limit=10000, output_limit=500)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User 1"},
            {"role": "assistant", "content": "Asst 1"},
        ]

        original = [m.copy() for m in messages]
        aleph._trim_messages(messages, model="test")

        # Should be unchanged
        assert len(messages) == len(original)
        for orig, trimmed in zip(original, messages):
            assert orig == trimmed

    def test_trim_keeps_recent_messages(self) -> None:
        """Recent messages should be preserved over older ones."""
        aleph = self._make_mock_aleph(context_limit=300, output_limit=50)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old user message 1"},
            {"role": "assistant", "content": "Old assistant response 1"},
            {"role": "user", "content": "Old user message 2"},
            {"role": "assistant", "content": "Old assistant response 2"},
            {"role": "user", "content": "Recent user message"},
            {"role": "assistant", "content": "Recent assistant response"},
            {"role": "user", "content": "Most recent message"},
        ]

        aleph._trim_messages(messages, model="test")

        # Most recent message should be preserved
        assert any("Most recent" in m["content"] for m in messages)
        # System should be first
        assert messages[0]["role"] == "system"

    def test_trim_minimum_messages(self) -> None:
        """Trimming should preserve at least system + one user message."""
        aleph = self._make_mock_aleph(context_limit=10, output_limit=5)

        messages = [
            {"role": "system", "content": "System prompt that is very long " * 10},
            {"role": "user", "content": "User message"},
        ]

        aleph._trim_messages(messages, model="test")

        # Should have at least 2 messages (system + user)
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
