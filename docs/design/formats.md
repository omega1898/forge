# File formats: read anything, edit text exactly, never leak a secret

FORGE recognizes 199 file formats across the areas it is built for:
avionics, embedded and chip design; mainframe; databases and Access; Office
and documents; compliance; security; AI; full-stack; cloud; and Windows. Every
file the assistant reads or changes goes through `forge/formats/`. The full
list by area is in [formats-by-area.md](formats-by-area.md).

```bash
forge formats list --area mainframe     # what FORGE does with each format
forge formats show report.docx          # what a model would be shown
forge formats check app.properties      # the syntax gate; exit status 1 on an error
```

## Why this exists

Before this layer, the assistant decoded every file as UTF-8 and wrote back
UTF-8. Two edits show what that did:

| File | Before the edit | After the old edit |
|---|---|---|
| `run.bat` (CRLF) | `goto :start\r\n` | `goto :start\n`. cmd.exe can fail to find GOTO and CALL labels in LF-only files. |
| `app.properties` (ISO-8859-1) | `Grüße` | `Gr��e`: two bytes became U+FFFD twice |

It also showed binary files to the model as replacement characters. It showed
every secret in a `.env` file. And it could not patch a C file containing a
form feed, because the patch parser split lines with Python's
`str.splitlines()`, which also splits at form feeds and Unicode separators.

## The five parts

| Module | Job |
|---|---|
| `registry.py` | Finds each file's format. Content comes first (a ZIP is a ZIP whatever it is called), then the exact name (`Makefile`), then the longest suffix (`.cdx.json` before `.json`), then a content sniff where one suffix means two things. For example, `.cls` is a VBA class or a LaTeX class, and `.frm` is a VBA form or a MySQL table. |
| `text.py` | Decodes and re-encodes text. The byte order mark, the encoding and the line-ending style are recorded and restored. A file decoded and encoded unchanged comes back byte for byte (tested on random content in six encodings and three line-ending styles). A character the file's encoding cannot hold is refused with a reason, never replaced. |
| `project.py` | Builds the model's view of a text file: the same lines, with secrets and very long lines replaced by markers. Markers are restored when an edit is written back. |
| `checks.py` | The syntax gate: an edit that leaves a file unparseable is refused before any build or test. |
| `views.py` | Bounded, pure-Python readers for documents, model files, databases and binaries. Nothing is ever executed. |

## What a model sees

**Text** keeps one line for each line of the file, so line numbers in the
model's patch are the file's line numbers. FORGE's notes come after the
content, in a block marked as not part of the file. For example, "Windows-1252,
CRLF line endings; edits keep them".

**Secrets are masked in every text file.** A frontier model reached by
escalation is outside this machine, so a secret shown to it has left the
machine.

- **Token shapes, in every file:** AWS access keys, GitHub, GitLab, Slack,
  Google, Stripe, Anthropic/OpenAI, Hugging Face, npm and PyPI tokens, JSON
  Web Tokens, private key blocks, and passwords in URLs and connection
  strings.
- **Credential values, in configuration formats:** the value of any key named
  like a credential, including YAML, JSON, TOML, INI, `.properties`, `.env`,
  XML elements and attributes, and .NET `<add key="ApiKey" value="..."/>`.
  Keys are matched by whole words, so these are masked:
  - `auth_token`
  - `api_key`
  - `client_secret`

  These stay visible, because they are model settings and file names, not
  credentials:
  - `max_tokens`
  - `tokenizer_class`
  - `pad_token`
  - `password_file`

  References such as `${VAULT_TOKEN}` and plain numbers stay visible too.
- **The search index and the context builder** store and show the masked view
  only, so a search for a secret finds nothing.

A line holding a marker can be deleted, which removes a hard-coded
credential, or left as it is. It cannot be rewritten. A marker copied into
another file is refused, so a secret cannot be moved somewhere it is visible.

**Never shown at all:** private key files, keystores (`.p12`, `.jks`,
`.kdbx`), OpenPGP data, Terraform state and HTTP archives (`.har`). Terraform
state and HTTP archives routinely hold credentials in plain text.

**Documents and binaries** are summarized by readers that parse, never run:

| Reader | Shows | Flags |
|---|---|---|
| Word (`.docx`, `.docm`) | headings, lists, tables, tracked deletions, comments | VBA macros, embedded objects |
| Excel (`.xlsx`, `.xlsm`) | cells by column, formulas with their cached values | hidden and very hidden sheets, macros, external links |
| PowerPoint (`.pptx`) | slide text in order, speaker notes | macros |
| OpenDocument (`.odt`, `.ods`, `.odp`) | headings, paragraphs, tables, slides | |
| ZIP-based archives | members and sizes | zip slip paths, compression over 100:1, executables and scripts, pickles inside (scanned) |
| Legacy Office, `.msg`, `.msi` (OLE2) | stream names | VBA macros, password protection |
| Pickle, PyTorch, joblib | every callable the file would import on load, without unpickling | `os.system`, `eval`, `subprocess` and other known-dangerous imports |
| safetensors | tensors, dtypes, shapes, metadata | offsets outside the file, overlaps, wrong sizes |
| GGUF | metadata and the chat template (Jinja the runtime renders) | |
| Keras (`.keras`) | layer classes | Lambda layers (Python code) |
| NumPy (`.npy`, `.npz`) | dtype and shape from the header | object arrays (loading needs `allow_pickle=True`, which runs a pickle) |
| SQLite | the schema, from a read-only copy opened in defensive mode with `trusted_schema` off | |
| Access (`.mdb`, `.accdb`) | engine and version | |
| ELF, PE | class, machine, type, entry point; for PE also subsystem and timestamp | PE without ASLR, DEP or Control Flow Guard |
| PEM | the text, private keys masked | certificate subject, issuer, validity, expiry, self-signed |
| pcap | link type, packet count | |

The readers have fixed limits:

- ZIP members are read up to 32 MiB each and 128 MiB in total (more is
  refused as a possible ZIP bomb).
- The XML parser refuses any DOCTYPE or entity declaration. Office Open XML
  and OpenDocument never use one, and declarations are how entity-expansion
  and external-entity attacks start.
- Binary headers are parsed with every offset checked against the bytes read.

## What a model can change

Text files can be changed through patches. Each patch goes through these
steps:

1. A patch to a protected path (tests, evaluator files, `forge.toml`) is
   refused first; see [check.md](check.md).
2. A patch to a document, a binary or a secret store is refused with a reason.
3. The patch is applied to the model's view, masked lines are restored, and
   the text is written back with the file's own encoding, byte order mark and
   line endings.
4. A new file gets what its format needs: CRLF for batch files, a UTF-8 byte
   order mark for PowerShell with non-ASCII text, LF otherwise.
5. The syntax gate runs on every edited file. Only problems the edit
   introduced count, so a file that was already broken, or a format detected
   wrongly, cannot trap the model.

## The syntax gate

| Check | Catches |
|---|---|
| Python, JSON, JSON Lines, TOML, XML, notebooks, SARIF 2.1.0 | parse errors, with the line |
| YAML | parse errors with PyYAML if installed (nodes only; nothing is constructed). Tabs in indentation are always checked. Without PyYAML the result is "not checked", never "passed". Templates (`{{ }}`) are not checked. |
| INI and `setup.cfg`, systemd units | keys outside sections, duplicate sections and keys (repeated keys are allowed where the format allows them, such as `.gitconfig` and systemd) |
| Java `.properties` | malformed `\uXXXX` escapes, which make `Properties.load` fail |
| `.env` | lines that are not `NAME=value`, unclosed quotes |
| CSV, TSV | quoting errors; rows of a different width (warning) |
| Makefile | recipe lines that start with spaces instead of a tab ("missing separator") |
| Dockerfile | unknown instructions, a first instruction other than FROM or ARG; heredocs and the escape directive are understood |
| JCL | records over 80 columns, names over 8 characters |
| COBOL and Fortran, fixed form | code past column 72, which the compiler ignores, so the statement is cut silently (gfortran warns only with `-Wline-truncation`); invalid indicator characters; non-numeric labels |
| Fortran, free form | lines over 132 characters (gfortran's default limit, an error) |
| Intel HEX, Motorola S-records | every record's byte count and checksum; the end record |
| Batch files | LF-only line endings (warning) |
| PowerShell | non-ASCII text in UTF-8 without a byte order mark, which Windows PowerShell 5.1 reads in the ANSI code page (warning) |

## Limits

- **No text yet for PDF, RTF, legacy `.doc` and `.xls`, or Access tables.**
  They are identified and say what they need: pdftotext, LibreOffice or
  mdbtools, run in the sandbox. That is the next step. Until then FORGE shows
  a note, never garbage.
- **Documents are read, not written.** Changing a Word or Excel file means
  changing the text it is generated from, or asking the person.
- **EBCDIC datasets** without line breaks (fixed records copied off a
  mainframe in binary) are not yet split into records. The COBOL pack's
  record decoder handles their data.
- **Secret masking is pattern-based.** A credential with no recognizable
  shape, stored under an innocent key in source code, is not caught. YAML
  block scalars (`key: |`) under a secret key are not masked line by line,
  except private key blocks.
- **The syntax checks are parsers, not compilers.** Code that parses can still
  fail to build; the build and tests decide that.
