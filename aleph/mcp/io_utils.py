"""File IO and format detection helpers for MCP local server."""

from __future__ import annotations

import bz2
import gzip
import importlib
import io
import json
import lzma
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import xml.etree.ElementTree as ET

from ..types import ContentFormat


def _detect_format(text: str) -> ContentFormat:
    """Detect content format from text."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        try:
            json.loads(text)
            return ContentFormat.JSON
        except Exception:
            return ContentFormat.TEXT
    return ContentFormat.TEXT


def _detect_format_for_suffix(text: str, suffix: str) -> ContentFormat:
    ext = suffix.lower()
    if ext in {".jsonl", ".ndjson"}:
        return ContentFormat.JSONL
    if ext == ".csv":
        return ContentFormat.CSV
    if ext == ".json":
        return ContentFormat.JSON if _detect_format(text) == ContentFormat.JSON else ContentFormat.TEXT
    if ext in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".cs",
        ".c", ".h", ".cpp", ".hpp",
    }:
        return ContentFormat.CODE
    return _detect_format(text)


def _effective_suffix(path: Path) -> str:
    suffixes = [s.lower() for s in path.suffixes]
    if suffixes and suffixes[-1] in {".gz", ".bz2", ".xz"}:
        return suffixes[-2] if len(suffixes) > 1 else ""
    return path.suffix.lower()


def _decompress_bytes(path: Path, data: bytes) -> tuple[bytes, str | None]:
    ext = path.suffix.lower()
    if ext == ".gz":
        return gzip.decompress(data), "gzip"
    if ext == ".bz2":
        return bz2.decompress(data), "bzip2"
    if ext == ".xz":
        return lzma.decompress(data), "xz"
    return data, None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        stripped = data.strip()
        if stripped:
            self._chunks.append(stripped)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _extract_text_from_html(text: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(text)
    return parser.text()


def _extract_text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for para in root.iter():
        if not para.tag.endswith("}p"):
            continue
        parts: list[str] = []
        for node in para.iter():
            if node.tag.endswith("}t") and node.text:
                parts.append(node.text)
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _extract_text_from_pdf(
    data: bytes,
    path: Path | None,
    timeout_seconds: float,
) -> tuple[str, str | None]:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = importlib.import_module(module_name)
            reader = module.PdfReader(io.BytesIO(data))
            pages: list[str] = []
            for page in reader.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    pages.append(page_text)
            text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception:
            continue

    if path is not None:
        pdf_tool = shutil.which("pdftotext")
        if pdf_tool:
            try:
                result = subprocess.run(
                    [pdf_tool, "-layout", str(path), "-"],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except Exception as e:
                return "", f"pdftotext failed: {e}"
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout, None
            stderr = result.stderr.strip()
            if stderr:
                return "", f"pdftotext error: {stderr}"

    return "", "PDF extraction unavailable. Install `pypdf` or `pdftotext` for best results."


def _load_text_from_path(
    path: Path,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[str, ContentFormat, str | None]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"File too large to read (>{max_bytes} bytes): {path}")

    data, compression = _decompress_bytes(path, data)
    if compression and len(data) > max_bytes:
        raise ValueError(f"Decompressed file too large (>{max_bytes} bytes): {path}")

    suffix = _effective_suffix(path)
    warning: str | None = None

    if suffix == ".pdf":
        text, warning = _extract_text_from_pdf(data, path, timeout_seconds)
        if not text.strip():
            raise ValueError(warning or "Failed to extract PDF text")
    elif suffix == ".docx":
        try:
            text = _extract_text_from_docx(data)
        except Exception as e:
            raise ValueError(f"Failed to extract DOCX text: {e}") from e
        if not text.strip():
            warning = "DOCX extraction produced empty text"
    elif suffix in {".html", ".htm"}:
        text = _extract_text_from_html(data.decode("utf-8", errors="replace"))
    else:
        text = data.decode("utf-8", errors="replace")

    fmt = _detect_format_for_suffix(text, suffix)
    return text, fmt, warning
