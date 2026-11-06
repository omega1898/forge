"""File formats: detection, exact-bytes editing, secrets masking, the syntax gate and the bounded readers.

Most tests here are attacks or corruptions that a plain "decode as UTF-8, write
back UTF-8" editor suffers: a Latin-1 file filled with U+FFFD, a batch file
silently converted to LF, a secret shown to a model, a pickle that runs code,
an XML entity bomb.
"""

from __future__ import annotations

import io
import json
import os
import pickle
import random
import sqlite3
import struct
import zipfile
from pathlib import Path

import pytest

from forge.formats import checks, registry, views
from forge.formats.checks import check, new_problems
from forge.formats.project import ProjectionError, is_secret_key, project, restore
from forge.formats.registry import BY_ID, detect
from forge.formats.text import EncodingRefused, NotText, decode, encode
from forge.workspace.patches import apply_file_patch, parse_patch


def _keep_ends(text: str) -> list[str]:
    """Lines split on "\n" only, as FORGE splits them (splitlines would also split at form feeds)."""
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def unified(path: str, before: str, after: str) -> str:
    import difflib

    return "".join(difflib.unified_diff(_keep_ends(before), _keep_ends(after), f"a/{path}", f"b/{path}"))


def zipped(members: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# ----------------------------------------------------------------- registry --


@pytest.mark.parametrize(("path", "head", "expected"), [
    ("src/Makefile", b"all:\n", "makefile"),
    ("CMakeLists.txt", b"project(x)\n", "cmake"),
    ("ops/Dockerfile.prod", b"FROM x\n", "dockerfile"),
    (".env.production", b"A=1\n", "dotenv"),
    ("report.sarif.json", b"{}", "sarif"),
    ("results.json", b'{"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "runs": []}', "sarif"),
    ("bom.json", b'{"bomFormat": "CycloneDX"}', "cyclonedx"),
    ("Thesis.cls", b"\\NeedsTeXFormat{LaTeX2e}", "latex"),
    ("Account.cls", b"VERSION 1.0 CLASS\r\n", "vba_class"),
    ("Form1.frm", b"VERSION 5.00\r\n", "vba_form"),
    ("table.frm", b"\xfe\x01\x09\x0c\x00\x00", "binary"),          # a MySQL table definition, not VBA
    ("contract.txt", b"PK\x03\x04", "zip"),                         # content wins over the name
    ("design.docx", b"PK\x03\x04", "docx"),
    ("model.pt", b"PK\x03\x04", "pytorch"),
    ("weights.pkl", b"\x80\x04\x95", "pickle"),
    ("encrypted.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole_document"),
    ("app.exe", b"", "pe"),
    ("top.vhd", b"library ieee;", "vhdl"),
    ("disk.vhd", b"conectix", "disk_image"),
    ("readings.dat", b"1.0 2.0\n", "text"),
    ("firmware.bin", b"\x00\x10\x20", "raw_image"),
    ("id_ed25519", b"-----BEGIN OPENSSH", "private_key"),
    ("mystery", b"hello\n", "text"),
    ("mystery", b"\x00\x01\x02", "binary"),
    ("JOB01.JCL", b"//JOB01 JOB\n", "jcl"),
    ("PAYROLL.CBL", b"       IDENTIFICATION DIVISION.\n", "cobol"),
])
def test_formats_are_detected_by_content_then_name_then_suffix(path: str, head: bytes, expected: str) -> None:
    assert detect(path, head).format_id == expected


def test_the_catalogue_is_consistent() -> None:
    suffixes = [s for fmt in registry.FORMATS for s in fmt.suffixes]
    names = [n for fmt in registry.FORMATS for n in fmt.names]
    assert len(suffixes) == len(set(suffixes)), "a suffix may name one format only"
    assert len(names) == len(set(names))
    for fmt in registry.FORMATS:
        assert fmt.kind in ("text", "document", "binary")
        assert fmt.check is None or fmt.check == "yaml" or fmt.check in checks.CHECKS, fmt.format_id
        assert fmt.reader is None or fmt.reader in views._READERS or fmt.reader in ("pem", "needs_converter") \
            or fmt.format_id == "safetensors", fmt.format_id
        assert set(fmt.areas) <= set(registry.AREAS), fmt.format_id
    for area in registry.AREAS:
        assert registry.formats_for_area(area), area


def _pe(dll_flags: int) -> bytes:
    data = bytearray(0x200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHI", data, 0x84, 0x8664, 3, 0)
    struct.pack_into("<H", data, 0x80 + 22, 0x22)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<HH", data, 0x98 + 68, 3, dll_flags)
    return bytes(data)


def test_a_pe_file_is_detected_by_its_header_and_its_hardening_is_reported() -> None:
    assert detect("tool.bin", _pe(0x140)).format_id == "pe"
    text = views.view("tool.exe", _pe(0x140)).text
    assert "x86-64" in text and "Windows console" in text
    assert "ASLR (dynamic base): yes" in text and "DEP (NX compatible): yes" in text
    assert "Control Flow Guard: NO" in text


# -------------------------------------------------------------- the codec --


@pytest.mark.parametrize("codec", ["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1", "cp437"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_decoding_then_encoding_returns_the_same_bytes(codec: str, newline: str) -> None:
    rng = random.Random(f"{codec}{newline!r}")
    alphabet = "abc XYZ 019 = ; \t \u00e9\u00fc\u00df\u00a3" + ("\u20ac\u2019" if codec in ("utf-8", "utf-8-sig",
                                                                                               "utf-16", "cp1252") else "")
    for _ in range(25):
        lines = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30))) for _ in range(rng.randint(1, 8))]
        data = newline.join(lines).encode(codec) + (newline.encode(codec.replace("-sig", "")) if rng.random() < .5
                                                    else b"")
        fmt = BY_ID["java_properties"] if codec == "latin-1" else BY_ID["batch"] if codec == "cp437" else None
        decoded = decode(data, fmt)
        assert encode(decoded.text, decoded) == data, (codec, newline, data)


