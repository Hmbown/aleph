from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aleph.mcp.io_utils import _load_text_from_path
from aleph.types import ContentFormat


def test_load_text_from_path_uses_markitdown_for_docx(tmp_path) -> None:
    path = tmp_path / "sample.docx"
    path.write_bytes(b"placeholder")

    fake_converter = SimpleNamespace(
        convert_stream=lambda stream, file_extension=None, **kwargs: SimpleNamespace(
            text_content="Converted document body",
            markdown="Converted document body",
        )
    )

    with patch("aleph.mcp.io_utils._get_markitdown_converter", return_value=fake_converter):
        text, fmt, warning = _load_text_from_path(path, max_bytes=1024, timeout_seconds=1.0)

    assert text == "Converted document body"
    assert fmt == ContentFormat.TEXT
    assert warning is None


def test_load_text_from_path_requires_markitdown_for_pptx(tmp_path) -> None:
    path = tmp_path / "slides.pptx"
    path.write_bytes(b"placeholder")

    with patch("aleph.mcp.io_utils._get_markitdown_converter", return_value=None):
        with pytest.raises(ValueError, match="markitdown"):
            _load_text_from_path(path, max_bytes=1024, timeout_seconds=1.0)
