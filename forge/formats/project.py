"""The model's view of a text file: the same lines, with secrets and very long lines masked.

Two things never reach a model in clear:

- credentials: private key blocks and well-known token shapes (AWS, GitHub,
  GitLab, Slack, Google, Stripe, Anthropic, OpenAI, Hugging Face, npm, PyPI,
  JSON Web Tokens, passwords inside URLs and connection strings) in every
  text file, plus, in configuration formats, the value of any key named like
  a credential (password, secret, token, api_key, ...). A frontier model
  reached by escalation is outside this machine, so a secret it is shown has
  left the machine.
- lines over LONG_LINE characters (minified code, embedded base64 images in a
  notebook), which would fill the context with nothing a model can use.

The view keeps one line for each line of the file, so line numbers in the
model's patch are the file's line numbers. Each masked line holds a marker
such as [FORGE-REDACTED-3: password]. When a patch is written back, every
line that still holds a marker is replaced by the original line it stands
for. A patch that edits a marked line, or carries a marker into another
file, is refused: the model can delete such a line or leave it alone, never
rewrite or copy it.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from forge.formats.registry import FileFormat

LONG_LINE = 4000
KEEP_OF_LONG_LINE = 200
MARKER = re.compile(r"\[FORGE-(?:REDACTED|ELIDED)-\d+[^\]]*\]")

_TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("hugging-face-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}")),
    ("json-web-token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY[A-Z ]*-----.*?-----END [A-Z ]*PRIVATE KEY[A-Z ]*-----")),
)
# Patterns whose group 1 is the secret (the rest of the match stays visible).
_SECRET_GROUPS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url-password", re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:([^@\s/]+)@")),
    ("azure-account-key", re.compile(r"(?i)AccountKey=([A-Za-z0-9+/=]{20,})")),
    ("connection-string-password", re.compile(r"(?i)(?:^|[;\"'])\s*(?:password|pwd)=([^;'\"\s]+)")),
)
_SECRET_WORDS = frozenset({"password", "passwd", "passphrase", "pwd", "secret", "secrets", "token", "credential",
                           "credentials", "authorization", "dsn", "cookie", "webhook", "apikey", "privatekey",
                           "accesskey", "accountkey", "connectionstring", "pat"})
_SECRET_PAIRS = frozenset({("api", "key"), ("access", "key"), ("private", "key"), ("client", "secret"), ("auth", "token"),
                           ("connection", "string"), ("conn", "str"), ("signing", "key"), ("encryption", "key"),
                           ("session", "key"), ("account", "key"), ("sas", "token"), ("secret", "key"),
                           ("master", "key"), ("shared", "key")})
# A last word that names where or what shape a credential is, not the credential itself.
_NOT_SECRET_LAST = frozenset({"url", "uri", "endpoint", "path", "file", "dir", "name", "type", "length", "policy",
                              "expiry", "expires", "ttl", "timeout", "header", "env", "var", "field", "prompt", "label",
                              "hint", "id", "count", "limit", "max", "min", "size", "class", "format", "mode",
                              "enabled", "required", "algorithm", "usage", "source", "location", "ref", "rotation"})
# Tokens in the language-model sense: max_tokens, pad_token, eos_token are settings, not credentials.
_MODEL_TOKEN_WORDS = frozenset({"max", "num", "pad", "bos", "eos", "unk", "sep", "cls", "mask", "total", "input",
                                "output", "completion", "prompt", "special", "added", "per", "budget", "tokens",
                                "start", "end", "image", "audio", "video"})
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_KEY_VALUE = re.compile(r"""^(?P<head>\s*(?:export\s+|-\s+)?["']?(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)["']?\s*[=:]\s*)"""
                        r"""(?P<value>.*)$""")
_SPACE_KEY_VALUE = re.compile(r"(?i)^(?P<head>\s*(?:password|passwd|passphrase|secret|token)\s+)(?P<value>\S.*)$")
_XML_ELEMENT = re.compile(r"(<(?:[\w.\-]+:)?([\w.\-]+)\b[^>]*>)([^<]+)(</)")
_XML_ATTRIBUTE = re.compile(r"""\b([\w.\-]+)\s*=\s*(["'])(.*?)\2""")
_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
# .NET appSettings and similar: <add key="ApiKey" value="..."/>, the secret named by another attribute.
_XML_NAMED_VALUE = re.compile(r"""\b(?:key|name)\s*=\s*(["'])([^"']*)\1[^>]*?\bvalue\s*=\s*(["'])(.*?)\3""")


def is_secret_key(key: str) -> bool:
    """A key named like a credential: words, not substrings, so tokenizer_class and max_tokens are not."""
    words = [word.lower() for word in _WORD.findall(key)]
    if not words or words[-1] in _NOT_SECRET_LAST:
        return False
    if any(word in ("token", "tokens") for word in words) and any(word in _MODEL_TOKEN_WORDS for word in words
                                                                  if word not in ("token", "tokens")):
        return False
    return any(word in _SECRET_WORDS for word in words) or any(pair in _SECRET_PAIRS for pair in itertools.pairwise(words))


_PLACEHOLDER = re.compile(
    r"(?i)^(?:|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%\([^)]*\)s|\{\{.*\}\}|<[^>]*>|env\(.*\)|ENC\(.*\)|"
    r"\*+|x+|null|none|nil|true|false|yes|no|~|changeme|change_me|example|placeholder|redacted|\[\]|\{\})$")
_PEM_BEGIN = re.compile(r"-----BEGIN ([A-Z ]*PRIVATE KEY[A-Z ]*)-----")
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY[A-Z ]*-----")


class ProjectionError(ValueError):
    """A patch edits or copies a masked line."""


@dataclass
class Projection:
    view: str
    originals: dict[str, str] = field(default_factory=dict)  # view line -> original line, where they differ
    redacted: int = 0
    elided: int = 0

    def notes(self) -> list[str]:
        notes = []
        if self.redacted:
            notes.append(f"{self.redacted} secret value(s) are shown as [FORGE-REDACTED-n] markers; lines holding "
                         "them can be deleted or left as they are, not rewritten")
        if self.elided:
            notes.append(f"{self.elided} line(s) longer than {LONG_LINE} characters are cut short and marked "
                         "[FORGE-ELIDED-n]; they cannot be edited")
        return notes


class _Masker:
    def __init__(self) -> None:
        self.count = 0
        self.redacted = 0
        self.elided = 0

    def mark(self, kind: str) -> str:
        self.count += 1
        if kind == "elided":
            self.elided += 1
            return f"[FORGE-ELIDED-{self.count}"
        self.redacted += 1
        return f"[FORGE-REDACTED-{self.count}: {kind}]"


def _quoted(value: str) -> tuple[str, str, str] | None:
    """(opening quote + nothing, inner, rest) for a quoted value, else None."""
    if value[:1] in "\"'":
        quote = value[0]
        end = 1
        while end < len(value):
            if value[end] == "\\":
                end += 2
                continue
            if value[end] == quote:
                return quote, value[1:end], value[end:]
            end += 1
    return None


def _secret_value(value: str) -> bool:
    value = value.strip()
    return bool(value) and not _PLACEHOLDER.match(value) and not _NUMBER.match(value) and not MARKER.search(value)


def _mask_key_values(line: str, masker: _Masker, fmt: FileFormat) -> str:
    match = _KEY_VALUE.match(line)
    if match is not None and not is_secret_key(match.group("key")):
        match = None
    if match is None and fmt.format_id in ("credentials_file", "conf", "vpn_config", "ssh_config"):
        match = _SPACE_KEY_VALUE.match(line)
    if match is not None:
        value = match.group("value")
        quoted = _quoted(value)
        if quoted is not None:
            quote, inner, rest = quoted
            if _secret_value(inner):
                return match.group("head") + quote + masker.mark("value") + rest
        else:
            bare, trail = value.rstrip(), ""
            if bare.endswith(","):
                bare, trail = bare[:-1].rstrip(), ","
            if _secret_value(bare) and bare[:1] not in "[{|>&":
                return match.group("head") + masker.mark("value") + trail
        return line
    line = _XML_NAMED_VALUE.sub(
        lambda m: m.group(0)[:m.start(4) - m.start(0)] + masker.mark("value") + m.group(3)
        if is_secret_key(m.group(2)) and _secret_value(m.group(4)) else m.group(0), line)
    line = _XML_ELEMENT.sub(lambda m: m.group(1) + (masker.mark("value") if is_secret_key(m.group(2))
                                                    and _secret_value(m.group(3)) else m.group(3)) + m.group(4), line)
    return _XML_ATTRIBUTE.sub(
        lambda m: m.group(0)[:m.start(3) - m.start(0)] + masker.mark("value") + m.group(2)
        if is_secret_key(m.group(1)) and _secret_value(m.group(3)) else m.group(0), line)


def _mask_tokens(line: str, masker: _Masker) -> str:
    for kind, pattern in _TOKENS:
        line = pattern.sub(lambda _m, kind=kind: masker.mark(kind), line)
    for kind, pattern in _SECRET_GROUPS:
        line = pattern.sub(lambda m, kind=kind: m.group(0)[:m.start(1) - m.start(0)] + masker.mark(kind)
                           + m.group(0)[m.end(1) - m.start(0):], line)
    return line


def project(text: str, fmt: FileFormat) -> Projection:
    """The model's view of a decoded text file (newlines already "\\n")."""
    masker = _Masker()
    originals: dict[str, str] = {}
    out: list[str] = []
    in_key = False
    lines = text.split("\n")
    for original in lines:
        line = original
        if in_key:
            if _PEM_END.search(line):
                in_key = False
            else:
                line = masker.mark("private-key")
        elif _PEM_BEGIN.search(line) and not _PEM_END.search(line):
            in_key = True
        else:
            line = _mask_tokens(line, masker)
            if fmt.secrets:
                line = _mask_key_values(line, masker, fmt)
        if len(line) > LONG_LINE:
            line = line[:KEEP_OF_LONG_LINE] + masker.mark("elided") + f": {len(original) - KEEP_OF_LONG_LINE} more " \
                "characters]"
        if line != original or MARKER.search(original):
            originals[line] = original
        out.append(line)
    return Projection(view="\n".join(out), originals=originals, redacted=masker.redacted, elided=masker.elided)


def restore(edited: str, projection: Projection) -> str:
    """The real text for an edited view: every marked line back to the line it stands for."""
    if not projection.originals:
        if MARKER.search(edited):
            raise ProjectionError("the edit adds a [FORGE-REDACTED] or [FORGE-ELIDED] marker that does not belong "
                                  "to this file; masked values cannot be copied between files")
        return edited
    out = []
    for number, line in enumerate(edited.split("\n"), 1):
        if MARKER.search(line):
            if line not in projection.originals:
                raise ProjectionError(
                    f"line {number} changes a line that holds a masked value; FORGE cannot rewrite lines holding "
                    "secrets or elided text. Delete the line or leave it unchanged, or ask the person")
            out.append(projection.originals[line])
        else:
            out.append(line)
    return "\n".join(out)