def test_a_crlf_batch_file_keeps_its_line_endings_through_an_edit() -> None:
    """The defect this module fixes: the patched file came back with LF, which can break GOTO in cmd.exe."""
    original = b"@echo off\r\ngoto :start\r\n:start\r\necho old\r\n"
    decoded = decode(original, BY_ID["batch"])
    [patch] = parse_patch(unified("run.bat", decoded.text, decoded.text.replace("old", "new")))
    assert encode(apply_file_patch(decoded.text, patch), decoded) == original.replace(b"old", b"new")


def test_a_latin1_properties_file_keeps_its_bytes_through_an_edit() -> None:
    """The defect this module fixes: Grüße came back as Gr\\ufffd\\ufffde."""
    original = "greeting=Gr\u00fc\u00dfe\nname=old\n".encode("latin-1")
    fmt = BY_ID["java_properties"]
    decoded = decode(original, fmt)
    assert decoded.encoding == "latin-1"
    [patch] = parse_patch(unified("app.properties", decoded.text, decoded.text.replace("old", "new")))
    assert encode(apply_file_patch(decoded.text, patch), decoded, fmt) == original.replace(b"old", b"new")


def test_a_character_the_encoding_cannot_hold_is_refused_with_a_way_out() -> None:
    fmt = BY_ID["java_properties"]
    decoded = decode("a=\u00e9\n".encode("latin-1"), fmt)
    with pytest.raises(EncodingRefused, match=r"U\+20AC.*\\uXXXX"):
        encode("a=\u00e9\nb=\u20ac\n", decoded, fmt)


def test_new_files_get_the_line_endings_and_marks_their_format_needs() -> None:
    assert encode("@echo off\ngoto x\n", None, BY_ID["batch"]) == b"@echo off\r\ngoto x\r\n"
    assert encode('Write-Host "Gr\u00fc\u00dfe"\n', None, BY_ID["powershell"]).startswith(b"\xef\xbb\xbf")
    assert not encode("Write-Host hi\n", None, BY_ID["powershell"]).startswith(b"\xef\xbb\xbf")
    assert encode("echo hi\n", None, BY_ID["shell"]) == b"echo hi\n"


