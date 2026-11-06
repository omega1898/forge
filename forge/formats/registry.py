"""The format catalogue: every file type FORGE recognizes, and how to treat it.

A format is found by content first (a ZIP is a ZIP whatever it is called),
then by exact file name (Makefile, Dockerfile), then by suffix (longest
first, so ".cdx.json" beats ".json"), then by a content sniff where one
suffix names two different things (.cls is a VBA class module or a LaTeX
class; .frm is a VBA form or a MySQL table definition). Text the catalogue
does not know is plain text; bytes it does not know are binary.

Each entry records only rules that change what FORGE does:

- kind: "text" is shown and edited as lines; "document" and "binary" are
  read by a bounded reader in views.py and never patched as text.
- newline: "\\r\\n" when the format needs CRLF (cmd.exe batch files). An edit
  keeps a file's own line endings; this decides them for a new file.
- legacy_encoding: the single-byte code page tried when the bytes are not
  UTF-8 (Java .properties are ISO-8859-1 when read by Properties.load).
- check: the syntax check in checks.py that an edited file must pass.
- secrets: values are often credentials, so the model sees them redacted by
  key (password = ...). Token patterns are redacted in every text file.
- sensitive: the file is a secret store and is never shown at all.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import PurePosixPath

AREAS = {
    "avionics": "avionics and safety-critical software (DO-178C, DO-254, ARINC)",
    "embedded": "embedded systems and firmware",
    "chips": "chip design (RTL, constraints, netlists, layout)",
    "mainframe": "mainframe (z/OS, COBOL, JCL, PL/I, REXX, assembler)",
    "databases": "databases, SQL and Microsoft Access",
    "office": "Microsoft Office and VBA",
    "documents": "documents and technical writing",
    "compliance": "compliance and assurance evidence",
    "security": "security testing and red teaming",
    "ai": "AI engineering and model security",
    "fullstack": "full-stack web applications",
    "cloud": "cloud, containers and infrastructure as code",
    "windows": "Windows administration and scripting",
    "general": "general software engineering",
}


@dataclass(frozen=True)
class FileFormat:
    format_id: str
    title: str
    family: str
    kind: str = "text"                    # text | document | binary
    suffixes: tuple[str, ...] = ()
    names: tuple[str, ...] = ()           # exact file names, compared ignoring case
    areas: tuple[str, ...] = ("general",)
    newline: str | None = None            # line ending for a new file when the format requires one
    legacy_encoding: str = "cp1252"       # tried when the bytes are not valid UTF-8
    check: str | None = None              # syntax check id (checks.py)
    secrets: bool = False                 # redact values by key in the model's view
    sensitive: bool = False               # a secret store: never shown
    reader: str | None = None             # bounded reader id (views.py) for documents and binaries
    bom_for_non_ascii: bool = False       # a new file with non-ASCII text gets a UTF-8 byte order mark


def _f(format_id: str, title: str, family: str, suffixes: str = "", names: str = "", areas: str = "general",
       **rules: object) -> FileFormat:
    return FileFormat(format_id=format_id, title=title, family=family, suffixes=tuple(suffixes.split()),
                      names=tuple(n.casefold() for n in names.split()), areas=tuple(areas.split()), **rules)


CRLF = "\r\n"

FORMATS: tuple[FileFormat, ...] = (
    # ---------------------------------------------------------------- source code
    _f("python", "Python", "source", ".py .pyi .pyw", areas="general ai fullstack security", check="python"),
    _f("c", "C", "source", ".c .h", areas="general embedded avionics security"),
    _f("cpp", "C++", "source", ".cpp .cc .cxx .c++ .hpp .hh .hxx .ipp .inl .tpp", areas="general embedded avionics"),
    _f("csharp", "C#", "source", ".cs .csx", areas="general windows fullstack"),
    _f("java", "Java", "source", ".java", areas="general fullstack"),
    _f("kotlin", "Kotlin", "source", ".kt .kts", areas="fullstack"),
    _f("scala", "Scala", "source", ".scala .sc", areas="general"),
    _f("go", "Go", "source", ".go", areas="cloud general"),
    _f("rust", "Rust", "source", ".rs", areas="general embedded security"),
    _f("javascript", "JavaScript", "source", ".js .mjs .cjs .jsx", areas="fullstack"),
    _f("typescript", "TypeScript", "source", ".ts .tsx .mts .cts", areas="fullstack"),
    _f("html", "HTML", "source", ".html .htm .xhtml", areas="fullstack documents"),
    _f("css", "CSS and preprocessors", "source", ".css .scss .sass .less", areas="fullstack"),
    _f("web_component", "Vue or Svelte component", "source", ".vue .svelte .astro", areas="fullstack"),
    _f("php", "PHP", "source", ".php .phtml", areas="fullstack security"),
    _f("ruby", "Ruby", "source", ".rb .rake .gemspec", names="Gemfile Rakefile", areas="fullstack"),
    _f("perl", "Perl", "source", ".pl .pm .t", areas="general mainframe"),
    _f("lua", "Lua", "source", ".lua", areas="general"),
    _f("r", "R", "source", ".r .rmd", areas="ai"),
    _f("julia", "Julia", "source", ".jl", areas="ai"),
    _f("matlab", "MATLAB or Octave", "source", ".m", areas="avionics embedded ai"),
    _f("objective_c", "Objective-C", "source", ".mm", areas="general"),
    _f("swift", "Swift", "source", ".swift", areas="general"),
    _f("haskell", "Haskell", "source", ".hs .lhs", areas="general"),
    _f("ocaml", "OCaml", "source", ".ml .mli", areas="general avionics"),
    _f("lisp", "Lisp or Scheme", "source", ".lisp .lsp .el .scm .ss .rkt .clj .cljs", areas="general"),
    _f("ada", "Ada or SPARK", "source", ".adb .ads .ada", areas="avionics embedded"),
    _f("gnat_project", "GNAT project (GPRbuild)", "build", ".gpr", areas="avionics embedded"),
    _f("ada_config", "Ada configuration pragmas", "config", ".adc", areas="avionics"),
    _f("fortran_fixed", "Fortran, fixed form (FORTRAN 77)", "source", ".f .for .f77 .ftn .fpp",
       areas="avionics general", check="fortran_fixed"),
    _f("fortran_free", "Fortran, free form (90 and later)", "source", ".f90 .f95 .f03 .f08 .f18",
       areas="avionics general", check="fortran_free"),
    _f("jovial", "JOVIAL J73", "source", ".jov .j73 .jovial", areas="avionics"),
    _f("assembly", "Assembly (GNU as, NASM, 68k, PowerPC, MIL-STD-1750A)", "source", ".s .asm .nasm .a51 .src",
       areas="embedded avionics security"),
    _f("cobol", "COBOL", "source", ".cbl .cob .cobol .pco .ccp", areas="mainframe", check="cobol_fixed"),
    _f("copybook", "COBOL copybook", "source", ".cpy .copy", areas="mainframe", check="cobol_fixed"),
    _f("pli", "PL/I", "source", ".pli .pl1", areas="mainframe"),
    _f("rexx", "REXX", "script", ".rex .rexx .exec", areas="mainframe"),
    _f("clist", "TSO CLIST", "script", ".clist", areas="mainframe"),
    _f("jcl", "JCL (job control language)", "script", ".jcl .job", areas="mainframe", check="jcl"),
    _f("hlasm", "IBM High Level Assembler", "source", ".mac .mlc .hlasm .bal", areas="mainframe"),
    _f("cics_bms", "CICS BMS map", "source", ".bms", areas="mainframe"),
    _f("ims_control", "IMS DBD or PSB source", "source", ".dbd .psb", areas="mainframe databases"),
    _f("rpg", "RPG (IBM i)", "source", ".rpg .rpgle .sqlrpgle .clle", areas="mainframe"),
    _f("sas", "SAS", "source", ".sas", areas="mainframe ai"),
    _f("easytrieve", "Easytrieve", "source", ".ezt", areas="mainframe"),
    _f("vba_module", "VBA or VB6 module", "source", ".bas", areas="office databases windows"),
    _f("vba_class", "VBA or VB6 class module", "source", ".cls", areas="office databases windows"),
    _f("vba_form", "VBA or VB6 form", "source", ".frm .dsr", areas="office databases windows"),
    _f("vbnet", "Visual Basic .NET", "source", ".vb", areas="windows"),
    _f("vbscript", "VBScript", "script", ".vbs .vbe", areas="windows security"),
    _f("sql", "SQL (any dialect)", "source", ".sql .ddl .dml .psql .pgsql .tsql .plsql .pks .pkb .prc .fnc .trg .vw",
       areas="databases mainframe fullstack"),
    _f("dbml", "DBML schema", "source", ".dbml", areas="databases"),
    _f("graphql", "GraphQL", "source", ".graphql .gql", areas="fullstack"),
    _f("protobuf_schema", "Protocol Buffers schema", "source", ".proto", areas="cloud fullstack"),
    _f("thrift", "Thrift or Avro schema", "source", ".thrift .avsc .avdl", areas="cloud databases"),
    _f("vhdl", "VHDL", "source", ".vhd .vhdl", areas="chips avionics"),
    _f("verilog", "Verilog or SystemVerilog", "source", ".v .vh .sv .svh", areas="chips"),
    _f("hw_constraints", "Timing and pin constraints (SDC, XDC, UCF, PCF)", "config", ".sdc .xdc .ucf .pcf .qsf",
       areas="chips"),
    _f("tcl", "Tcl", "script", ".tcl .tk", areas="chips"),
    _f("spice", "SPICE netlist", "source", ".sp .spi .spice .cir .ckt", areas="chips"),
    _f("liberty", "Liberty timing library", "source", ".lib", areas="chips"),
    _f("lef_def", "LEF or DEF physical design", "source", ".lef .def", areas="chips"),
    _f("chisel_scala", "Chisel (Scala) hardware", "source", ".fir", areas="chips"),
    # ---------------------------------------------------------------- scripts
    _f("shell", "POSIX shell or Bash", "script", ".sh .bash .zsh .ksh", names=".bashrc .profile .zshrc .bash_profile",
       areas="general cloud security", newline="\n"),
    _f("batch", "Windows batch (cmd.exe)", "script", ".bat .cmd", areas="windows", newline=CRLF, check="batch",
       legacy_encoding="cp437"),
    _f("powershell", "PowerShell", "script", ".ps1 .psm1 .psd1", areas="windows security cloud", check="powershell",
       bom_for_non_ascii=True),
    _f("windows_registry", "Windows registry export", "config", ".reg", areas="windows", newline=CRLF, secrets=True),
    _f("windows_inf", "Windows setup information (INF)", "config", ".inf", areas="windows", newline=CRLF),
    _f("windows_script_host", "Windows Script File", "script", ".wsf .wsc", areas="windows", check="xml"),
    _f("autohotkey", "AutoHotkey", "script", ".ahk", areas="windows"),
    _f("lauterbach", "Lauterbach TRACE32 script", "script", ".cmm", areas="embedded avionics"),
    _f("gdb_script", "GDB script", "script", ".gdb", names=".gdbinit", areas="embedded security"),
    # ---------------------------------------------------------------- build
    _f("makefile", "Makefile", "build", ".mk .mak", names="Makefile GNUmakefile", areas="general embedded",
       check="makefile"),
    _f("cmake", "CMake", "build", ".cmake", names="CMakeLists.txt", areas="general embedded"),
    _f("meson", "Meson", "build", "", names="meson.build meson_options.txt", areas="general"),
    _f("bazel", "Bazel or Starlark", "build", ".bzl .bazel", names="BUILD WORKSPACE MODULE.bazel .bazelrc",
       areas="general cloud"),
    _f("ninja", "Ninja", "build", ".ninja", areas="general"),
    _f("gradle", "Gradle", "build", ".gradle", areas="fullstack"),
    _f("msbuild", "MSBuild project", "build", ".csproj .vbproj .vcxproj .fsproj .sqlproj .props .targets .proj",
       areas="windows", check="xml"),
    _f("visual_studio_solution", "Visual Studio solution", "build", ".sln", areas="windows", newline=CRLF),
    _f("dockerfile", "Dockerfile or Containerfile", "build", ".dockerfile", names="Dockerfile Containerfile",
       areas="cloud fullstack security", check="dockerfile"),
    _f("jenkinsfile", "Jenkins pipeline (Groovy)", "build", ".groovy .jenkinsfile", names="Jenkinsfile",
       areas="cloud"),
    _f("linker_script", "Linker script", "build", ".ld .lds .lcf .icf .scat", areas="embedded avionics"),
    _f("terraform", "Terraform or HCL", "config", ".tf .hcl .tfvars", areas="cloud security", secrets=True),
    _f("nix", "Nix", "build", ".nix", areas="cloud"),
    # ---------------------------------------------------------------- configuration
    _f("json", "JSON", "config", ".json .webmanifest .geojson", areas="general fullstack cloud ai", check="json",
       secrets=True),
    _f("jsonc", "JSON with comments", "config", ".jsonc .json5",
       names="tsconfig.json jsconfig.json devcontainer.json .eslintrc.json settings.json launch.json tasks.json "
             "extensions.json",
       areas="fullstack general", secrets=True),
    _f("jsonl", "JSON Lines", "data", ".jsonl .ndjson", areas="ai databases security", check="jsonl"),
    _f("yaml", "YAML", "config", ".yaml .yml", names=".clang-format .clang-tidy",
       areas="cloud fullstack ai security compliance", check="yaml", secrets=True),
    _f("toml", "TOML", "config", ".toml", names="Pipfile", areas="general ai", check="toml", secrets=True),
    _f("ini", "INI", "config", ".ini",
       names="setup.cfg tox.ini .editorconfig .flake8 .pylintrc .coveragerc mypy.ini pytest.ini .pypirc .npmrc "
             ".gitconfig .yarnrc .isort.cfg",
       areas="general windows", check="ini", secrets=True),
    _f("systemd_unit", "systemd unit", "config", ".service .socket .timer .mount .target .path .slice",
       areas="cloud", check="systemd", secrets=True),
    _f("conf", "Configuration file (.conf, .cfg, .cnf)", "config", ".conf .cfg .cnf .rc", names="hosts",
       areas="general cloud security", secrets=True),
    _f("java_properties", "Java properties", "config", ".properties", areas="fullstack databases",
       check="properties", secrets=True, legacy_encoding="latin-1"),
    _f("dotenv", "Environment file (.env)", "config", ".env", names=".env .envrc .flaskenv",
       areas="fullstack cloud", check="dotenv", secrets=True),
    _f("xml", "XML", "config", ".xml .xsd .xsl .xslt .wsdl .xaml .resx .plist .manifest .config .xliff .xlf .dita .ditamap "
       ".rss .atom .kml .gpx", areas="general windows fullstack", check="xml", secrets=True),
    _f("svg", "SVG image", "document", ".svg", areas="fullstack documents", check="xml"),
    _f("odbc_dsn", "ODBC data source", "config", ".dsn", areas="databases windows", check="ini", secrets=True),
    _f("oracle_net", "Oracle Net configuration (tnsnames.ora)", "config", ".ora", areas="databases", secrets=True),
    _f("ssh_config", "SSH client configuration", "config", "", names="ssh_config sshd_config known_hosts",
       areas="security cloud"),
    _f("ignore_rules", "Ignore and attribute rules", "config", "",
       names=".gitignore .dockerignore .gitattributes .npmignore .eslintignore .prettierignore CODEOWNERS",
       areas="general"),
    _f("credentials_file", "Credentials file", "config", ".netrc .pgpass .htpasswd .git-credentials .s3cfg",
       names=".netrc _netrc .pgpass .htpasswd .git-credentials credentials .s3cfg .dockercfg",
       areas="security cloud databases", secrets=True),
    _f("device_tree", "Device tree source", "config", ".dts .dtsi", areas="embedded"),
    _f("can_database", "CAN database (DBC)", "config", ".dbc", areas="embedded avionics"),
    _f("asap2", "ASAM MCD-2 MC calibration (A2L)", "config", ".a2l", areas="embedded"),
    _f("cmsis_svd", "CMSIS System View Description", "config", ".svd", areas="embedded", check="xml"),
    _f("autosar", "AUTOSAR XML", "config", ".arxml", areas="embedded", check="xml"),
    _f("kubernetes_kustomize", "Helm template", "config", ".tpl", areas="cloud"),
    # ---------------------------------------------------------------- documents and text
    _f("markdown", "Markdown", "documents", ".md .markdown .mdx .mdown", names="README LICENSE CHANGELOG NOTICE",
       areas="documents general"),
    _f("restructuredtext", "reStructuredText", "documents", ".rst", areas="documents general"),
    _f("asciidoc", "AsciiDoc", "documents", ".adoc .asciidoc .asc", areas="documents"),
    _f("latex", "LaTeX or BibTeX", "documents", ".tex .ltx .sty .bib .bst", areas="documents"),
    _f("plain_text", "Plain text", "documents", ".txt .text .log .out .lst .map .rpt .org .1 .man",
       areas="general"),
    _f("csv", "CSV", "data", ".csv", areas="databases office ai compliance", check="csv"),
    _f("tsv", "TSV", "data", ".tsv .tab", areas="databases ai", check="tsv"),
    _f("email_message", "Email message (RFC 5322)", "documents", ".eml .mbox", areas="security documents",
       secrets=True),
    _f("calendar_vcard", "iCalendar or vCard", "documents", ".ics .vcf", areas="documents"),
    _f("subtitles", "Subtitles", "documents", ".srt .vtt", areas="documents"),
    _f("rtf", "Rich Text Format", "documents", ".rtf", kind="document", reader="needs_converter",
       areas="documents office"),
    _f("docx", "Word document", "documents", ".docx .docm .dotx .dotm", kind="document", reader="docx",
       areas="documents office compliance avionics"),
    _f("xlsx", "Excel workbook", "documents", ".xlsx .xlsm .xltx .xltm", kind="document", reader="xlsx",
       areas="office databases compliance avionics"),
    _f("pptx", "PowerPoint presentation", "documents", ".pptx .pptm .potx .ppsx", kind="document", reader="pptx",
       areas="documents office"),
    _f("visio", "Visio drawing", "documents", ".vsdx .vsdm", kind="document", reader="zip_listing",
       areas="documents avionics"),
    _f("odf_text", "OpenDocument text", "documents", ".odt .ott", kind="document", reader="odf",
       areas="documents"),
    _f("odf_spreadsheet", "OpenDocument spreadsheet", "documents", ".ods .ots", kind="document", reader="odf",
       areas="documents databases"),
    _f("odf_presentation", "OpenDocument presentation", "documents", ".odp .otp", kind="document", reader="odf",
       areas="documents"),
    _f("epub", "EPUB book", "documents", ".epub", kind="document", reader="zip_listing", areas="documents"),
    _f("pdf", "PDF", "documents", ".pdf", kind="document", reader="needs_converter",
       areas="documents compliance avionics security"),
    _f("ole_document", "Legacy Office document (Word, Excel, PowerPoint 97-2003)", "documents",
       ".doc .dot .xls .xlt .ppt .pot .pps", kind="document", reader="ole", areas="office documents"),
    _f("outlook_message", "Outlook message", "documents", ".msg .oft", kind="document", reader="ole",
       areas="office security"),
    _f("windows_installer", "Windows Installer package", "binary", ".msi .msp .mst", kind="binary", reader="ole",
       areas="windows security"),
    _f("visio_legacy", "Visio drawing (legacy)", "documents", ".vsd .vss .vst", kind="document", reader="ole",
       areas="documents"),
    _f("project_plan", "Microsoft Project plan", "documents", ".mpp", kind="document", reader="ole",
       areas="documents"),
    _f("onenote", "OneNote section", "documents", ".one", kind="binary", areas="documents"),
    _f("publisher", "Publisher document", "documents", ".pub", kind="document", reader="ole", areas="documents"),
    # ---------------------------------------------------------------- notebooks, AI and models
    _f("jupyter", "Jupyter notebook", "ai", ".ipynb", areas="ai security", check="ipynb"),
    _f("chat_template", "Jinja or chat template", "ai", ".jinja .jinja2 .j2", areas="ai security fullstack"),
    _f("safetensors", "safetensors model weights", "ai", ".safetensors", kind="binary", reader="safetensors",
       areas="ai security"),
    _f("gguf", "GGUF model (llama.cpp)", "ai", ".gguf", kind="binary", reader="gguf", areas="ai security"),
    _f("pickle", "Python pickle", "ai", ".pkl .pickle .joblib .dill .sav .p", kind="binary", reader="pickle",
       areas="ai security"),
    _f("pytorch", "PyTorch checkpoint", "ai", ".pt .pth .ckpt .tar.pth", kind="binary", reader="pytorch",
       areas="ai security"),
    _f("keras", "Keras model", "ai", ".keras", kind="binary", reader="keras", areas="ai security"),
    _f("onnx", "ONNX model", "ai", ".onnx .ort", kind="binary", areas="ai security"),
    _f("tflite", "TensorFlow Lite model", "ai", ".tflite", kind="binary", areas="ai embedded"),
    _f("tensorflow_graph", "TensorFlow graph or SavedModel", "ai", ".pb .pbtxt", kind="binary", areas="ai"),
    _f("coreml", "Core ML model", "ai", ".mlmodel .mlpackage", kind="binary", areas="ai"),
    _f("numpy", "NumPy array", "ai", ".npy .npz", kind="binary", reader="numpy", areas="ai security"),
    _f("hdf5", "HDF5 (including legacy Keras .h5)", "ai", ".h5 .hdf5 .he5 .nc", kind="binary", areas="ai"),
    _f("matlab_data", "MATLAB data", "ai", ".mat", kind="binary", areas="ai avionics"),
    _f("simulink", "Simulink model", "avionics", ".slx .mdl", kind="binary", reader="zip_listing",
       areas="avionics embedded"),
    # ---------------------------------------------------------------- data and databases
    _f("sqlite", "SQLite database", "data", ".sqlite .sqlite3 .db3 .db .s3db", kind="binary", reader="sqlite",
       areas="databases fullstack ai security"),
    _f("access", "Microsoft Access database", "data", ".mdb .accdb .accde .mde .accdr", kind="binary",
       reader="access", areas="databases office"),
    _f("access_lock", "Access lock file", "data", ".ldb .laccdb", kind="binary", areas="databases office"),
    _f("sql_server_data", "SQL Server data or backup", "data", ".mdf .ndf .ldf .bak", kind="binary",
       areas="databases"),
    _f("sql_server_package", "SQL Server data-tier package", "data", ".bacpac .dacpac", kind="binary",
       reader="zip_listing", areas="databases"),
    _f("parquet", "Apache Parquet", "data", ".parquet .pq", kind="binary", areas="databases ai"),
    _f("arrow", "Apache Arrow or Feather", "data", ".arrow .feather .ipc", kind="binary", areas="databases ai"),
    _f("avro", "Apache Avro data", "data", ".avro", kind="binary", areas="databases cloud"),
    _f("orc", "Apache ORC", "data", ".orc", kind="binary", areas="databases"),
    _f("bson", "BSON (MongoDB)", "data", ".bson", kind="binary", areas="databases"),
    _f("redis_dump", "Redis dump", "data", ".rdb", kind="binary", areas="databases"),
    # ---------------------------------------------------------------- security and compliance
    _f("sarif", "SARIF static analysis results", "compliance", ".sarif .sarif.json", areas="security compliance",
       check="sarif"),
    _f("cyclonedx", "CycloneDX SBOM", "compliance", ".cdx.json .cdx.xml .bom.json .bom.xml", names="bom.json bom.xml",
       areas="security compliance", check="json"),
    _f("spdx", "SPDX SBOM", "compliance", ".spdx .spdx.json .spdx.yaml .spdx.rdf", areas="security compliance"),
    _f("reqif", "ReqIF requirements interchange", "compliance", ".reqif", areas="avionics compliance", check="xml"),
    _f("reqif_archive", "ReqIF archive", "compliance", ".reqifz", kind="document", reader="zip_listing",
       areas="avionics compliance"),
    _f("uml_xmi", "UML or SysML model (XMI)", "compliance", ".xmi .uml .sysml", areas="avionics", check="xml"),
    _f("yara", "YARA rules", "security", ".yar .yara", areas="security"),
    _f("ids_rules", "Snort or Suricata rules", "security", ".rules", areas="security"),
    _f("http_archive", "HTTP archive (HAR)", "security", ".har", areas="security fullstack", sensitive=True),
    _f("terraform_state", "Terraform state", "cloud", ".tfstate .tfstate.backup", areas="cloud security",
       sensitive=True),
    _f("vpn_config", "VPN configuration", "security", ".ovpn", areas="security", secrets=True),
    _f("pem", "PEM certificate, key or request", "security", ".pem .crt .cer .csr .ca-bundle",
       areas="security cloud", reader="pem"),
    _f("private_key", "Private key", "security", ".key .ppk",
       names="id_rsa id_dsa id_ecdsa id_ed25519 id_ecdsa_sk id_ed25519_sk", areas="security cloud", sensitive=True),
    _f("pgp", "OpenPGP data", "security", ".gpg .pgp .sig", kind="binary", areas="security", sensitive=True),
    _f("keystore", "Key or password store", "security", ".p12 .pfx .jks .jceks .keystore .bks .kdbx .kdb .p8 .der",
       kind="binary", areas="security cloud", sensitive=True),
    _f("packet_capture", "Packet capture", "security", ".pcap .pcapng .cap", kind="binary", reader="pcap",
       areas="security embedded"),
    # ---------------------------------------------------------------- firmware, executables, archives
    _f("intel_hex", "Intel HEX firmware image", "embedded", ".hex .ihex .ihx .h86 .mcs", areas="embedded avionics",
       check="intel_hex"),
    _f("srecord", "Motorola S-record firmware image", "embedded", ".srec .s19 .s28 .s37 .mot .mhx .exo",
       areas="embedded avionics", check="srecord"),
    _f("raw_image", "Raw binary or firmware image", "embedded", ".bin .img .rom .fw .dat .raw", kind="binary",
       areas="embedded avionics security"),
    _f("device_tree_blob", "Device tree blob", "embedded", ".dtb .dtbo", kind="binary", areas="embedded"),
    _f("elf", "ELF executable or object", "binary", ".elf .axf .o .so .ko .prx", kind="binary", reader="elf",
       areas="embedded avionics security"),
    _f("pe", "Windows executable (PE)", "binary", ".exe .dll .sys .ocx .scr .efi .cpl .drv .mui", kind="binary",
       reader="pe", areas="windows security"),
    _f("macho", "Mach-O executable", "binary", ".dylib .bundle", kind="binary", areas="security"),
    _f("java_class", "Java class or archive", "binary", ".class .jar .war .ear .aar", kind="binary",
       reader="zip_listing", areas="fullstack security"),
    _f("android_package", "Android package", "binary", ".apk .aab .dex", kind="binary", reader="zip_listing",
       areas="security"),
    _f("wasm", "WebAssembly module", "binary", ".wasm", kind="binary", areas="fullstack security"),
    _f("static_library", "Static library (ar archive)", "binary", ".a", kind="binary",
       areas="embedded avionics"),
    _f("gdsii", "GDSII or OASIS layout", "chips", ".gds .gdsii .gds2 .oas .oasis", kind="binary", areas="chips"),
    _f("zip", "ZIP archive", "archive", ".zip .whl .nupkg .vsix .xpi .ipa", kind="binary", reader="zip_listing",
       areas="general"),
    _f("tar", "tar archive", "archive", ".tar", kind="binary", areas="general"),
    _f("compressed", "Compressed data (gzip, bzip2, xz, zstd, 7z, RAR)", "archive",
       ".gz .tgz .bz2 .xz .txz .zst .7z .rar .lz4 .lzma .z", kind="binary", areas="general"),
    _f("disk_image", "Disk or VM image", "archive", ".iso .vhdx .vmdk .qcow2 .ova .ovf .wim", kind="binary",
       areas="cloud security"),
    _f("tso_xmit", "TSO TRANSMIT (XMIT) file", "mainframe", ".xmit", kind="binary", areas="mainframe"),
    _f("image", "Image", "media", ".png .jpg .jpeg .gif .bmp .tif .tiff .webp .ico .icns .heic .psd", kind="binary",
       areas="documents fullstack"),
    _f("audio_video", "Audio or video", "media", ".mp3 .wav .flac .ogg .m4a .mp4 .mkv .mov .avi .webm",
       kind="binary", areas="documents"),
    _f("font", "Font", "media", ".ttf .otf .woff .woff2 .eot", kind="binary", areas="fullstack documents"),
    _f("text", "Text (unrecognized)", "text", areas="general"),
    _f("binary", "Binary (unrecognized)", "binary", kind="binary", areas="general"),
)

BY_ID = {fmt.format_id: fmt for fmt in FORMATS}
_BY_NAME = {name: fmt for fmt in FORMATS for name in fmt.names}
_SUFFIXES = sorted(((suffix, fmt) for fmt in FORMATS for suffix in fmt.suffixes), key=lambda item: -len(item[0]))
# Suffixes ZIP-based formats use; a ZIP named otherwise is a plain archive.
_ZIP_FORMATS = ("docx", "xlsx", "pptx", "visio", "odf_text", "odf_spreadsheet", "odf_presentation", "epub",
                "keras", "pytorch", "numpy", "simulink", "sql_server_package", "reqif_archive", "java_class",
                "android_package", "zip")
_OLE_FORMATS = ("ole_document", "outlook_message", "windows_installer", "visio_legacy", "project_plan", "publisher")
_PICKLE_SUFFIXES = (".pkl", ".pickle", ".joblib", ".dill", ".sav", ".p", ".pt", ".pth", ".ckpt", ".bin", ".model")

_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"%PDF-", "pdf"),
    (0, b"{\\rtf", "rtf"),
    (0, b"SQLite format 3\x00", "sqlite"),
    (4, b"Standard Jet DB", "access"),
    (4, b"Standard ACE DB", "access"),
    (0, b"\x7fELF", "elf"),
    (0, b"GGUF", "gguf"),
    (0, b"\x93NUMPY", "numpy"),
    (0, b"PAR1", "parquet"),
    (0, b"ARROW1", "arrow"),
    (0, b"Obj\x01", "avro"),
    (0, b"\x89HDF\r\n\x1a\n", "hdf5"),
    (0, b"\xd4\xc3\xb2\xa1", "packet_capture"), (0, b"\xa1\xb2\xc3\xd4", "packet_capture"),
    (0, b"\x4d\x3c\xb2\xa1", "packet_capture"), (0, b"\xa1\xb2\x3c\x4d", "packet_capture"),
    (0, b"\x0a\x0d\x0d\x0a", "packet_capture"),
    (0, b"\xd0\x0d\xfe\xed", "device_tree_blob"),
    (0, b"!<arch>\n", "static_library"),
    (0, b"\x00asm", "wasm"),
    (4, b"TFL3", "tflite"),
    (0, b"REDIS", "redis_dump"),
    (0, b"\x89PNG\r\n\x1a\n", "image"), (0, b"\xff\xd8\xff", "image"), (0, b"GIF8", "image"),
    (0, b"\x1f\x8b", "compressed"), (0, b"BZh", "compressed"), (0, b"\xfd7zXZ\x00", "compressed"),
    (0, b"7z\xbc\xaf\x27\x1c", "compressed"), (0, b"Rar!\x1a\x07", "compressed"), (0, b"\x28\xb5\x2f\xfd", "compressed"),
    (257, b"ustar", "tar"),
    (0, b"\xfe\xed\xfa\xce", "macho"), (0, b"\xfe\xed\xfa\xcf", "macho"), (0, b"\xce\xfa\xed\xfe", "macho"),
    (0, b"\xcf\xfa\xed\xfe", "macho"),
    (0, b"\x00\x06\x00\x02", "gdsii"),
    (0, b"conectix", "disk_image"),  # a Hyper-V .vhd; VHDL sources share the suffix
    (0, b"QFI\xfb", "disk_image"),
)


def _suffix_format(name: str) -> FileFormat | None:
    for suffix, fmt in _SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return fmt
    return None


def _by_suffix_among(name: str, candidates: tuple[str, ...], fallback: str) -> FileFormat:
    found = _suffix_format(name)
    return found if found is not None and found.format_id in candidates else BY_ID[fallback]


def _is_pe(head: bytes) -> bool:
    if not head.startswith(b"MZ") or len(head) < 0x40:
        return False
    (offset,) = struct.unpack_from("<I", head, 0x3C)
    return offset + 4 <= len(head) and head[offset:offset + 4] == b"PE\x00\x00"


def _is_safetensors(head: bytes) -> bool:
    if len(head) < 10:
        return False
    (length,) = struct.unpack_from("<Q", head, 0)
    return 2 <= length <= 100 * 1024 * 1024 and head[8:9] == b"{"


def _sniff_text(fmt: FileFormat, head: bytes) -> FileFormat:
    """One suffix, two meanings: decide by the first bytes."""
    start = head[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if fmt.format_id == "vba_class" and (b"\\ProvidesClass" in start or b"\\NeedsTeXFormat" in start):
        return BY_ID["latex"]
    if fmt.format_id == "matlab" and (start.startswith((b"#import", b"@interface", b"@implementation"))
                                      or b"\n@interface" in start):
        return BY_ID["objective_c"]
    if fmt.format_id == "json":
        if b'"bomFormat"' in start and b"CycloneDX" in start:
            return BY_ID["cyclonedx"]
        if b'"spdxVersion"' in start:
            return BY_ID["spdx"]
        if b"sarif" in start[:512] and b'"runs"' in start:
            return BY_ID["sarif"]
    return fmt


def detect(path: str, head: bytes = b"") -> FileFormat:
    """The format of a file, from its path and its first bytes (4 KiB is enough)."""
    name = PurePosixPath(path).name.casefold()
    for offset, magic, format_id in _MAGIC:
        if head[offset:offset + len(magic)] == magic:
            return BY_ID[format_id]
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _by_suffix_among(name, _ZIP_FORMATS, "zip")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _by_suffix_among(name, _OLE_FORMATS, "ole_document")
    if head.startswith(b"\xca\xfe\xba\xbe"):  # Java class file, or a universal Mach-O binary
        return BY_ID["java_class"] if name.endswith(".class") else BY_ID["macho"]
    if _is_pe(head):
        return BY_ID["pe"]
    if _is_safetensors(head):
        return BY_ID["safetensors"]
    suffix_format = _suffix_format(name)
    if head[:1] == b"\x80" and head[1:2] in (b"\x02", b"\x03", b"\x04", b"\x05") and (
            name.endswith(_PICKLE_SUFFIXES) or "." not in name):
        return BY_ID["pickle"]
    if name in _BY_NAME:
        found = _BY_NAME[name]
    elif name.startswith(".env"):
        found = BY_ID["dotenv"]
    elif name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        found = BY_ID["dockerfile"]
    elif suffix_format is not None:
        found = suffix_format
    else:
        found = None
    looks_binary = b"\x00" in head[:8192] and not head.startswith((b"\xff\xfe", b"\xfe\xff"))
    if found is None:
        return BY_ID["binary"] if looks_binary else BY_ID["text"]
    if found.format_id == "raw_image" and not looks_binary and head:
        return BY_ID["text"]  # .dat and .bin hold text as often as bytes
    if found.kind == "text" and looks_binary:
        # A text suffix on binary bytes: a MySQL .frm, an object file named .lib, a compiled .mo.
        return BY_ID["binary"]
    return _sniff_text(found, head) if found.kind == "text" else found


def formats_for_area(area: str) -> list[FileFormat]:
    return [fmt for fmt in FORMATS if area in fmt.areas]


def text_suffixes() -> tuple[str, ...]:
    """Every suffix whose files are text, for the search index."""
    return tuple(sorted({suffix for fmt in FORMATS if fmt.kind == "text" and not fmt.sensitive
                         for suffix in fmt.suffixes}))


def text_names() -> tuple[str, ...]:
    return tuple(sorted({name for fmt in FORMATS if fmt.kind == "text" and not fmt.sensitive for name in fmt.names}))
