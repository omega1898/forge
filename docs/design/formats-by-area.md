# File formats by work area

Generated from `forge/formats/registry.py`. The same lists come from
`forge formats list --area <area>` (add `--json` for data). A format can belong to
several areas, so it can appear more than once.

"Check" names the syntax check an edited file must pass; "-" means FORGE has
no parser for that format and does not claim one.

### Avionics and safety-critical software (DO-178C, DO-254, ARINC)

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| C | `.c` `.h` | text | - |
| C++ | `.cpp` `.cc` `.cxx` `.c++` `.hpp` `.hh` ... | text | - |
| MATLAB or Octave | `.m` | text | - |
| OCaml | `.ml` `.mli` | text | - |
| Ada or SPARK | `.adb` `.ads` `.ada` | text | - |
| GNAT project (GPRbuild) | `.gpr` | text | - |
| Ada configuration pragmas | `.adc` | text | - |
| Fortran, fixed form (FORTRAN 77) | `.f` `.for` `.f77` `.ftn` `.fpp` | text | fortran_fixed |
| Fortran, free form (90 and later) | `.f90` `.f95` `.f03` `.f08` `.f18` | text | fortran_free |
| JOVIAL J73 | `.jov` `.j73` `.jovial` | text | - |
| Assembly (GNU as, NASM, 68k, PowerPC, MIL-STD-1750A) | `.s` `.asm` `.nasm` `.a51` `.src` | text | - |
| VHDL | `.vhd` `.vhdl` | text | - |
| Lauterbach TRACE32 script | `.cmm` | text | - |
| Linker script | `.ld` `.lds` `.lcf` `.icf` `.scat` | text | - |
| CAN database (DBC) | `.dbc` | text | - |
| Word document | `.docx` `.docm` `.dotx` `.dotm` | summarized by the docx reader | - |
| Excel workbook | `.xlsx` `.xlsm` `.xltx` `.xltm` | summarized by the xlsx reader | - |
| Visio drawing | `.vsdx` `.vsdm` | summarized by the zip_listing reader | - |
| PDF | `.pdf` | identified; text needs a converter | - |
| MATLAB data | `.mat` | identified only | - |
| Simulink model | `.slx` `.mdl` | summarized by the zip_listing reader | - |
| ReqIF requirements interchange | `.reqif` | text | xml |
| ReqIF archive | `.reqifz` | summarized by the zip_listing reader | - |
| UML or SysML model (XMI) | `.xmi` `.uml` `.sysml` | text | xml |
| Intel HEX firmware image | `.hex` `.ihex` `.ihx` `.h86` `.mcs` | text | intel_hex |
| Motorola S-record firmware image | `.srec` `.s19` `.s28` `.s37` `.mot` `.mhx` ... | text | srecord |
| Raw binary or firmware image | `.bin` `.img` `.rom` `.fw` `.dat` `.raw` | identified only | - |
| ELF executable or object | `.elf` `.axf` `.o` `.so` `.ko` `.prx` | summarized by the elf reader | - |
| Static library (ar archive) | `.a` | identified only | - |