def test_utf16_with_and_without_a_byte_order_mark_is_text_and_other_nul_bytes_are_not() -> None:
    assert decode("REGEDIT4\r\n".encode("utf-16")).text == "REGEDIT4\n"
    assert decode("STRINGTABLE\r\n".encode("utf-16-le")).encoding == "utf-16-le"
    with pytest.raises(NotText):
        decode(b"\x7fELF\x02\x01\x01\x00\x00\x00")


def test_form_feeds_and_unicode_separators_survive_a_patch() -> None:
    """splitlines() would have split at \\x0c and \\u2028 and rewritten them as newlines."""
    before = "int a;\n\x0c\nint b; /* \u2028 */\n"
    after = before.replace("int b", "int c")
    [patch] = parse_patch(unified("x.c", before, after))
    assert apply_file_patch(before, patch) == after


def test_an_empty_context_line_without_its_leading_space_is_read_as_git_apply_reads_it() -> None:
    patch_text = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n a = 1\n\n-b = 2\n+b = 3\n"
    [patch] = parse_patch(patch_text)
    assert apply_file_patch("a = 1\n\nb = 2\n", patch) == "a = 1\n\nb = 3\n"


# ----------------------------------------------------------- secrets mask --


@pytest.mark.parametrize(("key", "secret"), [
    ("password", True), ("DB_PASSWORD", True), ("dbPassword", True), ("api_key", True), ("ApiKey", True),
    ("client_secret", True), ("AWS_SECRET_ACCESS_KEY", True), ("auth_token", True), ("connectionString", True),
    ("max_tokens", False), ("tokenizer_class", False), ("pad_token", False), ("password_file", False),
    ("token_url", False), ("secret_name", False), ("passwordPolicy", False), ("bypass", False),
])
def test_secret_keys_are_matched_by_whole_words(key: str, secret: bool) -> None:
    assert is_secret_key(key) is secret


TOKENS = {
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github": "ghp_" + "a1" * 18,
    "slack": "xoxb-" + "1234567890-abcdef",
    "stripe": "sk_live_" + "a" * 24,
    "anthropic": "sk-ant-" + "api03-" + "x" * 30,
    "jwt": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}


@pytest.mark.parametrize("token", list(TOKENS.values()), ids=list(TOKENS))
def test_token_shapes_are_masked_in_any_text_file(token: str) -> None:
    source = f'client = connect(key="{token}")\n'
    view = project(source, BY_ID["python"])
    assert token not in view.view and "[FORGE-REDACTED-1" in view.view


def test_config_values_are_masked_but_settings_and_references_are_not() -> None:
    text = ('db:\n  host: db.internal\n  password: hunter2\n  url: postgres://admin:s3cret@db/app\n'
            '  token: ${VAULT_TOKEN}\n  max_tokens: 4096\n')
    view = project(text, BY_ID["yaml"]).view
    assert "hunter2" not in view and "s3cret" not in view
    assert "db.internal" in view and "${VAULT_TOKEN}" in view and "max_tokens: 4096" in view and "admin:" in view
    xml = '<add key="ApiKey" value="zzz-secret"/>\n<add key="Theme" value="dark"/>\n<password>pw1</password>\n'
    view = project(xml, BY_ID["xml"]).view
    assert "zzz-secret" not in view and "pw1" not in view and 'value="dark"' in view


def test_a_private_key_block_is_masked_line_by_line_and_keeps_the_line_count() -> None:
    text = "key: |\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nqwerty\n-----END RSA PRIVATE KEY-----\nnext: 1\n"
    view = project(text, BY_ID["yaml"])
    assert "MIIEpAIBAAKCAQEA" not in view.view
    assert len(view.view.split("\n")) == len(text.split("\n"))
    assert view.view.split("\n")[5] == "next: 1"


