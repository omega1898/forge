# Compliance in FORGE

FORGE checks software against the standards of the industry it is built
for. A person chooses the domain once. The checks then run on their own, and
the report names the exact control in the exact standard.

## Four commands

```bash
forge comply set avionics --level A
```

This sets the domain. From now on, every new execute task carries the
domain's gate. Such a task cannot complete until the gate's checks are met on
the snapshot being delivered, or a person waives them with a recorded
decision. Only a trusted person can set or switch off the domain (`forge
comply set none`). A model cannot, not even by calling the controller
directly.

```bash
forge comply check --requirements reqs.csv --output report.md
```

`forge check` runs the tests, analysis and coverage first, then this
compliance check. On its own, `forge comply check` produces the evidence
FORGE can produce without running your code:

- a CycloneDX 1.6 SBOM;
- a secrets scan;
- a requirements trace;
- a known-vulnerability scan, if you imported a database.

It then evaluates every check, records the gate's result, prints what to do
next and writes the full report.

```bash
forge comply attest nist-800-53r5 AC-1 AT-1 --statement "Policy v3 approved by the CISO, 2026-10-01"
```

This records your statement for controls FORGE cannot check from code, such
as policies, training or incident response. It is recorded in your name. For
many controls at once, use `--file attestations.csv` with the columns
`catalog,label,statement`.

```bash
forge comply report --domain banking --output banking.md
```

This reports against any domain without changing the gate. Use `--output
FILE.json` for machine-readable output.

`forge comply domains` lists every domain, its options and its standards.

## Domains

| Domain | Catalogs cited | Gate checks (FORGE's choice) | Options |
|---|---|---|---|
| general | SSDF | tests, static analysis, no secrets | |
| federal | 800-53 baseline, SSDF | + dynamic analysis, flaws, change control, review, SBOM, audit log | `--baseline low/moderate/high` |
| defense | 800-171 Rev. 3, 800-53 baseline, SSDF | federal + vulnerability scan | `--baseline` |
| avionics | SSDF + your DO-178C import | tests, traceability, static analysis, change control, review, coverage by level | `--level A–E` (A: MC/DC, B: decision, C: statement) |
| medical | SSDF + your IEC 62304 import | tests, traceability, static analysis, change control, review, SBOM, vulnerability scan, no secrets | `--safety-class`, `--coverage` |
| banking | 800-53 baseline, SSDF | federal + vulnerability scan | `--baseline` |
| automotive, rail, nuclear, space | SSDF + your standard's import | tests, traceability, static analysis, change control, review (+ coverage if set) | ASIL / SIL / class, `--coverage` |
| industrial | SSDF + your IEC 61508 / 62443 import | as above + vulnerability scan | `--sil`, `--coverage` |

The gate checks are FORGE's choice of what must be shown before work is
done. They are evidence, not certification. The standard's own requirements
come from the standard.

## What the report says about each control

| Status | Meaning |
|---|---|
| met | FORGE's evidence for this snapshot supports the control. Its organizational parts still need the responsible person. |
| gap | The evidence shows a problem, for example a secret in source or an open high-severity flaw. |
| incomplete | The evidence that would decide it has not been produced. The report says which command produces it. |
| attested | A person stated it is in place. The report shows who, when and what they said. |
| needs attestation | FORGE cannot check it from code. |

A "process" attestation carries forward to later snapshots until it is
withdrawn (`--withdraw`). A "snapshot" attestation (`--scope snapshot`)
applies only to the snapshot it names.

## Where the standards come from

- **Bundled:** NIST SP 800-53 Rev. 5.2.0 with its low, moderate, high and
  privacy baselines, SP 800-171 Rev. 3 and SP 800-218 (SSDF). They are
  imported from NIST's official OSCAL repository
  (`github.com/usnistgov/oscal-content`). They are public domain. Each
  report cites the source URL and the SHA-256 of the exact file. A test
  fails if any control FORGE maps to is missing or withdrawn.
- **Named, not shipped:** DO-178C, DO-330, DO-326A, ARP4754B, IEC 62304,
  ISO 14971, IEC 81001-5-1, PCI DSS, ISO 26262, ISO/SAE 21434, MISRA C,
  IEC 61508, IEC 62443-4-1, EN 50716, IEC 60880 and others. These are
  copyrighted and sold, or free only under a licence that forbids
  redistribution. FORGE lists each one's publisher, how to obtain it, and
  which checks support its topics. It does not invent clause numbers.
- **Your licensed copy:** import it and it is cited like NIST's catalogs:

  ```bash
  forge comply import-catalog do178c-objectives.csv --id do-178c --title "DO-178C" --mapping do178c-map.csv
  ```

  ```bash
  forge comply set avionics --level A --catalog do-178c
  ```

  The CSV needs `id,title` columns, and optionally `group` and `parent`.
  OSCAL JSON works too. The mapping file has one `label,check_id[;check_id]`
  row per control you want FORGE to check. Imported catalogs are stored as
  sensitive and are never sent to a model.

## Evidence producers

| Producer | What it records |
|---|---|
| SBOM | Packages from `requirements*.txt`, `pyproject.toml`, `package-lock.json` (or `package.json` alone), `pom.xml`, `go.mod`, `Cargo.lock`, `Gemfile.lock` and NuGet project files, with package URLs. Every file in the snapshot is listed with its SHA-256. Unpinned dependencies are listed because they cannot be reproduced or checked. |
| Secrets | File, line and kind of each likely credential. The value is never copied into the record. |
| Trace | `Implements: HLR-1` in code and `Verifies: HLR-1` in tests, checked against an optional requirements list (`--requirements`). Gaps are named: no code, no test, or a tag that is not a listed requirement. Finding no requirements at all is "not run", not "met". |
| Vulnerabilities | Matches against OSV records you import (`forge comply import-vulns DIR`), by listed version first, then by range. The record names the database by size and digest, because offline data ages. |

The checks also read evidence that `forge check` records (see
[check.md](check.md)): test assessments, analyzer runs, sanitizer results,
builds and their digests, and structural coverage (statement, decision, and
MC/DC through GCC 14's `-fcondition-coverage`). They also read approvals
bound to change digests and the event log's replay.

## Limits

- "Met" is evidence about one snapshot. It is not an auditor's opinion or a
  certification.
- Most controls in any standard are organizational. FORGE shows which those
  are and records who attested them, but it cannot verify them.
- The vulnerability scan is only as current as your last import, and
  version-range matching is best-effort outside exact version lists.