### Embedded systems and firmware

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| C | `.c` `.h` | text | - |
| C++ | `.cpp` `.cc` `.cxx` `.c++` `.hpp` `.hh` ... | text | - |
| Rust | `.rs` | text | - |
| MATLAB or Octave | `.m` | text | - |
| Ada or SPARK | `.adb` `.ads` `.ada` | text | - |
| GNAT project (GPRbuild) | `.gpr` | text | - |
| Assembly (GNU as, NASM, 68k, PowerPC, MIL-STD-1750A) | `.s` `.asm` `.nasm` `.a51` `.src` | text | - |
| Lauterbach TRACE32 script | `.cmm` | text | - |
| GDB script | `.gdb` `.gdbinit` | text | - |
| Makefile | `.mk` `.mak` `makefile` `gnumakefile` | text | makefile |
| CMake | `.cmake` `cmakelists.txt` | text | - |
| Linker script | `.ld` `.lds` `.lcf` `.icf` `.scat` | text | - |
| Device tree source | `.dts` `.dtsi` | text | - |
| CAN database (DBC) | `.dbc` | text | - |
| ASAM MCD-2 MC calibration (A2L) | `.a2l` | text | - |
| CMSIS System View Description | `.svd` | text | xml |
| AUTOSAR XML | `.arxml` | text | xml |
| TensorFlow Lite model | `.tflite` | identified only | - |
| Simulink model | `.slx` `.mdl` | summarized by the zip_listing reader | - |
| Packet capture | `.pcap` `.pcapng` `.cap` | summarized by the pcap reader | - |
| Intel HEX firmware image | `.hex` `.ihex` `.ihx` `.h86` `.mcs` | text | intel_hex |
| Motorola S-record firmware image | `.srec` `.s19` `.s28` `.s37` `.mot` `.mhx` ... | text | srecord |
| Raw binary or firmware image | `.bin` `.img` `.rom` `.fw` `.dat` `.raw` | identified only | - |
| Device tree blob | `.dtb` `.dtbo` | identified only | - |
| ELF executable or object | `.elf` `.axf` `.o` `.so` `.ko` `.prx` | summarized by the elf reader | - |
| Static library (ar archive) | `.a` | identified only | - |

### Chip design (RTL, constraints, netlists, layout)

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| VHDL | `.vhd` `.vhdl` | text | - |
| Verilog or SystemVerilog | `.v` `.vh` `.sv` `.svh` | text | - |
| Timing and pin constraints (SDC, XDC, UCF, PCF) | `.sdc` `.xdc` `.ucf` `.pcf` `.qsf` | text | - |
| Tcl | `.tcl` `.tk` | text | - |
| SPICE netlist | `.sp` `.spi` `.spice` `.cir` `.ckt` | text | - |
| Liberty timing library | `.lib` | text | - |
| LEF or DEF physical design | `.lef` `.def` | text | - |
| Chisel (Scala) hardware | `.fir` | text | - |
| GDSII or OASIS layout | `.gds` `.gdsii` `.gds2` `.oas` `.oasis` | identified only | - |

### Mainframe (z/OS, COBOL, JCL, PL/I, REXX, assembler)

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| Perl | `.pl` `.pm` `.t` | text | - |
| COBOL | `.cbl` `.cob` `.cobol` `.pco` `.ccp` | text | cobol_fixed |
| COBOL copybook | `.cpy` `.copy` | text | cobol_fixed |
| PL/I | `.pli` `.pl1` | text | - |
| REXX | `.rex` `.rexx` `.exec` | text | - |
| TSO CLIST | `.clist` | text | - |
| JCL (job control language) | `.jcl` `.job` | text | jcl |
| IBM High Level Assembler | `.mac` `.mlc` `.hlasm` `.bal` | text | - |
| CICS BMS map | `.bms` | text | - |
| IMS DBD or PSB source | `.dbd` `.psb` | text | - |
| RPG (IBM i) | `.rpg` `.rpgle` `.sqlrpgle` `.clle` | text | - |
| SAS | `.sas` | text | - |
| Easytrieve | `.ezt` | text | - |
| SQL (any dialect) | `.sql` `.ddl` `.dml` `.psql` `.pgsql` `.tsql` ... | text | - |
| TSO TRANSMIT (XMIT) file | `.xmit` | identified only | - |