def test_an_edit_next_to_a_secret_restores_the_secret_exactly() -> None:
    text = 'DB_HOST=db\nDB_PASSWORD="s3cr3t!"\nPORT=5432\n'
    masked = project(text, BY_ID["dotenv"])
    edited = apply_file_patch(masked.view, parse_patch(unified(".env", masked.view,
                                                               masked.view.replace("5432", "6543")))[0])
    assert restore(edited, masked) == text.replace("5432", "6543")


def test_an_edit_to_a_masked_line_or_a_copied_marker_is_refused() -> None:
    masked = project('DB_PASSWORD="s3cr3t!"\nPORT=1\n', BY_ID["dotenv"])
    with pytest.raises(ProjectionError, match="masked value"):
        restore(masked.view.replace("DB_PASSWORD", "DB_PASS"), masked)
    other = project("PORT=1\n", BY_ID["dotenv"])
    with pytest.raises(ProjectionError, match="copied between files"):
        restore("PORT=1\n" + masked.view.split("\n")[0] + "\n", other)


def test_deleting_a_line_that_holds_a_secret_is_allowed() -> None:
    """Removing a hard-coded credential is a security fix the model must be able to make."""
    masked = project('API_KEY = "AKIAABCDEFGHIJKLMNOP"\nuse()\n', BY_ID["python"])
    assert restore("use()\n", masked) == "use()\n"


def test_a_very_long_line_is_cut_short_in_the_view_and_restored() -> None:
    text = "short\n" + "x" * 10_000 + "\nend\n"
    masked = project(text, BY_ID["javascript"])
    assert len(masked.view) < 1000 and masked.elided == 1
    assert restore(masked.view.replace("short", "brief"), masked) == text.replace("short", "brief")


def test_a_file_that_already_contains_marker_text_keeps_it() -> None:
    text = 'doc = "[FORGE-REDACTED-1: value] is what the model sees"\nx = 1\n'
    masked = project(text, BY_ID["python"])
    assert restore(masked.view.replace("x = 1", "x = 2"), masked) == text.replace("x = 1", "x = 2")


# --------------------------------------------------------- the syntax gate --


@pytest.mark.parametrize(("path", "good", "bad"), [
    ("a.json", b'{"a": 1}', b'{"a": 1,}'),
    ("a.toml", b"x = 1\n", b"x = \n"),
    ("a.xml", b"<a><b/></a>", b"<a><b></a>"),
    ("a.py", b"def f():\n    return 1\n", b"def f(:\n"),
    ("setup.cfg", b"[metadata]\nname = x\n", b"[metadata]\nname = x\nname = y\n"),
    ("a.properties", b"k=\\u00e9\n", b"k=\\u00G9\n"),
    (".env", b"A=1\nexport B='two'\n", b"A=1\nnot a line\n"),
    ("Makefile", b"all: x\n\tcc x.c\n", b"all: x\n    cc x.c\n"),
    ("Dockerfile", b"FROM a\nRUN <<EOF\nanything\nEOF\n", b"FROM a\nRUNN b\n"),
    ("JOB1.jcl", b"//JOB1 JOB\n", b"//JOB1 JOB\n//" + b"X" * 85 + b"\n"),
    ("fw.hex", b":0300300002337A1E\n:00000001FF\n", b":0300300002337A1F\n:00000001FF\n"),
    ("fw.s19", b"S1130000285F245F2212226A000424290008237C2A\n", b"S1130000285F245F2212226A000424290008237C2B\n"),
    ("a.ipynb", b'{"cells": [], "nbformat": 4}', b'{"cells": [{"source": ""}], "nbformat": 4}'),
    ("a.sarif", b'{"version": "2.1.0", "runs": []}', b'{"version": "2.0.0", "runs": []}'),
    ("a.f90", b"x = 1\n", b"x = " + b"1 + " * 40 + b"1\n"),
])
def test_the_syntax_gate_passes_good_files_and_names_the_line_in_bad_ones(path: str, good: bytes, bad: bytes) -> None:
    fmt = detect(path, good)
    assert check(fmt, decode(good, fmt), path).status == "passed", path
    result = check(fmt, decode(bad, fmt), path)
    assert result.status == "failed" and result.errors and result.errors[0].line >= 1, path


