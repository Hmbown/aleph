"""Built-in helper functions exposed inside the Aleph REPL.

These helpers provide powerful text analysis capabilities for any kind of document:
- Code, logs, configs, legal docs, financial reports, research papers, etc.

The REPL injects wrappers so that the LLM can call these directly.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TypedDict, Any, Callable, Sequence, Iterable


# =============================================================================
# Type definitions
# =============================================================================

class SearchResult(TypedDict):
    match: str
    line_num: int
    context: str


class Citation(TypedDict):
    """Manual citation for evidence tracking."""
    snippet: str
    line_range: tuple[int, int] | None
    note: str | None


class ExtractedMatch(TypedDict):
    """Result from extraction functions."""
    value: str
    line_num: int
    start: int
    end: int


# =============================================================================
# Core helpers (original)
# =============================================================================

def _to_text(ctx: object) -> str:
    """Best-effort conversion of context into a string."""
    if ctx is None:
        return ""
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, bytes):
        try:
            return ctx.decode("utf-8", errors="replace")
        except Exception:
            return repr(ctx)
    if isinstance(ctx, (dict, list, tuple)):
        try:
            import json
            return json.dumps(ctx, indent=2, ensure_ascii=False)
        except Exception:
            return str(ctx)
    return str(ctx)


def peek(ctx: object, start: int = 0, end: int | None = None) -> str:
    """Get a character slice of the context."""
    text = _to_text(ctx)
    return text[start:end]


def lines(ctx: object, start: int = 0, end: int | None = None) -> str:
    """Get a line slice of the context."""
    text = _to_text(ctx)
    parts = text.splitlines()
    return "\n".join(parts[start:end])


def search(
    ctx: object,
    pattern: str,
    context_lines: int = 2,
    flags: int = 0,
    max_results: int = 20,
) -> list[SearchResult]:
    """Regex search returning matching lines with surrounding context.

    Returns list of dicts: {"match": str, "line_num": int, "context": str}
    """
    text = _to_text(ctx)
    lines_list = text.splitlines()
    results: list[SearchResult] = []
    rx = re.compile(pattern, flags=flags)

    for i, line in enumerate(lines_list):
        if rx.search(line):
            start = max(0, i - context_lines)
            end = min(len(lines_list), i + context_lines + 1)
            results.append({
                "match": line,
                "line_num": i,
                "context": "\n".join(lines_list[start:end]),
            })
            if len(results) >= max_results:
                break

    return results


def chunk(ctx: object, chunk_size: int, overlap: int = 0) -> list[str]:
    """Split context into chunks by character count."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be < chunk_size")

    text = _to_text(ctx)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = min(n, i + chunk_size)
        out.append(text[i:j])
        if j == n:
            break
        i = j - overlap
    return out


def cite(
    snippet: str,
    line_range: tuple[int, int] | None = None,
    note: str | None = None,
) -> Citation:
    """Manually cite evidence for provenance tracking."""
    return Citation(
        snippet=snippet[:500],
        line_range=line_range,
        note=note,
    )


# =============================================================================
# Extraction helpers - pull structured data from text
# =============================================================================

def _extract_with_pattern(
    ctx: object,
    pattern: str,
    flags: int = 0,
    max_results: int = 100,
) -> list[ExtractedMatch]:
    """Generic extraction helper."""
    text = _to_text(ctx)
    lines_list = text.splitlines()
    results: list[ExtractedMatch] = []
    rx = re.compile(pattern, flags=flags)

    for line_num, line in enumerate(lines_list):
        for m in rx.finditer(line):
            results.append({
                "value": m.group(0),
                "line_num": line_num,
                "start": m.start(),
                "end": m.end(),
            })
            if len(results) >= max_results:
                return results
    return results


def extract_numbers(ctx: object, include_negative: bool = True, include_decimals: bool = True) -> list[ExtractedMatch]:
    """Extract all numbers from text.

    Returns list of {"value": str, "line_num": int, "start": int, "end": int}
    """
    if include_decimals and include_negative:
        pattern = r'-?\d+\.?\d*'
    elif include_decimals:
        pattern = r'\d+\.?\d*'
    elif include_negative:
        pattern = r'-?\d+'
    else:
        pattern = r'\d+'
    return _extract_with_pattern(ctx, pattern)