### Databases, SQL and Microsoft Access

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| IMS DBD or PSB source | `.dbd` `.psb` | text | - |
| VBA or VB6 module | `.bas` | text | - |
| VBA or VB6 class module | `.cls` | text | - |
| VBA or VB6 form | `.frm` `.dsr` | text | - |
| SQL (any dialect) | `.sql` `.ddl` `.dml` `.psql` `.pgsql` `.tsql` ... | text | - |
| DBML schema | `.dbml` | text | - |
| Thrift or Avro schema | `.thrift` `.avsc` `.avdl` | text | - |
| JSON Lines | `.jsonl` `.ndjson` | text | jsonl |
| Java properties | `.properties` | text, secrets masked by key | properties |
| ODBC data source | `.dsn` | text, secrets masked by key | ini |
| Oracle Net configuration (tnsnames.ora) | `.ora` | text, secrets masked by key | - |
| Credentials file | `.netrc` `.pgpass` `.htpasswd` `.git-credentials` `.s3cfg` `.netrc` ... | text, secrets masked by key | - |
| CSV | `.csv` | text | csv |
| TSV | `.tsv` `.tab` | text | tsv |
| Excel workbook | `.xlsx` `.xlsm` `.xltx` `.xltm` | summarized by the xlsx reader | - |
| OpenDocument spreadsheet | `.ods` `.ots` | summarized by the odf reader | - |
| SQLite database | `.sqlite` `.sqlite3` `.db3` `.db` `.s3db` | summarized by the sqlite reader | - |
| Microsoft Access database | `.mdb` `.accdb` `.accde` `.mde` `.accdr` | summarized by the access reader | - |
| Access lock file | `.ldb` `.laccdb` | identified only | - |
| SQL Server data or backup | `.mdf` `.ndf` `.ldf` `.bak` | identified only | - |
| SQL Server data-tier package | `.bacpac` `.dacpac` | summarized by the zip_listing reader | - |
| Apache Parquet | `.parquet` `.pq` | identified only | - |
| Apache Arrow or Feather | `.arrow` `.feather` `.ipc` | identified only | - |
| Apache Avro data | `.avro` | identified only | - |
| Apache ORC | `.orc` | identified only | - |
| BSON (MongoDB) | `.bson` | identified only | - |
| Redis dump | `.rdb` | identified only | - |

### Microsoft Office and VBA

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| VBA or VB6 module | `.bas` | text | - |
| VBA or VB6 class module | `.cls` | text | - |
| VBA or VB6 form | `.frm` `.dsr` | text | - |
| CSV | `.csv` | text | csv |
| Rich Text Format | `.rtf` | identified; text needs a converter | - |
| Word document | `.docx` `.docm` `.dotx` `.dotm` | summarized by the docx reader | - |
| Excel workbook | `.xlsx` `.xlsm` `.xltx` `.xltm` | summarized by the xlsx reader | - |
| PowerPoint presentation | `.pptx` `.pptm` `.potx` `.ppsx` | summarized by the pptx reader | - |
| Legacy Office document (Word, Excel, PowerPoint 97-2003) | `.doc` `.dot` `.xls` `.xlt` `.ppt` `.pot` ... | summarized by the ole reader | - |
| Outlook message | `.msg` `.oft` | summarized by the ole reader | - |
| Microsoft Access database | `.mdb` `.accdb` `.accde` `.mde` `.accdr` | summarized by the access reader | - |
| Access lock file | `.ldb` `.laccdb` | identified only | - |