def test_fixed_form_cobol_and_fortran_statements_past_column_72_are_errors() -> None:
    cobol = ("      * comment\n       IDENTIFICATION DIVISION.\n       PROCEDURE DIVISION.\n"
             '           DISPLAY "' + "A" * 60 + 'MORE".\n')
    fmt = BY_ID["cobol"]
    assert "column 72" in check(fmt, decode(cobol.encode(), fmt)).errors[0].message
    sequenced = "       IDENTIFICATION DIVISION.".ljust(72) + "00010000\n"  # columns 73-80: a sequence number
    assert check(fmt, decode(sequenced.encode(), fmt)).status == "passed"
    free = ">>SOURCE FORMAT IS FREE\nIDENTIFICATION DIVISION.\n" + "DISPLAY 'x'. " * 10 + "\n"
    assert check(fmt, decode(free.encode(), fmt)).status == "passed"


def test_batch_and_powershell_rules_are_warnings_not_blocks() -> None:
    bat, ps1 = BY_ID["batch"], BY_ID["powershell"]
    assert check(bat, decode(b"goto x\n:x\n", bat)).problems[0].severity == "warning"
    assert check(bat, decode(b"goto x\r\n:x\r\n", bat)).problems == []
    lf_ps1 = check(ps1, decode('Write-Host "\u00fc"\n'.encode(), ps1))
    assert lf_ps1.status == "passed" and "PowerShell 5.1" in lf_ps1.problems[0].message
    assert check(ps1, decode(b"\xef\xbb\xbf" + 'Write-Host "\u00fc"\n'.encode(), ps1)).problems == []


def test_yaml_without_a_parser_is_not_reported_as_passed() -> None:
    fmt = BY_ID["yaml"]
    result = check(fmt, decode(b"a: 1\n", fmt))
    import importlib.util

    if importlib.util.find_spec("yaml") is None:
        assert result.status == "not_checked" and "no YAML parser" in result.reason
    else:
        assert result.status == "passed"
    assert check(fmt, decode(b"a:\n\tb: 1\n", fmt)).status == "failed"
    assert check(fmt, decode(b"a: {{ .Values.x }}\n", fmt)).status == "not_checked"


def test_only_problems_the_edit_introduced_count() -> None:
    fmt = BY_ID["json"]
    broken = check(fmt, decode(b'{"a": 1,}', fmt))
    assert new_problems(broken, check(fmt, decode(b'{"a": 1,}', fmt))) == []
    assert new_problems(check(fmt, decode(b'{"a": 1}', fmt)), broken) == broken.problems


# --------------------------------------------------------------- readers --

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def test_a_word_document_is_read_with_headings_tables_tracked_deletions_and_macros_flagged() -> None:
    docx = zipped({"word/document.xml": (
        f'<w:document {W}><w:body><w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Plan</w:t></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">Level A, </w:t></w:r><w:del><w:r><w:delText>draft</w:delText></w:r>'
        '</w:del></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>HLR-1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>TC-7'
        '</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'), "word/vbaProject.bin": b"\x00"})
    result = views.view("plan.docm", docx)
    assert result.text.split("\n") == ["## Plan", "Level A, [deleted: draft]", "| HLR-1 | TC-7 |"]
    assert any("VBA macros" in note for note in result.notes)


