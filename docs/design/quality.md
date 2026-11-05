# Coding standards as code

FORGE enforces published coding standards with open checkers and its own
analyzers, and ties every finding to the rule it breaks. The same gate runs
inside the assistant loop: a model's change that passes its tests but breaks
a standard is rejected. The model then gets the rule, the weakness, and
CERT's compliant example, and tries again.

The research behind this design is in
[reports/Open coding standards and checkers.md](../../reports/Open%20coding%20standards%20and%20checkers.md).
The gate exists for one reason. Studies cited there found that 30–45% of
model-written code is insecure whatever the model's size, and feeding
analyzer findings back to the model cut security issues from over 40% to 13%.

## Choosing standards

```toml
[quality]
standards = ["cert-c", "power-of-ten"]
```

`forge check standards` lists every choice. Without a `[quality]` section,
each language gets defaults:

| Toolchain | Default standards |
|---|---|
| C, ARM C | cert-c, cwe-top25, shell |
| C++ | cert-cpp, cwe-top25, shell |
| Python | python-secure, cwe-top25, shell |
| Ada | ada-warnings, shell |
| Fortran | fortran-warnings, shell |
| COBOL | cobol-warnings, shell |

These are the profiles you can choose:

| Profile | Checkers | Blocks on |
|---|---|---|
| `cert-c` / `cert-cpp` | GCC 14 `-fanalyzer` (C), clang-tidy `cert-*`, `bugprone-*`, `clang-analyzer-*`, cppcheck | findings tied to a CERT rule |
| `cwe-top25` | the same, and ruff's security rules for Python | weaknesses in the 2025 Top 25, including a more specific weakness whose parent is in the list |
| `power-of-ten` | FORGE's analyzer and GCC with `-Wall -Wextra -Wpedantic` | decidable Power of Ten rules, and compiler warnings |
| `cpp-core-guidelines` | clang-tidy `cppcoreguidelines-*` | nothing (advisory) |
| `python-secure` | ruff with the pyflakes, bugbear and bandit rule families (`F`, `B`, `S`) and syntax errors (`E9`) | syntax errors and undefined names |
| `pep8` | ruff `E`, `W` | nothing (advisory) |
| `ada-restricted` | GNAT with `pragma Restrictions` (No_Recursion, No_Allocators, No_Implicit_Heap_Allocations, No_Abort_Statements, No_Implementation_Pragmas) | every violation, as a compile error |
| `fortran-2018` | gfortran with `-std=f2018 -fimplicit-none -Wall -Wextra` | conformance errors |
| `ada-warnings`, `fortran-warnings`, `cobol-warnings` | the compiler with every warning on | compiler errors |
| `shell` | ShellCheck on every `.sh` and `.bash` file | ShellCheck errors |

The standards you choose belong to the protected project spec. Changing them
takes a person's `--accept-tests`, so a model cannot remove a standard to get
past it.

## What each finding carries

Each finding has the following fields:

- **Tool and rule:** for example `clang-tidy:cert-err33-c` or `gcc:-Wanalyzer-possible-null-dereference`.
- **Weakness:** the CWE IDs the tool reported, kept only if CWE 5.0 defines them.
- **Standards:**
  - `CERT ERR33-C`, `CWE Top 25 (2025)`, `CISQ security`, `OWASP A10:2025`, `Power of Ten P10-1`, or `Ada pragma Restrictions (No_Recursion)`.
  - CERT rules come from four sources:
    - the check name (`cert-err33-c`);
    - CERT's own "Automated Detection" tables (bundled);
    - CWE entries that CWE 5.0 marks as an exact CERT equivalent;
    - for GCC analyzer warnings that CERT's tables omit, FORGE's own mapping, kept in a table in `forge/quality/registry.py`.
