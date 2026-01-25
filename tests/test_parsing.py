"""Tests for FINAL/FINAL_VAR and code block parsing."""

from __future__ import annotations

from aleph.core import _FINAL_RE, _FINAL_VAR_RE, _CODE_BLOCK_RE, Aleph


class TestFinalParsing:
    """Tests for FINAL(answer) pattern matching."""

    def test_final_simple(self) -> None:
        text = "FINAL(42)"
        match = _FINAL_RE.search(text)
        assert match is not None
        assert match.group(1) == "42"

    def test_final_with_text(self) -> None:
        text = "The answer is FINAL(hello world)"
        match = _FINAL_RE.search(text)
        assert match is not None
        assert match.group(1) == "hello world"

    def test_final_multiline(self) -> None:
        text = """Here is my answer:
FINAL(The answer is
multiple lines)
Done."""
        match = _FINAL_RE.search(text)
        assert match is not None
        assert "multiple lines" in match.group(1)

    def test_final_no_match(self) -> None:
        text = "No final answer here"
        match = _FINAL_RE.search(text)
        assert match is None

    def test_final_extraction(self) -> None:
        aleph = Aleph.__new__(Aleph)
        assert aleph._extract_final("FINAL(the answer)") == "the answer"
        assert aleph._extract_final("Prefix FINAL(result) suffix") == "result"


class TestFinalVarParsing:
    """Tests for FINAL_VAR(variable_name) pattern matching."""

    def test_final_var_simple(self) -> None:
        text = "FINAL_VAR(result)"
        match = _FINAL_VAR_RE.search(text)
        assert match is not None
        assert match.group(1) == "result"

    def test_final_var_double_quoted(self) -> None:
        text = 'FINAL_VAR("my_var")'
        match = _FINAL_VAR_RE.search(text)
        assert match is not None

    def test_final_var_single_quoted(self) -> None:
        text = "FINAL_VAR('my_var')"
        match = _FINAL_VAR_RE.search(text)
        assert match is not None

    def test_final_var_extraction(self) -> None:
        aleph = Aleph.__new__(Aleph)
        assert aleph._extract_final_var('FINAL_VAR("result")') == "result"
        assert aleph._extract_final_var("FINAL_VAR('result')") == "result"
        assert aleph._extract_final_var("FINAL_VAR(result)") == "result"
        assert aleph._extract_final_var("FINAL_VAR(  spaced  )") == "spaced"

    def test_final_var_no_match(self) -> None:
        text = "FINAL(not a var)"
        match = _FINAL_VAR_RE.search(text)
        assert match is None


class TestCodeBlockParsing:
    """Tests for Python code block extraction."""

    def test_code_block_python(self) -> None:
        text = '''Here is code:
```python
x = 1
print(x)
```
Done.'''
        match = _CODE_BLOCK_RE.search(text)
        assert match is not None
        assert "x = 1" in match.group(1)
        assert "print(x)" in match.group(1)

    def test_code_block_no_language(self) -> None:
        text = '''```
x = 1
```'''
        match = _CODE_BLOCK_RE.search(text)
        assert match is not None
        assert "x = 1" in match.group(1)

    def test_code_block_multiple(self) -> None:
        text = '''First:
```python
a = 1
```
Second:
```python
b = 2
```'''
        match = _CODE_BLOCK_RE.search(text)
        assert match is not None
        # Should match the first block
        assert "a = 1" in match.group(1)

    def test_no_code_block(self) -> None:
        text = "Just plain text without code"
        match = _CODE_BLOCK_RE.search(text)
        assert match is None

    def test_code_block_whitespace(self) -> None:
        text = '''```python

x = 1

```'''
        match = _CODE_BLOCK_RE.search(text)
        assert match is not None
        assert "x = 1" in match.group(1)