def test_an_excel_workbook_shows_cells_formulas_and_hidden_sheets() -> None:
    xlsx = zipped({
        "xl/workbook.xml": f'<workbook {S}><sheets><sheet name="Trace" sheetId="1" r:id="rId1"/>'
                           '<sheet name="Answers" sheetId="2" r:id="rId2" state="veryHidden"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                                      'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                                      '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Requirement</t></si><si><t>HLR-1</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                                    '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><f>SUM(C2:D2)</f><v>3</v></c>'
                                    '<c r="C2" t="b"><v>1</v></c></row></sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet {S}><sheetData/></worksheet>'})
    result = views.view("trace.xlsx", xlsx)
    assert "A=Requirement" in result.text and "A=HLR-1 | B==SUM(C2:D2) -> 3 | C=TRUE" in result.text
    assert "sheet 'Answers' is veryHidden" in result.notes


def test_presentations_and_opendocument_text_are_read() -> None:
    pptx = zipped({"ppt/slides/slide2.xml": "<p:sld xmlns:p='p' xmlns:a='a'><a:p><a:r><a:t>Second</a:t></a:r></a:p>"
                                            "</p:sld>",
                   "ppt/slides/slide1.xml": "<p:sld xmlns:p='p' xmlns:a='a'><a:p><a:r><a:t>First</a:t></a:r></a:p>"
                                            "</p:sld>"})
    assert views.view("deck.pptx", pptx).text.split("\n") == ["## Slide 1", "First", "", "## Slide 2", "Second"]
    odt = zipped({"content.xml": "<office:document-content xmlns:office='o' xmlns:text='t'><office:body>"
                                 "<text:h text:outline-level='1'>Title</text:h><text:p>Body</text:p></office:body>"
                                 "</office:document-content>"})
    assert views.view("notes.odt", odt).text == "# Title\nBody"


def test_an_xml_entity_bomb_inside_a_document_is_refused() -> None:
    bomb = zipped({"word/document.xml": '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                                        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>'})
    assert "DOCTYPE or entities; refused" in views.view("bomb.docx", bomb).text
    # An external DTD alone: expat would not fetch it, but OOXML never has one, so it is refused too.
    external = zipped({"word/document.xml": '<!DOCTYPE w SYSTEM "http://attacker.example/x.dtd"><w/>'})
    assert "DOCTYPE or entities; refused" in views.view("ssrf.docx", external).text


def test_a_zip_bomb_and_zip_slip_are_reported_not_unpacked(monkeypatch) -> None:
    monkeypatch.setattr(views, "MAX_UNPACKED", 1024)
    bomb = zipped({"word/document.xml": b"\x00" * 4096})
    assert "possible ZIP bomb" in views.view("big.docx", bomb).text
    monkeypatch.undo()
    slip = zipped({"../../etc/cron.d/job": b"x", "run.ps1": b"x"})
    notes = views.view("upload.zip", slip).notes
    assert any("zip slip" in note for note in notes) and any("run.ps1" in note for note in notes)


class _RunsCode:
    def __reduce__(self):
        return (os.system, ("echo pwned",))


class _Evals:
    def __reduce__(self):
        return (eval, ("1+1",))


@pytest.mark.parametrize("protocol", [2, 4, 5])
def test_a_pickle_that_runs_code_is_named_without_being_loaded(protocol: int) -> None:
    for payload, needle in ((_RunsCode(), ".system (dangerous)"), (_Evals(), ".eval (dangerous)")):
        report = views.view("model.pkl", pickle.dumps(payload, protocol=protocol)).text
        assert report.startswith("1 pickle(s); DANGEROUS") and needle in report, report


def test_a_checkpoint_zip_has_its_embedded_pickle_scanned() -> None:
    checkpoint = zipped({"archive/data.pkl": pickle.dumps(_RunsCode(), protocol=2), "archive/version": b"3"})
    assert detect("pytorch_model.pt", checkpoint).format_id == "pytorch"
    notes = views.view("pytorch_model.pt", checkpoint).notes
    assert any("archive/data.pkl" in note and "DANGEROUS" in note for note in notes)


