"""Line fingerprints compatible with GitHub code scanning (`primaryLocationLineHash`).

A finding's identity must survive unrelated edits elsewhere in the file, so
it cannot be its line number. GitHub's code scanning identifies results by a
rolling hash over the first 100 non-whitespace characters starting at the
result's line, with an occurrence counter for identical lines. This module
is a line-for-line port of that algorithm from github/codeql-action
(src/fingerprints.ts, MIT license), checked against the test vectors in its
fingerprints.test.ts. Computing the same value lets FORGE baselines round-trip
with GitHub, and lets FORGE recognize "the same finding" across snapshots
without any network.

Details that matter for exactness:

- arithmetic is 64-bit two's complement with wrap-around, printed unsigned in
  hexadecimal without leading zeros (as the `long` library does);
- characters are UTF-16 code units (JavaScript's charCodeAt), so text outside
  the Basic Multilingual Plane counts as two units;
- spaces and tabs are skipped, CR and CRLF become LF, and a sentinel 65535
  marks the end of the file.
"""

from __future__ import annotations

TAB, SPACE, LF, CR = 9, 32, 10, 13
EOF = 65535
BLOCK_SIZE = 100
MOD = 37
MASK = (1 << 64) - 1
FIRST_MOD = pow(MOD, BLOCK_SIZE, 1 << 64)


def line_hashes(text: str) -> list[str]:
    """`hash:occurrence` for every line of `text`, in line order (index 0 is line 1)."""
    window = [0] * BLOCK_SIZE
    line_numbers = [-1] * BLOCK_SIZE
    state = {"hash": 0, "index": 0, "line": 0, "line_start": True, "prev_cr": False}
    counts: dict[str, int] = {}
    out: dict[int, str] = {}

    def output() -> None:
        value = format(state["hash"] & MASK, "x")
        counts[value] = counts.get(value, 0) + 1
        out[line_numbers[state["index"]]] = f"{value}:{counts[value]}"
        line_numbers[state["index"]] = -1

    def update(current: int) -> None:
        index = state["index"]
        begin = window[index]
        window[index] = current
        state["hash"] = (MOD * state["hash"] + current - FIRST_MOD * begin) & MASK
        state["index"] = (index + 1) % BLOCK_SIZE

    def process(current: int) -> None:
        if current in (SPACE, TAB) or (state["prev_cr"] and current == LF):
            state["prev_cr"] = False
            return
        if current == CR:
            current, state["prev_cr"] = LF, True
        else:
            state["prev_cr"] = False
        if line_numbers[state["index"]] != -1:
            output()
        if state["line_start"]:
            state["line_start"] = False
            state["line"] += 1
            line_numbers[state["index"]] = state["line"]
        if current == LF:
            state["line_start"] = True
        update(current)

    units = text.encode("utf-16-le")
    for position in range(0, len(units), 2):
        process(units[position] | (units[position + 1] << 8))
    process(EOF)
    for _ in range(BLOCK_SIZE):
        if line_numbers[state["index"]] != -1:
            output()
        update(0)
    return [out[number] for number in sorted(out)]


def line_hash(text: str, line: int) -> str | None:
    """The fingerprint of one 1-based line, or None when the line does not exist."""
    hashes = line_hashes(text)
    return hashes[line - 1] if 1 <= line <= len(hashes) else None