def extract_money(ctx: object, currencies: str = r'[$€£¥₹]') -> list[ExtractedMatch]:
    """Extract monetary amounts like $1,234.56 or €100."""
    pattern = rf'{currencies}\s*[\d,]+\.?\d*|\d+\.?\d*\s*{currencies}'
    return _extract_with_pattern(ctx, pattern)


def extract_percentages(ctx: object) -> list[ExtractedMatch]:
    """Extract percentages like 45%, 3.14%, -2.5%."""
    pattern = r'-?\d+\.?\d*\s*%'
    return _extract_with_pattern(ctx, pattern)


def extract_dates(ctx: object) -> list[ExtractedMatch]:
    """Extract dates in common formats (YYYY-MM-DD, MM/DD/YYYY, etc.)."""
    patterns = [
        r'\d{4}-\d{2}-\d{2}',  # ISO: 2024-01-15
        r'\d{1,2}/\d{1,2}/\d{2,4}',  # US: 1/15/2024
        r'\d{1,2}-\d{1,2}-\d{2,4}',  # 15-01-2024
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}',  # Jan 15, 2024
        r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}',  # 15 Jan 2024
    ]
    combined = '|'.join(f'({p})' for p in patterns)
    return _extract_with_pattern(ctx, combined, flags=re.IGNORECASE)


def extract_times(ctx: object) -> list[ExtractedMatch]:
    """Extract times like 14:30, 2:30 PM, 14:30:45."""
    pattern = r'\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?'
    return _extract_with_pattern(ctx, pattern)


def extract_timestamps(ctx: object) -> list[ExtractedMatch]:
    """Extract ISO timestamps and common log formats."""
    patterns = [
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?',  # ISO 8601
        r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}',  # Common log format
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}',  # Syslog
    ]
    combined = '|'.join(f'({p})' for p in patterns)
    return _extract_with_pattern(ctx, combined, flags=re.IGNORECASE)


def extract_emails(ctx: object) -> list[ExtractedMatch]:
    """Extract email addresses."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return _extract_with_pattern(ctx, pattern)


def extract_urls(ctx: object) -> list[ExtractedMatch]:
    """Extract URLs (http, https, ftp)."""
    pattern = r'https?://[^\s<>"\']+|ftp://[^\s<>"\']+|www\.[^\s<>"\']+'
    return _extract_with_pattern(ctx, pattern)


def extract_ips(ctx: object, include_ipv6: bool = False) -> list[ExtractedMatch]:
    """Extract IP addresses."""
    ipv4 = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    if include_ipv6:
        ipv6 = r'(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}'
        pattern = f'{ipv4}|{ipv6}'
    else:
        pattern = ipv4
    return _extract_with_pattern(ctx, pattern)


def extract_phones(ctx: object) -> list[ExtractedMatch]:
    """Extract phone numbers in various formats."""
    pattern = r'(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    return _extract_with_pattern(ctx, pattern)


def extract_hex(ctx: object) -> list[ExtractedMatch]:
    """Extract hexadecimal values like 0x1F, #FF5733, etc."""
    pattern = r'0x[0-9a-fA-F]+|#[0-9a-fA-F]{3,8}\b'
    return _extract_with_pattern(ctx, pattern)


def extract_uuids(ctx: object) -> list[ExtractedMatch]:
    """Extract UUIDs."""
    pattern = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
    return _extract_with_pattern(ctx, pattern)


def extract_paths(ctx: object) -> list[ExtractedMatch]:
    """Extract file paths (Unix and Windows)."""
    patterns = [
        r'/(?:[^/\s]+/)*[^/\s]+',  # Unix: /path/to/file
        r'[A-Za-z]:\\(?:[^\\:\s]+\\)*[^\\:\s]+',  # Windows: C:\path\to\file
        r'\.{1,2}/(?:[^/\s]+/)*[^/\s]*',  # Relative: ./path or ../path
    ]
    combined = '|'.join(f'({p})' for p in patterns)
    return _extract_with_pattern(ctx, combined)


def extract_env_vars(ctx: object) -> list[ExtractedMatch]:
    """Extract environment variable references like $VAR, ${VAR}, %VAR%."""
    pattern = r'\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%'
    return _extract_with_pattern(ctx, pattern)