### Documents and technical writing

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| HTML | `.html` `.htm` `.xhtml` | text | - |
| SVG image | `.svg` | text | xml |
| Markdown | `.md` `.markdown` `.mdx` `.mdown` `readme` `license` ... | text | - |
| reStructuredText | `.rst` | text | - |
| AsciiDoc | `.adoc` `.asciidoc` `.asc` | text | - |
| LaTeX or BibTeX | `.tex` `.ltx` `.sty` `.bib` `.bst` | text | - |
| Email message (RFC 5322) | `.eml` `.mbox` | text, secrets masked by key | - |
| iCalendar or vCard | `.ics` `.vcf` | text | - |
| Subtitles | `.srt` `.vtt` | text | - |
| Rich Text Format | `.rtf` | identified; text needs a converter | - |
| Word document | `.docx` `.docm` `.dotx` `.dotm` | summarized by the docx reader | - |
| PowerPoint presentation | `.pptx` `.pptm` `.potx` `.ppsx` | summarized by the pptx reader | - |
| Visio drawing | `.vsdx` `.vsdm` | summarized by the zip_listing reader | - |
| OpenDocument text | `.odt` `.ott` | summarized by the odf reader | - |
| OpenDocument spreadsheet | `.ods` `.ots` | summarized by the odf reader | - |
| OpenDocument presentation | `.odp` `.otp` | summarized by the odf reader | - |
| EPUB book | `.epub` | summarized by the zip_listing reader | - |
| PDF | `.pdf` | identified; text needs a converter | - |
| Legacy Office document (Word, Excel, PowerPoint 97-2003) | `.doc` `.dot` `.xls` `.xlt` `.ppt` `.pot` ... | summarized by the ole reader | - |
| Visio drawing (legacy) | `.vsd` `.vss` `.vst` | summarized by the ole reader | - |
| Microsoft Project plan | `.mpp` | summarized by the ole reader | - |
| OneNote section | `.one` | identified only | - |
| Publisher document | `.pub` | summarized by the ole reader | - |
| Image | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.tif` ... | identified only | - |
| Audio or video | `.mp3` `.wav` `.flac` `.ogg` `.m4a` `.mp4` ... | identified only | - |
| Font | `.ttf` `.otf` `.woff` `.woff2` `.eot` | identified only | - |

### Compliance and assurance evidence

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| YAML | `.yaml` `.yml` `.clang-format` `.clang-tidy` | text, secrets masked by key | yaml |
| CSV | `.csv` | text | csv |
| Word document | `.docx` `.docm` `.dotx` `.dotm` | summarized by the docx reader | - |
| Excel workbook | `.xlsx` `.xlsm` `.xltx` `.xltm` | summarized by the xlsx reader | - |
| PDF | `.pdf` | identified; text needs a converter | - |
| SARIF static analysis results | `.sarif` `.sarif.json` | text | sarif |
| CycloneDX SBOM | `.cdx.json` `.cdx.xml` `.bom.json` `.bom.xml` `bom.json` `bom.xml` | text | json |
| SPDX SBOM | `.spdx` `.spdx.json` `.spdx.yaml` `.spdx.rdf` | text | - |
| ReqIF requirements interchange | `.reqif` | text | xml |
| ReqIF archive | `.reqifz` | summarized by the zip_listing reader | - |

### Security testing and red teaming

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| Python | `.py` `.pyi` `.pyw` | text | python |
| C | `.c` `.h` | text | - |
| Rust | `.rs` | text | - |
| PHP | `.php` `.phtml` | text | - |
| Assembly (GNU as, NASM, 68k, PowerPC, MIL-STD-1750A) | `.s` `.asm` `.nasm` `.a51` `.src` | text | - |
| VBScript | `.vbs` `.vbe` | text | - |
| POSIX shell or Bash | `.sh` `.bash` `.zsh` `.ksh` `.bashrc` `.profile` ... | text | - |
| PowerShell | `.ps1` `.psm1` `.psd1` | text | powershell |
| GDB script | `.gdb` `.gdbinit` | text | - |
| Dockerfile or Containerfile | `.dockerfile` `dockerfile` `containerfile` | text | dockerfile |
| Terraform or HCL | `.tf` `.hcl` `.tfvars` | text, secrets masked by key | - |
| JSON Lines | `.jsonl` `.ndjson` | text | jsonl |
| YAML | `.yaml` `.yml` `.clang-format` `.clang-tidy` | text, secrets masked by key | yaml |
| Configuration file (.conf, .cfg, .cnf) | `.conf` `.cfg` `.cnf` `.rc` `hosts` | text, secrets masked by key | - |
| SSH client configuration | `ssh_config` `sshd_config` `known_hosts` | text | - |
| Credentials file | `.netrc` `.pgpass` `.htpasswd` `.git-credentials` `.s3cfg` `.netrc` ... | text, secrets masked by key | - |
| Email message (RFC 5322) | `.eml` `.mbox` | text, secrets masked by key | - |
| PDF | `.pdf` | identified; text needs a converter | - |
| Outlook message | `.msg` `.oft` | summarized by the ole reader | - |
| Windows Installer package | `.msi` `.msp` `.mst` | summarized by the ole reader | - |
| Jupyter notebook | `.ipynb` | text | ipynb |
| Jinja or chat template | `.jinja` `.jinja2` `.j2` | text | - |
| safetensors model weights | `.safetensors` | summarized by the safetensors reader | - |
| GGUF model (llama.cpp) | `.gguf` | summarized by the gguf reader | - |
| Python pickle | `.pkl` `.pickle` `.joblib` `.dill` `.sav` `.p` | summarized by the pickle reader | - |
| PyTorch checkpoint | `.pt` `.pth` `.ckpt` `.tar.pth` | summarized by the pytorch reader | - |
| Keras model | `.keras` | summarized by the keras reader | - |
| ONNX model | `.onnx` `.ort` | identified only | - |
| NumPy array | `.npy` `.npz` | summarized by the numpy reader | - |
| SQLite database | `.sqlite` `.sqlite3` `.db3` `.db` `.s3db` | summarized by the sqlite reader | - |
| SARIF static analysis results | `.sarif` `.sarif.json` | text | sarif |
| CycloneDX SBOM | `.cdx.json` `.cdx.xml` `.bom.json` `.bom.xml` `bom.json` `bom.xml` | text | json |
| SPDX SBOM | `.spdx` `.spdx.json` `.spdx.yaml` `.spdx.rdf` | text | - |
| YARA rules | `.yar` `.yara` | text | - |
| Snort or Suricata rules | `.rules` | text | - |
| HTTP archive (HAR) | `.har` | never shown (secret store) | - |
| Terraform state | `.tfstate` `.tfstate.backup` | never shown (secret store) | - |
| VPN configuration | `.ovpn` | text, secrets masked by key | - |
| PEM certificate, key or request | `.pem` `.crt` `.cer` `.csr` `.ca-bundle` | text | - |
| Private key | `.key` `.ppk` `id_rsa` `id_dsa` `id_ecdsa` `id_ed25519` ... | never shown (secret store) | - |
| OpenPGP data | `.gpg` `.pgp` `.sig` | never shown (secret store) | - |
| Key or password store | `.p12` `.pfx` `.jks` `.jceks` `.keystore` `.bks` ... | never shown (secret store) | - |
| Packet capture | `.pcap` `.pcapng` `.cap` | summarized by the pcap reader | - |
| Raw binary or firmware image | `.bin` `.img` `.rom` `.fw` `.dat` `.raw` | identified only | - |
| ELF executable or object | `.elf` `.axf` `.o` `.so` `.ko` `.prx` | summarized by the elf reader | - |
| Windows executable (PE) | `.exe` `.dll` `.sys` `.ocx` `.scr` `.efi` ... | summarized by the pe reader | - |
| Mach-O executable | `.dylib` `.bundle` | identified only | - |
| Java class or archive | `.class` `.jar` `.war` `.ear` `.aar` | summarized by the zip_listing reader | - |
| Android package | `.apk` `.aab` `.dex` | summarized by the zip_listing reader | - |
| WebAssembly module | `.wasm` | identified only | - |
| Disk or VM image | `.iso` `.vhdx` `.vmdk` `.qcow2` `.ova` `.ovf` ... | identified only | - |

### AI engineering and model security

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| Python | `.py` `.pyi` `.pyw` | text | python |
| R | `.r` `.rmd` | text | - |
| Julia | `.jl` | text | - |
| MATLAB or Octave | `.m` | text | - |
| SAS | `.sas` | text | - |
| JSON | `.json` `.webmanifest` `.geojson` | text, secrets masked by key | json |
| JSON Lines | `.jsonl` `.ndjson` | text | jsonl |
| YAML | `.yaml` `.yml` `.clang-format` `.clang-tidy` | text, secrets masked by key | yaml |
| TOML | `.toml` `pipfile` | text, secrets masked by key | toml |
| CSV | `.csv` | text | csv |
| TSV | `.tsv` `.tab` | text | tsv |
| Jupyter notebook | `.ipynb` | text | ipynb |
| Jinja or chat template | `.jinja` `.jinja2` `.j2` | text | - |
| safetensors model weights | `.safetensors` | summarized by the safetensors reader | - |
| GGUF model (llama.cpp) | `.gguf` | summarized by the gguf reader | - |
| Python pickle | `.pkl` `.pickle` `.joblib` `.dill` `.sav` `.p` | summarized by the pickle reader | - |
| PyTorch checkpoint | `.pt` `.pth` `.ckpt` `.tar.pth` | summarized by the pytorch reader | - |
| Keras model | `.keras` | summarized by the keras reader | - |
| ONNX model | `.onnx` `.ort` | identified only | - |
| TensorFlow Lite model | `.tflite` | identified only | - |
| TensorFlow graph or SavedModel | `.pb` `.pbtxt` | identified only | - |
| Core ML model | `.mlmodel` `.mlpackage` | identified only | - |
| NumPy array | `.npy` `.npz` | summarized by the numpy reader | - |
| HDF5 (including legacy Keras .h5) | `.h5` `.hdf5` `.he5` `.nc` | identified only | - |
| MATLAB data | `.mat` | identified only | - |
| SQLite database | `.sqlite` `.sqlite3` `.db3` `.db` `.s3db` | summarized by the sqlite reader | - |
| Apache Parquet | `.parquet` `.pq` | identified only | - |
| Apache Arrow or Feather | `.arrow` `.feather` `.ipc` | identified only | - |

### Full-stack web applications

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| Python | `.py` `.pyi` `.pyw` | text | python |
| C# | `.cs` `.csx` | text | - |
| Java | `.java` | text | - |
| Kotlin | `.kt` `.kts` | text | - |
| JavaScript | `.js` `.mjs` `.cjs` `.jsx` | text | - |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | text | - |
| HTML | `.html` `.htm` `.xhtml` | text | - |
| CSS and preprocessors | `.css` `.scss` `.sass` `.less` | text | - |
| Vue or Svelte component | `.vue` `.svelte` `.astro` | text | - |
| PHP | `.php` `.phtml` | text | - |
| Ruby | `.rb` `.rake` `.gemspec` `gemfile` `rakefile` | text | - |
| SQL (any dialect) | `.sql` `.ddl` `.dml` `.psql` `.pgsql` `.tsql` ... | text | - |
| GraphQL | `.graphql` `.gql` | text | - |
| Protocol Buffers schema | `.proto` | text | - |
| Gradle | `.gradle` | text | - |
| Dockerfile or Containerfile | `.dockerfile` `dockerfile` `containerfile` | text | dockerfile |
| JSON | `.json` `.webmanifest` `.geojson` | text, secrets masked by key | json |
| JSON with comments | `.jsonc` `.json5` `tsconfig.json` `jsconfig.json` `devcontainer.json` `.eslintrc.json` ... | text, secrets masked by key | - |
| YAML | `.yaml` `.yml` `.clang-format` `.clang-tidy` | text, secrets masked by key | yaml |
| Java properties | `.properties` | text, secrets masked by key | properties |
| Environment file (.env) | `.env` `.env` `.envrc` `.flaskenv` | text, secrets masked by key | dotenv |
| XML | `.xml` `.xsd` `.xsl` `.xslt` `.wsdl` `.xaml` ... | text, secrets masked by key | xml |
| SVG image | `.svg` | text | xml |
| Jinja or chat template | `.jinja` `.jinja2` `.j2` | text | - |
| SQLite database | `.sqlite` `.sqlite3` `.db3` `.db` `.s3db` | summarized by the sqlite reader | - |
| HTTP archive (HAR) | `.har` | never shown (secret store) | - |
| Java class or archive | `.class` `.jar` `.war` `.ear` `.aar` | summarized by the zip_listing reader | - |
| WebAssembly module | `.wasm` | identified only | - |
| Image | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.tif` ... | identified only | - |
| Font | `.ttf` `.otf` `.woff` `.woff2` `.eot` | identified only | - |

