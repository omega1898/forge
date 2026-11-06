"""File formats: what each file is, how a model may see it, and how an edit is written back.

Every file the assistant reads or changes passes through this package:

- registry.py names each format (detected by file name, suffix and content) and
  records the rules that matter for it: line endings, legacy encodings, the
  syntax check, and whether it may hold credentials.
- text.py decodes and re-encodes text so an edit keeps the file's exact
  encoding, byte order mark and line endings.
- project.py builds the model's view of a text file: the same lines, with
  secrets and very long lines replaced by markers that are restored when an
  edit is written back.
- checks.py is the syntax gate: an edit that leaves a file unparseable is
  refused before any test runs.
- views.py reads documents, notebooks, model files, databases and binaries
  with bounded, pure-Python parsers and never runs their contents.
"""