def extract_versions(ctx: object) -> list[ExtractedMatch]:
    """Extract version numbers like v1.2.3, 2.0.0-beta, etc."""
    pattern = r'v?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.]+)?(?:\+[a-zA-Z0-9.]+)?'
    return _extract_with_pattern(ctx, pattern)


def extract_hashes(ctx: object) -> list[ExtractedMatch]:
    """Extract common hash formats (MD5, SHA1, SHA256)."""
    patterns = [
        r'\b[a-fA-F0-9]{32}\b',  # MD5
        r'\b[a-fA-F0-9]{40}\b',  # SHA1
        r'\b[a-fA-F0-9]{64}\b',  # SHA256
    ]
    combined = '|'.join(patterns)
    return _extract_with_pattern(ctx, combined)


# =============================================================================
# Code-specific extraction
# =============================================================================

def extract_functions(ctx: object, lang: str = "python") -> list[ExtractedMatch]:
    """Extract function definitions."""
    patterns = {
        "python": r'(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
        "javascript": r'(?:async\s+)?function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(|(?:const|let|var)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
        "go": r'func\s+(?:\([^)]+\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
        "rust": r'(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        "java": r'(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
    }
    pattern = patterns.get(lang.lower(), patterns["python"])
    return _extract_with_pattern(ctx, pattern)


def extract_classes(ctx: object, lang: str = "python") -> list[ExtractedMatch]:
    """Extract class definitions."""
    patterns = {
        "python": r'class\s+([A-Za-z_][A-Za-z0-9_]*)',
        "javascript": r'class\s+([A-Za-z_$][A-Za-z0-9_$]*)',
        "java": r'(?:public\s+)?(?:abstract\s+)?(?:final\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)',
        "go": r'type\s+([A-Za-z_][A-Za-z0-9_]*)\s+struct',
        "rust": r'(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)',
    }
    pattern = patterns.get(lang.lower(), patterns["python"])
    return _extract_with_pattern(ctx, pattern)


def extract_imports(ctx: object, lang: str = "python") -> list[ExtractedMatch]:
    """Extract import statements."""
    patterns = {
        "python": r'(?:from\s+[\w.]+\s+)?import\s+[\w., ]+',
        "javascript": r'import\s+.*?from\s+[\'"][^\'"]+[\'"]|require\s*\([\'"][^\'"]+[\'"]\)',
        "go": r'import\s+(?:\(\s*(?:"[^"]+"\s*)+\)|"[^"]+")',
        "java": r'import\s+[\w.]+;',
        "rust": r'use\s+[\w:]+;',
    }
    pattern = patterns.get(lang.lower(), patterns["python"])
    return _extract_with_pattern(ctx, pattern)


def extract_comments(ctx: object, lang: str = "python") -> list[ExtractedMatch]:
    """Extract comments."""
    patterns = {
        "python": r'#.*$|\'\'\'[\s\S]*?\'\'\'|"""[\s\S]*?"""',
        "javascript": r'//.*$|/\*[\s\S]*?\*/',
        "go": r'//.*$|/\*[\s\S]*?\*/',
        "java": r'//.*$|/\*[\s\S]*?\*/',
        "rust": r'//.*$|/\*[\s\S]*?\*/',
        "c": r'//.*$|/\*[\s\S]*?\*/',
        "html": r'<!--[\s\S]*?-->',
        "css": r'/\*[\s\S]*?\*/',
    }
    pattern = patterns.get(lang.lower(), patterns["python"])
    return _extract_with_pattern(ctx, pattern, flags=re.MULTILINE)


def extract_strings(ctx: object) -> list[ExtractedMatch]:
    """Extract string literals (single, double, backtick quotes)."""
    pattern = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`'
    return _extract_with_pattern(ctx, pattern)


def extract_todos(ctx: object) -> list[ExtractedMatch]:
    """Extract TODO, FIXME, HACK, XXX comments."""
    pattern = r'(?:TODO|FIXME|HACK|XXX|BUG|NOTE)[\s:]+.*'
    return _extract_with_pattern(ctx, pattern, flags=re.IGNORECASE)


# =============================================================================
# Log-specific extraction
# =============================================================================

def extract_log_levels(ctx: object) -> list[ExtractedMatch]:
    """Extract log levels (ERROR, WARN, INFO, DEBUG, etc.)."""
    pattern = r'\b(?:FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b'
    return _extract_with_pattern(ctx, pattern, flags=re.IGNORECASE)


def extract_exceptions(ctx: object) -> list[ExtractedMatch]:
    """Extract exception/error messages from logs or stack traces."""
    patterns = [
        r'(?:Exception|Error|Traceback).*',
        r'at\s+[\w.$]+\([\w.:]+\)',  # Java stack trace
        r'File ".*", line \d+',  # Python stack trace
    ]
    combined = '|'.join(patterns)
    return _extract_with_pattern(ctx, combined)


def extract_json_objects(ctx: object) -> list[ExtractedMatch]:
    """Extract JSON-like objects {...} from text."""
    # Simple brace matching (doesn't handle nested perfectly but good enough)
    pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    return _extract_with_pattern(ctx, pattern)


# =============================================================================
# Text statistics
# =============================================================================

def word_count(ctx: object) -> int:
    """Count total words in text."""
    text = _to_text(ctx)
    return len(text.split())


def char_count(ctx: object, include_whitespace: bool = True) -> int:
    """Count characters in text."""
    text = _to_text(ctx)
    if include_whitespace:
        return len(text)
    return len(text.replace(" ", "").replace("\n", "").replace("\t", ""))


def line_count(ctx: object) -> int:
    """Count lines in text."""
    text = _to_text(ctx)
    return len(text.splitlines())


def sentence_count(ctx: object) -> int:
    """Estimate sentence count (splits on .!?)."""
    text = _to_text(ctx)
    sentences = re.split(r'[.!?]+', text)
    return len([s for s in sentences if s.strip()])


def paragraph_count(ctx: object) -> int:
    """Count paragraphs (separated by blank lines)."""
    text = _to_text(ctx)
    paragraphs = re.split(r'\n\s*\n', text)
    return len([p for p in paragraphs if p.strip()])


def unique_words(ctx: object, case_insensitive: bool = True) -> list[str]:
    """Get list of unique words."""
    text = _to_text(ctx)
    if case_insensitive:
        text = text.lower()
    words = re.findall(r'\b\w+\b', text)
    return list(dict.fromkeys(words))  # Preserves order


def word_frequency(ctx: object, top_n: int = 20, case_insensitive: bool = True) -> list[tuple[str, int]]:
    """Get word frequency distribution."""
    text = _to_text(ctx)
    if case_insensitive:
        text = text.lower()
    words = re.findall(r'\b\w+\b', text)
    return Counter(words).most_common(top_n)


def ngrams(ctx: object, n: int = 2, top_k: int = 20) -> list[tuple[tuple[str, ...], int]]:
    """Get most common n-grams."""
    text = _to_text(ctx)
    words = re.findall(r'\b\w+\b', text.lower())
    grams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    return Counter(grams).most_common(top_k)


# =============================================================================
# Line operations (grep-like)
# =============================================================================

def head(ctx: object, n: int = 10) -> str:
    """Get first n lines."""
    text = _to_text(ctx)
    return "\n".join(text.splitlines()[:n])


def tail(ctx: object, n: int = 10) -> str:
    """Get last n lines."""
    text = _to_text(ctx)
    return "\n".join(text.splitlines()[-n:])


def grep(ctx: object, pattern: str, flags: int = 0) -> list[str]:
    """Filter lines matching pattern (like grep)."""
    text = _to_text(ctx)
    rx = re.compile(pattern, flags=flags)
    return [line for line in text.splitlines() if rx.search(line)]


def grep_v(ctx: object, pattern: str, flags: int = 0) -> list[str]:
    """Filter lines NOT matching pattern (like grep -v)."""
    text = _to_text(ctx)
    rx = re.compile(pattern, flags=flags)
    return [line for line in text.splitlines() if not rx.search(line)]


def grep_c(ctx: object, pattern: str, flags: int = 0) -> int:
    """Count lines matching pattern (like grep -c)."""
    return len(grep(ctx, pattern, flags))


def uniq(ctx: object) -> list[str]:
    """Remove duplicate consecutive lines (like uniq)."""
    text = _to_text(ctx)
    lines_list = text.splitlines()
    result = []
    prev = None
    for line in lines_list:
        if line != prev:
            result.append(line)
            prev = line
    return result


def sort_lines(ctx: object, reverse: bool = False, numeric: bool = False) -> list[str]:
    """Sort lines alphabetically or numerically."""
    text = _to_text(ctx)
    lines_list = text.splitlines()
    if numeric:
        def key(x: str) -> float:
            nums = re.findall(r'-?\d+\.?\d*', x)
            return float(nums[0]) if nums else 0
        return sorted(lines_list, key=key, reverse=reverse)
    return sorted(lines_list, reverse=reverse)


def number_lines(ctx: object, start: int = 1) -> str:
    """Add line numbers to text."""
    text = _to_text(ctx)
    lines_list = text.splitlines()
    width = len(str(start + len(lines_list)))
    return "\n".join(f"{i:{width}d}: {line}" for i, line in enumerate(lines_list, start))


def strip_lines(ctx: object) -> list[str]:
    """Strip whitespace from each line."""
    text = _to_text(ctx)
    return [line.strip() for line in text.splitlines()]


def blank_lines(ctx: object) -> list[int]:
    """Get indices of blank lines."""
    text = _to_text(ctx)
    return [i for i, line in enumerate(text.splitlines()) if not line.strip()]


def non_blank_lines(ctx: object) -> list[str]:
    """Filter out blank lines."""
    text = _to_text(ctx)
    return [line for line in text.splitlines() if line.strip()]


def columns(ctx: object, col: int, delim: str = r'\s+') -> list[str]:
    """Extract a column from delimited text (0-indexed)."""
    text = _to_text(ctx)
    result = []
    for line in text.splitlines():
        parts = re.split(delim, line)
        if col < len(parts):
            result.append(parts[col])
    return result


# =============================================================================
# Text manipulation
# =============================================================================

def replace_all(ctx: object, pattern: str, replacement: str, flags: int = 0) -> str:
    """Replace all occurrences of pattern."""
    text = _to_text(ctx)
    return re.sub(pattern, replacement, text, flags=flags)


def split_by(ctx: object, pattern: str, flags: int = 0) -> list[str]:
    """Split text by regex pattern."""
    text = _to_text(ctx)
    return re.split(pattern, text, flags=flags)


def between(ctx: object, start_pattern: str, end_pattern: str, include_markers: bool = False) -> list[str]:
    """Extract text between start and end patterns."""
    text = _to_text(ctx)
    if include_markers:
        pattern = f'({start_pattern}.*?{end_pattern})'
    else:
        pattern = f'{start_pattern}(.*?){end_pattern}'
    return re.findall(pattern, text, flags=re.DOTALL)


def before(ctx: object, pattern: str) -> str:
    """Get text before first occurrence of pattern."""
    text = _to_text(ctx)
    match = re.search(pattern, text)
    if match:
        return text[:match.start()]
    return text


def after(ctx: object, pattern: str) -> str:
    """Get text after first occurrence of pattern."""
    text = _to_text(ctx)
    match = re.search(pattern, text)
    if match:
        return text[match.end():]
    return ""


def truncate(ctx: object, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate text to max length with suffix."""
    text = _to_text(ctx)
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def wrap_text(ctx: object, width: int = 80) -> str:
    """Wrap text to specified width."""
    import textwrap
    text = _to_text(ctx)
    return textwrap.fill(text, width=width)


def indent_text(ctx: object, prefix: str = "  ") -> str:
    """Indent all lines with prefix."""
    text = _to_text(ctx)
    return "\n".join(prefix + line for line in text.splitlines())


def dedent_text(ctx: object) -> str:
    """Remove common leading whitespace."""
    import textwrap
    text = _to_text(ctx)
    return textwrap.dedent(text)


def normalize_whitespace(ctx: object) -> str:
    """Normalize whitespace (collapse multiple spaces, trim lines)."""
    text = _to_text(ctx)
    lines_list = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(lines_list)


def remove_punctuation(ctx: object) -> str:
    """Remove all punctuation."""
    text = _to_text(ctx)
    return re.sub(r'[^\w\s]', '', text)


# =============================================================================
# Text comparison
# =============================================================================

def diff(ctx1: object, ctx2: object, context_lines: int = 3) -> str:
    """Get unified diff between two texts."""
    import difflib
    text1 = _to_text(ctx1)
    text2 = _to_text(ctx2)
    lines1 = text1.splitlines(keepends=True)
    lines2 = text2.splitlines(keepends=True)
    return "".join(difflib.unified_diff(lines1, lines2, n=context_lines))


def similarity(ctx1: object, ctx2: object) -> float:
    """Get similarity ratio between two texts (0.0 to 1.0)."""
    import difflib
    text1 = _to_text(ctx1)
    text2 = _to_text(ctx2)
    return difflib.SequenceMatcher(None, text1, text2).ratio()


def common_lines(ctx1: object, ctx2: object) -> list[str]:
    """Get lines common to both texts."""
    text1 = _to_text(ctx1)
    text2 = _to_text(ctx2)
    set1 = set(text1.splitlines())
    set2 = set(text2.splitlines())
    return list(set1 & set2)


def diff_lines(ctx1: object, ctx2: object) -> dict[str, list[str]]:
    """Get lines unique to each text."""
    text1 = _to_text(ctx1)
    text2 = _to_text(ctx2)
    set1 = set(text1.splitlines())
    set2 = set(text2.splitlines())
    return {
        "only_in_first": list(set1 - set2),
        "only_in_second": list(set2 - set1),
    }


# =============================================================================
# Pattern matching helpers
# =============================================================================

def contains(ctx: object, pattern: str, flags: int = 0) -> bool:
    """Check if text contains pattern."""
    text = _to_text(ctx)
    return bool(re.search(pattern, text, flags=flags))


def contains_any(ctx: object, patterns: list[str], flags: int = 0) -> bool:
    """Check if text contains any of the patterns."""
    text = _to_text(ctx)
    return any(re.search(p, text, flags=flags) for p in patterns)


def contains_all(ctx: object, patterns: list[str], flags: int = 0) -> bool:
    """Check if text contains all patterns."""
    text = _to_text(ctx)
    return all(re.search(p, text, flags=flags) for p in patterns)


def count_matches(ctx: object, pattern: str, flags: int = 0) -> int:
    """Count regex matches in text."""
    text = _to_text(ctx)
    return len(re.findall(pattern, text, flags=flags))


def find_all(ctx: object, pattern: str, flags: int = 0) -> list[str]:
    """Find all matches of pattern."""
    text = _to_text(ctx)
    return re.findall(pattern, text, flags=flags)


def first_match(ctx: object, pattern: str, flags: int = 0) -> str | None:
    """Get first match of pattern or None."""
    text = _to_text(ctx)
    match = re.search(pattern, text, flags=flags)
    return match.group(0) if match else None


# =============================================================================
# Collection utilities
# =============================================================================

def dedupe(items: Sequence[Any]) -> list[Any]:
    """Remove duplicates while preserving order."""
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        hashable = item if isinstance(item, (str, int, float, tuple)) else str(item)
        if hashable not in seen:
            seen.add(hashable)
            result.append(item)
    return result


def flatten(nested: Sequence[Any], depth: int = -1) -> list[Any]:
    """Flatten nested lists/tuples. depth=-1 means fully flatten."""
    result: list[Any] = []
    for item in nested:
        if isinstance(item, (list, tuple)) and depth != 0:
            result.extend(flatten(item, depth - 1 if depth > 0 else -1))
        else:
            result.append(item)
    return result


def first(items: Sequence[Any], default: Any = None) -> Any:
    """Get first item or default."""
    return items[0] if items else default


def last(items: Sequence[Any], default: Any = None) -> Any:
    """Get last item or default."""
    return items[-1] if items else default


def take(n: int, items: Sequence[Any]) -> list[Any]:
    """Get first n items."""
    return list(items[:n])


def drop(n: int, items: Sequence[Any]) -> list[Any]:
    """Skip first n items."""
    return list(items[n:])


def partition(items: Sequence[Any], predicate: Callable[[Any], bool]) -> tuple[list[Any], list[Any]]:
    """Split items into (matches, non-matches) based on predicate."""
    matches: list[Any] = []
    non_matches: list[Any] = []
    for item in items:
        if predicate(item):
            matches.append(item)
        else:
            non_matches.append(item)
    return matches, non_matches


def group_by(items: Sequence[Any], key_fn: Callable[[Any], Any]) -> dict[Any, list[Any]]:
    """Group items by key function."""
    result: dict[Any, list[Any]] = {}
    for item in items:
        k = key_fn(item)
        if k not in result:
            result[k] = []
        result[k].append(item)
    return result


def frequency(items: Sequence[Any], top_n: int | None = None) -> list[tuple[Any, int]]:
    """Get frequency distribution of items."""
    counter = Counter(items)
    if top_n:
        return counter.most_common(top_n)
    return counter.most_common()


def sample_items(items: Sequence[Any], n: int, seed: int | None = None) -> list[Any]:
    """Random sample of n items."""
    import random
    if seed is not None:
        random.seed(seed)
    return random.sample(list(items), min(n, len(items)))


def shuffle_items(items: Sequence[Any], seed: int | None = None) -> list[Any]:
    """Shuffle items randomly."""
    import random
    if seed is not None:
        random.seed(seed)
    result = list(items)
    random.shuffle(result)
    return result


# =============================================================================
# Validation helpers
# =============================================================================

def is_numeric(text: str) -> bool:
    """Check if text represents a number."""
    try:
        float(text.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def is_email(text: str) -> bool:
    """Check if text is a valid email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, text.strip()))


def is_url(text: str) -> bool:
    """Check if text is a valid URL format."""
    pattern = r'^https?://[^\s<>"\']+$'
    return bool(re.match(pattern, text.strip()))


def is_ip(text: str) -> bool:
    """Check if text is a valid IPv4 address."""
    parts = text.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def is_uuid(text: str) -> bool:
    """Check if text is a valid UUID format."""
    pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    return bool(re.match(pattern, text.strip()))


def is_json(text: str) -> bool:
    """Check if text is valid JSON."""
    import json
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def is_blank(text: str) -> bool:
    """Check if text is empty or only whitespace."""
    return not text or not text.strip()


# =============================================================================
# Conversion helpers
# =============================================================================

def to_json(obj: Any, indent: int = 2) -> str:
    """Convert object to JSON string."""
    import json
    return json.dumps(obj, indent=indent, ensure_ascii=False, default=str)


def from_json(text: str) -> Any:
    """Parse JSON string to object."""
    import json
    return json.loads(text)


def to_csv_row(items: Sequence[Any], delim: str = ",") -> str:
    """Convert items to CSV row."""
    return delim.join(str(item) for item in items)


def from_csv_row(text: str, delim: str = ",") -> list[str]:
    """Parse CSV row to list."""
    import csv
    from io import StringIO
    reader = csv.reader(StringIO(text), delimiter=delim)
    return next(reader, [])


def to_int(text: str, default: int = 0) -> int:
    """Convert text to int with default."""
    try:
        return int(text.replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


def to_float(text: str, default: float = 0.0) -> float:
    """Convert text to float with default."""
    try:
        return float(text.replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


# =============================================================================
# Case conversion
# =============================================================================

def to_lower(ctx: object) -> str:
    """Convert to lowercase."""
    return _to_text(ctx).lower()


def to_upper(ctx: object) -> str:
    """Convert to uppercase."""
    return _to_text(ctx).upper()


def to_title(ctx: object) -> str:
    """Convert to title case."""
    return _to_text(ctx).title()


def to_snake_case(text: str) -> str:
    """Convert to snake_case."""
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', text)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return re.sub(r'[-\s]+', '_', s2).lower()


def to_camel_case(text: str) -> str:
    """Convert to camelCase."""
    parts = re.split(r'[-_\s]+', text)
    return parts[0].lower() + "".join(p.title() for p in parts[1:])


def to_pascal_case(text: str) -> str:
    """Convert to PascalCase."""
    parts = re.split(r'[-_\s]+', text)
    return "".join(p.title() for p in parts)


def to_kebab_case(text: str) -> str:
    """Convert to kebab-case."""
    return to_snake_case(text).replace("_", "-")


def slugify(text: str) -> str:
    """Convert to URL-safe slug."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')