- **Blocking:** a blocking finding fails `forge check analyze`, the compliance gate's static-analysis check, and the assistant's quality gate.
- **Decidable:** rules a tool cannot decide are not blocking. They become items for a person's attestation.
- **Line fingerprint:** GitHub code scanning's `primaryLocationLineHash`, ported and checked against GitHub's own test vectors. Findings keep their identity across unrelated edits and match GitHub's.

Findings from different tools about the same weakness at the same line merge
into one. The other tools that reported it are listed as provenance.
`forge check analyze` stores one SARIF 2.1.0 file per run. The same findings
always produce the same bytes.

## The bundled knowledge, and what is not bundled

| Pack | Source | Licence |
|---|---|---|
| CWE 5.0 (984 weaknesses, every view, graded CERT, CISQ, ISA/IEC 62443 and OpenSSF mappings) | MITRE, file SHA-256 `bcda44c9…73a3dc` | MITRE terms of use: the notice travels with every copy |
| SEI CERT C (122 rules) and C++ (83 rules), with risk tables, detectors and compliant examples | cmu-sei/secure-coding-standards | text CC BY 4.0, examples MIT |
| bandit's CWE table (75 rules, through ruff's matching `S` codes) | PyCQA/bandit | Apache-2.0 |

MISRA, AUTOSAR, HIC++ and the C++ Core Guidelines texts are not bundled; their
licences forbid it. FORGE uses only their rule IDs and check names. You can
import your own licensed copy with `forge comply import-catalog`.

Semgrep's registry rules, the CodeQL command-line tool and Sonar's analyzers
are not bundled either. Their terms forbid redistribution, and for Sonar
also use by outside AI tools.

The packs are rebuilt from their sources with `python -m forge.quality.packs`.

## The assistant's quality gate

`TaskAssistant(..., quality_gate=QualityGate(QualityRunner(checks), build, standards))`:

1. The base snapshot is scanned once, and its findings become the baseline.
   Old findings are never blamed on the model.
2. When a candidate passes its protected tests, it is scanned with the same
   standards.
3. A candidate that adds a blocking finding is rejected. The model's next
   prompt then lists, for each new finding (up to five):
   - the file and line;
   - the rule and the CWE weakness;
   - CERT's compliant example.

   This follows the edit-then-lint-then-revert loop that SWE-agent and Aider
   use, with the project's own standards.
4. If the checkers cannot run cleanly, the candidate is rejected and the model
   is told why. A failed scan never counts as a clean one.

## Power of Ten (NASA/JPL, Holzmann 2006)

FORGE's own analyzer reads the source text. It never runs the code.

| Rule | Checked | Decidable |
|---|---|---|
| P10-1 | `goto`, `setjmp`/`longjmp`, recursion (including indirect) among the analyzed functions | yes |
| P10-2 | `for (;;)`, `while (1)`, `while (true)` are reported; a person confirms bounds | no |
| P10-3 | allocation calls outside `*init*` functions are reported; a person confirms | no |
| P10-4 | functions over 60 lines | yes |
| P10-5 | fewer than two assertions per function on average, per file | yes |
| P10-6 | smallest scope | attestation only |
| P10-7 | return values: enforced through CERT ERR33-C in the `cert-c` profile | via CERT |
| P10-8 | token pasting `##` and variadic macros | yes |
| P10-9 | pointer-to-pointer declarations (except `main`'s `argv`), function pointers, pointer typedefs | yes |
| P10-10 | zero warnings with `-Wall -Wextra -Wpedantic` | yes |

The analyzer is lexical: macros are not expanded, so code generated by macros
is not seen.

## Limits

- Results depend on which checkers are installed. When a standard needs a
  tool that is missing, the result is "not run", never "met".
- Classification is table lookup. FORGE's GCC-to-CERT map is its own
  judgement and is labelled as such. Everything else comes from CERT's or
  MITRE's published data.
- CWE 5.0 still titles its OWASP view "RC1 (2025)". Where it differs from
  OWASP's final 2025 list, OWASP's own category pages are the authority.