### Cloud, containers and infrastructure as code

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| Go | `.go` | text | - |
| Protocol Buffers schema | `.proto` | text | - |
| Thrift or Avro schema | `.thrift` `.avsc` `.avdl` | text | - |
| POSIX shell or Bash | `.sh` `.bash` `.zsh` `.ksh` `.bashrc` `.profile` ... | text | - |
| PowerShell | `.ps1` `.psm1` `.psd1` | text | powershell |
| Bazel or Starlark | `.bzl` `.bazel` `build` `workspace` `module.bazel` `.bazelrc` | text | - |
| Dockerfile or Containerfile | `.dockerfile` `dockerfile` `containerfile` | text | dockerfile |
| Jenkins pipeline (Groovy) | `.groovy` `.jenkinsfile` `jenkinsfile` | text | - |
| Terraform or HCL | `.tf` `.hcl` `.tfvars` | text, secrets masked by key | - |
| Nix | `.nix` | text | - |
| JSON | `.json` `.webmanifest` `.geojson` | text, secrets masked by key | json |
| YAML | `.yaml` `.yml` `.clang-format` `.clang-tidy` | text, secrets masked by key | yaml |
| systemd unit | `.service` `.socket` `.timer` `.mount` `.target` `.path` ... | text, secrets masked by key | systemd |
| Configuration file (.conf, .cfg, .cnf) | `.conf` `.cfg` `.cnf` `.rc` `hosts` | text, secrets masked by key | - |
| Environment file (.env) | `.env` `.env` `.envrc` `.flaskenv` | text, secrets masked by key | dotenv |
| SSH client configuration | `ssh_config` `sshd_config` `known_hosts` | text | - |
| Credentials file | `.netrc` `.pgpass` `.htpasswd` `.git-credentials` `.s3cfg` `.netrc` ... | text, secrets masked by key | - |
| Helm template | `.tpl` | text | - |
| Apache Avro data | `.avro` | identified only | - |
| Terraform state | `.tfstate` `.tfstate.backup` | never shown (secret store) | - |
| PEM certificate, key or request | `.pem` `.crt` `.cer` `.csr` `.ca-bundle` | text | - |
| Private key | `.key` `.ppk` `id_rsa` `id_dsa` `id_ecdsa` `id_ed25519` ... | never shown (secret store) | - |
| Key or password store | `.p12` `.pfx` `.jks` `.jceks` `.keystore` `.bks` ... | never shown (secret store) | - |
| Disk or VM image | `.iso` `.vhdx` `.vmdk` `.qcow2` `.ova` `.ovf` ... | identified only | - |

