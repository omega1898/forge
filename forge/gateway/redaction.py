"""Redact recognizable secrets before any text is sent to a model.

A pattern scanner is incomplete by nature, so it is the second line of
defense: secret files are already excluded from snapshots by name (F03),
and evaluator suites are marked sensitive. This catches secrets that sit
inside ordinary source files.
"""

from __future__ import annotations

import re

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("anthropic_or_openai_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("password_in_url", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("assigned_secret", re.compile(
        r"(?i)\b(password|passwd|secret|api_key|apikey|token)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3")),
)


def redact(text: str) -> tuple[str, list[str]]:
    """Return redacted text and the kinds of secret that were removed (never the values)."""
    found: list[str] = []
    for kind, pattern in PATTERNS:
        def replace(match: re.Match[str], kind: str = kind) -> str:
            found.append(kind)
            if kind == "assigned_secret":
                return f"{match.group(1)}{match.group(2)}[REDACTED:{kind}]"
            return f"[REDACTED:{kind}]"

        text = pattern.sub(replace, text)
    return text, sorted(set(found))
