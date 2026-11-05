"""Mutation testing for Python: do the tests notice when the code is wrong?

A test suite that still passes after the code is deliberately broken is not
constraining that code. This tool makes small, deterministic changes
("mutants") to production lines, runs the project's tests in the sandbox
against each, and reports which mutants survived. It is the generalized
form of the D03/D07 rule "a test that cannot fail measures nothing".

Operators (one change per mutant): comparison swaps (< and <=, == and !=,
in and not in, is and is not), arithmetic swaps (+ and -, * and +, / and *,
// and *, % and //), and/or swap, removing `not`, integer constant + 1,
True/False swap, `return value` to `return None`, negating an `if` or
`while` condition, and augmented-assignment swaps (+= and -=).

Mutants are limited to the lines a change touched, ordered deterministically
and capped. A surviving mutant is a lead, not a verdict: it may be
equivalent to the original program (no test could ever kill it), which only
a reviewer can decide.
"""

from __future__ import annotations

import ast
import copy
import difflib
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from forge.contracts.ids import new_id

_COMPARE = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
            ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
_BINARY = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult,
           ast.Mod: ast.FloorDiv}
_SYMBOL = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=", ast.In: "in",
           ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not", ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
           ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%"}


@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    path: str
    line: int
    operator: str
    description: str
    source: str = field(repr=False)


def changed_lines(before: str | None, after: str) -> set[int]:
    """1-based line numbers in `after` that were added or replaced since `before`."""
    new_lines = after.splitlines()
    if before is None:
        return set(range(1, len(new_lines) + 1))
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=new_lines, autojunk=False)
    lines: set[int] = set()
    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            lines.update(range(j1 + 1, j2 + 1))
    return lines


def _variants(node: ast.AST) -> list[tuple[str, str, ast.AST]]:
    """(operator, description, replacement node) for one node."""
    found: list[tuple[str, str, ast.AST]] = []
    if isinstance(node, ast.Compare):
        for index, op in enumerate(node.ops):
            swap = _COMPARE.get(type(op))
            if swap:
                changed = copy.deepcopy(node)
                changed.ops[index] = swap()
                found.append(("compare", f"{_SYMBOL[type(op)]} -> {_SYMBOL[swap]}", changed))
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        swap = _BINARY[type(node.op)]
        changed = copy.deepcopy(node)
        changed.op = swap()
        found.append(("arithmetic", f"{_SYMBOL[type(node.op)]} -> {_SYMBOL[swap]}", changed))
    elif isinstance(node, ast.BoolOp):
        changed = copy.deepcopy(node)
        changed.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        found.append(("boolean", "and -> or" if isinstance(node.op, ast.And) else "or -> and", changed))
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        found.append(("negation", "remove not", copy.deepcopy(node.operand)))
    elif isinstance(node, ast.Constant) and type(node.value) is bool:
        found.append(("constant", f"{node.value} -> {not node.value}", ast.Constant(value=not node.value)))
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        found.append(("constant", f"{node.value} -> {node.value + 1}", ast.Constant(value=node.value + 1)))
    elif isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None):
        found.append(("return", "return value -> return None", ast.Return(value=ast.Constant(value=None))))
    elif isinstance(node, (ast.If, ast.While)):
        changed = copy.deepcopy(node)
        changed.test = ast.UnaryOp(op=ast.Not(), operand=changed.test)
        found.append(("condition", f"negate the {type(node).__name__.lower()} condition", changed))
    elif isinstance(node, ast.AugAssign) and type(node.op) in (ast.Add, ast.Sub):
        changed = copy.deepcopy(node)
        changed.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
        found.append(("augmented", f"{_SYMBOL[type(node.op)]}= -> {_SYMBOL[type(changed.op)]}=", changed))
    return found


class _Replace(ast.NodeTransformer):
    def __init__(self, target: ast.AST, replacement: ast.AST) -> None:
        self.target, self.replacement = target, replacement

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if node is self.target:
            return ast.copy_location(self.replacement, node)
        return super().generic_visit(node)


def generate_mutants(path: str, source: str, *, lines: set[int] | None = None, limit: int = 30) -> list[Mutant]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    original = ast.unparse(tree)
    # Docstrings and annotations are not behavior.
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            skip.add(id(node.body[0].value))
        if isinstance(node, (ast.arg, ast.AnnAssign)) and getattr(node, "annotation", None) is not None:
            skip.update(id(child) for child in ast.walk(node.annotation))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            skip.update(id(child) for child in ast.walk(node.returns))
    mutants: list[Mutant] = []
    seen: set[str] = {original}
    positions = {id(n): index for index, n in enumerate(ast.walk(tree))}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line is None or id(node) in skip or (lines is not None and line not in lines):
            continue
        for operator, description, replacement in _variants(node):
            # Replace on a deep copy: the same node sits at the same walk position.
            copied = copy.deepcopy(tree)
            target = list(ast.walk(copied))[positions[id(node)]]
            mutated_tree = _Replace(target, replacement).visit(copied)
            ast.fix_missing_locations(mutated_tree)
            try:
                text = ast.unparse(mutated_tree)
                compile(text, path, "exec")
            except (SyntaxError, ValueError, RecursionError):
                continue
            if text in seen:
                continue
            seen.add(text)
            mutants.append(Mutant(f"m{len(mutants) + 1:03d}", path, line, operator, description, text + "\n"))
    mutants.sort(key=lambda m: (m.path, m.line, m.operator, m.description))
    return [replace(m, mutant_id=f"{path}:m{index:03d}") for index, m in enumerate(mutants[:limit], 1)]


@dataclass
class MutantResult:
    mutant: Mutant
    status: str          # killed | survived | error
    detail: str = ""


@dataclass
class MutationReport:
    baseline_ok: bool
    results: list[MutantResult] = field(default_factory=list)
    note: str = ""

    @property
    def killed(self) -> int:
        return sum(1 for r in self.results if r.status == "killed")

    @property
    def survivors(self) -> list[MutantResult]:
        return [r for r in self.results if r.status == "survived"]

    @property
    def score(self) -> float | None:
        counted = [r for r in self.results if r.status in ("killed", "survived")]
        return None if not counted else sum(1 for r in counted if r.status == "killed") / len(counted)


def run_mutation(runner, snapshot_dir: Path, mutants: list[Mutant]) -> MutationReport:  # noqa: ANN001 - TestRunner
    """Run the tests once unchanged (they must pass), then once per mutant."""
    baseline = runner.run_directory(snapshot_dir)
    if not baseline.all_pass():
        return MutationReport(baseline_ok=False, note="the tests do not all pass on the unmutated code")
    report = MutationReport(baseline_ok=True)
    for mutant in mutants:
        copy_dir = runner.service.work_root / f"{new_id('job')}-mutant"
        shutil.copytree(snapshot_dir, copy_dir)
        try:
            (copy_dir / mutant.path).write_text(mutant.source)
            run = runner.run_directory(copy_dir)
        finally:
            shutil.rmtree(copy_dir, ignore_errors=True)
        if run.status == "error":
            # A crash or hang caused by the mutant is detected behavior: count it as killed.
            report.results.append(MutantResult(mutant, "killed", f"run failed: {run.error}"[:200]))
        elif run.all_pass():
            report.results.append(MutantResult(mutant, "survived", "every test still passes"))
        else:
            failing = [r.test_id for r in run.results if r.outcome != "pass"]
            report.results.append(MutantResult(mutant, "killed", f"failed: {', '.join(failing[:3])}"))
    return report