### Windows administration and scripting

| Format | Files | FORGE reads | Check |
|---|---|---|---|
| C# | `.cs` `.csx` | text | - |
| VBA or VB6 module | `.bas` | text | - |
| VBA or VB6 class module | `.cls` | text | - |
| VBA or VB6 form | `.frm` `.dsr` | text | - |
| Visual Basic .NET | `.vb` | text | - |
| VBScript | `.vbs` `.vbe` | text | - |
| Windows batch (cmd.exe) | `.bat` `.cmd` | text | batch |
| PowerShell | `.ps1` `.psm1` `.psd1` | text | powershell |
| Windows registry export | `.reg` | text, secrets masked by key | - |
| Windows setup information (INF) | `.inf` | text | - |
| Windows Script File | `.wsf` `.wsc` | text | xml |
| AutoHotkey | `.ahk` | text | - |
| MSBuild project | `.csproj` `.vbproj` `.vcxproj` `.fsproj` `.sqlproj` `.props` ... | text | xml |
| Visual Studio solution | `.sln` | text | - |
| INI | `.ini` `setup.cfg` `tox.ini` `.editorconfig` `.flake8` `.pylintrc` ... | text, secrets masked by key | ini |
| XML | `.xml` `.xsd` `.xsl` `.xslt` `.wsdl` `.xaml` ... | text, secrets masked by key | xml |
| ODBC data source | `.dsn` | text, secrets masked by key | ini |
| Windows Installer package | `.msi` `.msp` `.mst` | summarized by the ole reader | - |
| Windows executable (PE) | `.exe` `.dll` `.sys` `.ocx` `.scr` `.efi` ... | summarized by the pe reader | - |