def test_safetensors_and_gguf_headers_are_read_and_checked() -> None:
    header = json.dumps({"w": {"dtype": "F32", "shape": [2, 2], "data_offsets": [0, 16]},
                         "b": {"dtype": "F32", "shape": [3], "data_offsets": [8, 20]},
                         "__metadata__": {"format": "pt"}}).encode()
    tensors = struct.pack("<Q", len(header)) + header + b"\x00" * 20
    result = views.view("model.safetensors", tensors)
    assert "2 tensors, 7 values" in result.text and any("w and b overlap" in n for n in result.notes)

    def text(value: str) -> bytes:
        return struct.pack("<Q", len(value.encode())) + value.encode()

    gguf = (b"GGUF" + struct.pack("<IQQ", 3, 0, 3) + text("general.architecture") + struct.pack("<I", 8)
            + text("llama") + text("llama.context_length") + struct.pack("<II", 4, 8192)
            + text("tokenizer.chat_template") + struct.pack("<I", 8) + text("{{ messages }}"))
    result = views.view("m.gguf", gguf).text
    assert "general.architecture = llama" in result and "llama.context_length = 8192" in result
    assert result.endswith("{{ messages }}")
    assert "cut short" in views.view("m.gguf", gguf[:-5]).text  # truncated header: reported, not crashed


def test_a_numpy_object_array_is_flagged_because_loading_it_runs_pickle() -> None:
    header = b"{'descr': '|O', 'fortran_order': False, 'shape': (3,), }".ljust(70)
    npy = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header
    assert any("DANGEROUS" in note for note in views.view("x.npy", npy).notes)


def test_sqlite_schema_is_read_from_a_read_only_copy(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE requirement (id TEXT PRIMARY KEY, text TEXT)")
    connection.execute("CREATE TRIGGER t AFTER INSERT ON requirement BEGIN SELECT 1; END")
    connection.commit()
    connection.close()  # Windows cannot delete a database file that is still open
    data = path.read_bytes()
    result = views.view("app.db", data).text
    assert "CREATE TABLE requirement" in result and "-- trigger t" in result
    assert path.read_bytes() == data


def test_certificates_are_summarized_and_their_private_keys_never_shown() -> None:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge.test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC))
            .not_valid_after(datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)).sign(key, hashes.SHA256()))
    secret = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    result = views.view("server.pem", cert.public_bytes(serialization.Encoding.PEM) + secret)
    assert secret.decode().split("\n")[1] not in result.text
    assert any("CN=forge.test" in note and "EXPIRED" in note and "self-signed" in note for note in result.notes)


@pytest.mark.parametrize("path", ["id_rsa", "deploy.key", "terraform.tfstate", "session.har", "store.p12"])
def test_secret_stores_are_never_shown(path: str) -> None:
    result = views.view(path, b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    assert "never shows it" in result.text and "abc" not in result.text


def test_formats_that_need_a_converter_say_so() -> None:
    assert any("converter" in note for note in views.view("report.pdf", b"%PDF-1.7\n").notes)


def test_a_partial_read_is_never_offered_as_editable() -> None:
    result = views.view("big.log", b"line\n" * 10, size=10**9, complete=False)
    assert not result.editable and "first 50 of 1,000,000,000 bytes" in result.notes[0]


def test_the_command_line_checks_files_and_fails_on_errors(tmp_path: Path, capsys) -> None:
    from forge.client_cli import main

    (tmp_path / "good.toml").write_text("x = 1\n")
    (tmp_path / "bad.json").write_text('{"a": 1,}\n')
    (tmp_path / ".env").write_text("API_TOKEN=abc123secret\n")
    assert main(["formats", "check", str(tmp_path / "good.toml")]) == 0
    assert main(["formats", "check", str(tmp_path / "good.toml"), str(tmp_path / "bad.json")]) == 1
    assert "bad.json:1: error: JSON syntax error" in capsys.readouterr().out
    assert main(["formats", "show", str(tmp_path / ".env")]) == 0
    assert "abc123secret" not in capsys.readouterr().out
    assert main(["formats", "list", "--area", "mainframe", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {"jcl", "cobol", "copybook", "rexx"} <= {row["format"] for row in listed}
