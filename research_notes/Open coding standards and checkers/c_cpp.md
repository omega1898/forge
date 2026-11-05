# Open coding standards and checkers for C and C++ (status as of 2026-11-05)

Research method note: this session's network blocked most vendor and project sites (wiki.sei.cmu.edu, clang.llvm.org, gcc.gnu.org, misra.org.uk, frama-c.com, semgrep.dev, cwe.mitre.org, perforce.com, barrgroup.com, autosar.org, stroustrup.com, codeql.github.com, best.openssf.org were all refused by the proxy). Primary evidence therefore comes from (a) GitHub-hosted source and docs fetched from raw.githubusercontent.com or by `git clone`/`git ls-remote` (LLVM release branches, gcc-mirror, SEI's new CERT repo, CodeQL, cppcheck, IKOS, Infer, flawfinder, IWYU, OpenSSF), (b) MISRA's own GitLab (gitlab.com/MISRA), (c) PyPI, and (d) web-search snippets for everything else (marked as such). Counts marked "my count" were computed in this session from the cited repository at the cited revision.

## Q1. Standards: publisher, version, license/redistribution, how to obtain, size, which open tools enforce them

### Takeaway
Only a few C/C++ standards can legally be shipped as rule text inside another program. SEI CERT C/C++ (CC BY 4.0 text, MIT code examples, now on GitHub), the OpenSSF hardening guide (CC BY 4.0) and CWE (free for commercial use) qualify. The C++ Core Guidelines license covers only "personal or internal business use". MISRA, AUTOSAR, HIC++, BARR-C and JSF need either rule IDs and short paraphrases only, or a user-supplied text file: MISRA's own headline file is CC BY-NC-ND 4.0. In LLVM 23, all 42 `cert-*` clang-tidy checks are now aliases of `bugprone-`/`misc-`/etc. checks, and the `hicpp-*` module was removed over HIC++ licensing.

### Cited Findings

**SEI CERT C and SEI CERT C++ (Carnegie Mellon University, Software Engineering Institute)**
- The official home of the CERT standards has moved from the Confluence wiki to a GitHub-hosted site. SEI's 2026 LICENSE says contributions go to "the official project at https://cmu-sei.github.io/secure-coding-standards/", and the repo also holds the Android, Java, Perl and Fortran standards. Its last commit was dated 2026-11-05 when cloned. — [cmu-sei/secure-coding-standards LICENSE](https://github.com/cmu-sei/secure-coding-standards/blob/main/LICENSE); [repo content dir](https://github.com/cmu-sei/secure-coding-standards/tree/main/content)
- License (verbatim, © 2026 CMU, DM26-0304): "The SEI CERT® Coding Standards are licensed under a Creative Commons BY 4.0 Attribution License … and the code examples contained therein are licensed under a MIT license." "Carnegie Mellon and CERT are registered trademarks". **So the full rule text can be redistributed inside a program, with attribution. Modification is allowed. The CERT® trademark cannot be used to imply endorsement.** — [cmu-sei/secure-coding-standards LICENSE](https://github.com/cmu-sei/secure-coding-standards/blob/main/LICENSE)
- GitHub's CodeQL Coding Standards repo already ships CERT rule-help text under a 2021 grant with the same terms (CC BY 4.0 plus MIT). It adds a non-endorsement disclaimer: "such use does not necessarily constitute or imply an endorsement…". — [codeql-coding-standards thirdparty/cert/LICENSE](https://github.com/github/codeql-coding-standards/blob/main/thirdparty/cert/LICENSE); [codeql-coding-standards README](https://github.com/github/codeql-coding-standards/blob/main/README.md)
- The older frozen 2016-edition PDFs, downloaded after registration, carried a different notice: "may be reproduced in its entirety, without modification, and freely distributed … Permission is required for any other external and/or commercial use". This was seen only via a third-party copy. — [SEI download page](https://cert.org/secure-coding/products-services/secure-coding-download.cfm); [pdfroom copy (secondary)](https://pdfroom.com/books/sei-cert-c-coding-standard/p0q2JJAm2xE)
- Size (my count from the repo at the 2026-11-05 commit):
  - CERT C has 122 rule IDs and 183 recommendation IDs, in 17 rule chapters (API, ARR, STR, CON, DCL, ENV, ERR, EXP, FLP, FIO, INT, MEM, WIN, MSC, POS, PRE, SIG).
  - CERT C++ has 83 rule IDs (CON50–56, CTR50–58, DCL50–60, ERR50–62, EXP50–63, FIO50–51, INT50, MEM50–57, MSC50–54, OOP50–58, STR50–53, all -CPP).
  - CodeQL's rules.csv independently lists 123 CERT-C entries (120 "Rule" + 3 "Recommendation") and 83 CERT-C++ rules.
  - The 2014 Addison-Wesley book was titled "98 Rules".

  — [SEI repo content](https://github.com/cmu-sei/secure-coding-standards/tree/main/content); [codeql-coding-standards rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv); [book listing](https://campusstore.miamioh.edu/certreg-c-coding-standard-second-edition/bk/9780321984043)
- ID scheme: `XXXnn-C` / `XXXnn-CPP`. "Numeric values in the range of 00 to 29 are reserved for recommendations, and values in the range of 30 to 99 are reserved for rules". C++-specific rules use 50–99. — [SEI "How this coding standard is organized"](https://github.com/cmu-sei/secure-coding-standards/blob/main/content/4.sei-cert-c-coding-standard/02.front-matter/2.introduction/03.how-this-coding-standard-is-organized.md)
- Each rule page has a "Risk Assessment" table with columns Severity, Likelihood, Detectable, Repairable, Priority and Level. Example: ERR33-C is High / Likely / Yes / Yes / P27 / L1. Each page also has an "Automated Detection" table (Tool | Version | Checker | Description) and a "CERT-CWE Mapping Notes" section. — [ERR33-C page source](https://github.com/cmu-sei/secure-coding-standards/blob/main/content/4.sei-cert-c-coding-standard/03.rules/08.error-handling-err/4.err33-c.md)
- Definitions:
  - **Detectable**: "Can a static analysis tool automatically determine if code violates this guideline with high accuracy and precision?"
  - **Repairable**: "Can an automated repair tool reliably fix an alert by making local changes…?"
  - Detectable and Repairable combine into Remediation Cost (1–3), which is multiplied by severity and likelihood to give the priority.

  — [SEI organization page](https://github.com/cmu-sei/secure-coding-standards/blob/main/content/4.sei-cert-c-coding-standard/02.front-matter/2.introduction/03.how-this-coding-standard-is-organized.md)
- Open-source tools named in CERT's own Automated Detection tables (my parse of all rule pages):
  - **CERT C, Clang (15 rules):** DCL31-C (-Wimplicit-int), DCL41-C (-Wsometimes-uninitialized), ENV33-C (cert-env33-c), ERR34-C (cert-err34-c), EXP30-C (-Wunsequenced), EXP32-C (-Wincompatible-pointer-types-discards-qualifiers), EXP44-C (-Wunevaluated-expression), EXP45-C (-Wparentheses), EXP47-C (-Wvarargs), FIO38-C (misc-non-copyable-objects), FLP30-C (cert-flp30-c), INT36-C (-Wint-to-pointer-cast, -Wint-conversion), MEM34-C (clang-analyzer-unix.Malloc), MSC30-C (cert-msc30-c), STR38-C (-Wincompatible-pointer-types).
  - **CERT C, open-source Cppcheck (20 rules):** ARR30-C (arrayIndexOutOfBounds, outOfBounds, negativeIndex…), ARR32-C (negativeArraySize), ARR36-C (comparePointers), DCL30-C (danglingLifetime, returnDanglingLifetime, autoVariables, invalidLifetime), EXP30-C (unknownEvaluationOrder), EXP33-C (uninitvar, uninitdata…), EXP34-C (nullPointer…), EXP46-C (bitwiseOnBoolean), FIO39-C (IOWithoutPositioning), FIO42-C (resourceLeak), FIO47-C (invalidscanf, wrongPrintfScanfArgNum…), FLP34-C (floatConversionOverflow), INT31-C (memsetValueOutOfRange), INT33-C (zerodiv, zerodivcond), INT34-C (shiftNegative, shiftTooManyBits), MEM30-C (doubleFree, deallocret, deallocuse), MEM31-C (memleak…), MEM34-C (autovarInvalidDeallocation, mismatchAllocDealloc), MSC37-C (missingReturn), PRE30-C (preprocessorErrorDirective).
  - **CERT C, GCC (12 rules):** DCL31, DCL36, EXP30, EXP32, EXP33, EXP36, EXP37, EXP43 (-Wrestrict), EXP45, FIO30, FIO47, STR34 (-Wchar-subscripts). Splint covers 15 rules and Compass/ROSE 60.
  - **CERT C++, Clang (31 rules):** for example DCL50-CPP (cert-dcl50-cpp), DCL54 (misc-new-delete-overloads), ERR52/58/60/61, EXP50 (-Wunsequenced), EXP53 (-Wuninitialized, clang-analyzer-core.UndefinedBinaryOperatorResult), MEM50 (clang-analyzer-cplusplus.NewDelete), MEM51 (…NewDeleteLeaks, -Wmismatched-new-delete), OOP50 (clang-analyzer-alpha.cplusplus.VirtualCall), OOP52 (-Wdelete-non-virtual-dtor), OOP53 (-Wreorder), OOP54 (cert-oop54-cpp).
  - **CERT C++, Cppcheck (open source):** none.
  - **Commercial tools:** they dominate. Parasoft and Polyspace each appear on 120 of 122 C rules. "Cppcheck Premium" appears on 93 C rules.

  — [SEI repo rule pages](https://github.com/cmu-sei/secure-coding-standards/tree/main/content/4.sei-cert-c-coding-standard/03.rules)
- In CERT's "Detectable" column, CERT C rules split 61 Yes / 60 No (1 unparsed), CERT C recommendations 69 Yes / 112 No, and CERT C++ rules 38 Yes / 45 No (my parse). — [SEI repo](https://github.com/cmu-sei/secure-coding-standards/tree/main/content)
- CWE crosswalks: MITRE view CWE-1154 is "Weaknesses Addressed by the SEI CERT C Coding Standard". It is marked PROHIBITED for mapping real-world vulnerabilities, and it supersedes the 2008 view 734. — [CWE-1154](https://cwe.mitre.org/data/definitions/1154.html) (via search snippet)

**MISRA C:2012 / C:2023 / C:2025 and MISRA C++:2023 (The MISRA Consortium Ltd)**
- The full documents are paid. MISRA itself publishes a free headline file for Cppcheck on GitLab: "MISRA C:2023 Guideline Headlines for CPPcheck … Copyright (C)2023 The MISRA Consortium Limited … licenced under a Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0) licence … For licencing for commercial use, or requests for derivative work, please contact enquiries@misra.org.uk". The repo also has `misra_c_2012__headlines_for_cppcheck - AMD1+AMD2.txt`. — [gitlab.com/MISRA/MISRA-C/MISRA-C-2012/tools](https://gitlab.com/MISRA/MISRA-C/MISRA-C-2012/tools)
- The MISRA C:2012 Example Suite on GitLab is not redistributable: "A license is granted for single-user personal use on a single computer only. No permission is given for the distribution of these files, or any derivatives outside of your organisation". — [MISRA C:2012 Example-Suite](https://gitlab.com/MISRA/MISRA-C/MISRA-C-2012/Example-Suite)
- Sizes:
  - MISRA C:2023 has 221 guidelines (my count of `Rule`/`Dir` entries in MISRA's headline file).
  - CodeQL's "MISRA-C-2012" set (C:2012 with AMD1–4 and TC1–2) also has 221 entries: 22 Mandatory, 151 Required, 48 Advisory.

  — [MISRA headlines file](https://gitlab.com/MISRA/MISRA-C/MISRA-C-2012/tools); [codeql-coding-standards rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv)
- The original MISRA C:2012 rule set had 143 rules: 104 Single-Translation-Unit and 39 System scope, of which 26 were Undecidable. This is a third-party PRQA slide figure. — [PRQA slides](https://www.slideshare.net/slideshow/misra-introduction-45757698/45757698); [Wikipedia MISRA C](https://en.wikipedia.org/wiki/MISRA_C)
- MISRA C:2025 (published 2025) totals conflict between vendors: LDRA 223 (22 Dir + 201 Rules; 22 Mandatory / 153 Required / 47 Advisory; 1 disapplied), Parasoft 224, Perforce 225. — [LDRA blog](https://ldra.com/ldra-blog/misra-c-2025-the-whys-and-wherefores-of-whats-new/); [Parasoft](https://www.parasoft.com/webinar/misra-c-2025); [Perforce](https://www.perforce.com/blog/sca/misra-c-2025)
- MISRA C++:2023 has 179 guidelines: 4 directives and 175 rules. In CodeQL's rules.csv, 156 are "Decidable", 20 "Undecidable" and 3 blank; 5 are Mandatory, 124 Required and 50 Advisory; 4 are marked not supportable (the directives). — [codeql-coding-standards rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv); [Parasoft MISRA C++:2023 guide](https://parasoft.com/blog/misra-cpp-2023-guide/); [Polyspace coverage](https://www.mathworks.com/help/bugfinder/ug/polyspace_coverage_coding_standard.html)
- MISRA Compliance:2020, the framework for compliance claims and deviations, is a free download. — [MISRA forum](https://forum.misra.org.uk/showthread.php?tid=1529); [MISRA publications](https://misra.org.uk/publications)
- Rule numbers and short descriptions are widely reproduced by open tools:
  - CodeQL's MIT-licensed rules.csv lists MISRA IDs with one-line descriptions, for example "DIR-1-1 … Any implementation-defined behaviour on which the output of the program depends shall be documented".
  - cppcheck's GPL misra.py implements checks keyed by rule number but ships no rule text.

  I found no explicit MISRA policy statement on using bare rule numbers. — [rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv); [cppcheck manual](https://github.com/danmar/cppcheck/blob/main/man/manual.md)

**AUTOSAR C++14 Guidelines ("Guidelines for the use of the C++14 language in critical and safety-related systems", AUTOSAR AP)**
- These are public PDFs at autosar.org: R17-03, R17-10, R18-03, R19-03, R22-11.
- The disclaimer (R17-03) says the work "may be utilized or reproduced without any modification, in any form or by any means, for informational purposes only. For any other purpose, no part of the work may be utilized or reproduced … without permission in writing from the publisher". It also says "commercial exploitation … requires a license". **Rule IDs and paraphrases only.** — [AUTOSAR R17-03 PDF](https://www.autosar.org/fileadmin/standards/R17-03_R1.1.0/AP/AUTOSAR_RS_CPP14Guidelines.pdf) (via search snippet); [R22-11 PDF](https://www.autosar.org/fileadmin/standards/R22-11/AP/AUTOSAR_RS_CPP14Guidelines.pdf)
- Size: CodeQL rules.csv lists 397 AUTOSAR rules (A-rules plus inherited M-rules from MISRA C++:2008): 372 supportable; 329 "Automated", 46 "Non-Automated", 22 partially automated. Polyspace cites 349 statically enforceable rules. — [rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv); [Polyspace](https://www.mathworks.com/help/bugfinder/ug/polyspace_coverage_coding_standard.html)

**C++ Core Guidelines (Standard C++ Foundation / isocpp, eds. Stroustrup & Sutter)**
- License (verbatim): "Standard C++ Foundation grants you a worldwide, nonexclusive, royalty-free, perpetual license to copy, use, modify, and create derivative works from this project **for your personal or internal business use only**." **Embedding the text in a product distributed to others is not clearly granted**; IDs and titles with links are the safe form. — [CppCoreGuidelines LICENSE](https://github.com/isocpp/CppCoreGuidelines/blob/master/LICENSE)
- It is a living document with no numbered releases, kept as a single Markdown file. My count is 478 numbered `###` entries, which includes 21 FAQ.* and 7 NR.* non-rules. The largest families are C.=100, ES.=65, T.=54, F.=40, CP.=33, R.=25, E.=22, SL.=22, I.=20, NL.=20, Per.=18, SF.=16, P.=13. There are 443 "Enforcement" mentions. — [CppCoreGuidelines.md](https://github.com/isocpp/CppCoreGuidelines/blob/master/CppCoreGuidelines.md)
- Enforcement is mostly through clang-tidy `cppcoreguidelines-*`. LLVM 23 has 31 primary checks and 12 aliases (see Q2). — [LLVM 23 list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)

**JSF AV C++ (Lockheed Martin, Doc. 2RDU00001 Rev C, December 2005)**
- It is mirrored at stroustrup.com. One copy carries "Copyright 2005 by Lockheed Martin Corporation. DISTRIBUTION STATEMENT A: Approved for public release; distribution is unlimited". A later Rev D (June 2007) is cited elsewhere. — [stroustrup.com JSF-AV-rules.pdf](https://stroustrup.com/JSF-AV-rules.pdf); [SlideShare copy](https://www.slideshare.net/DavidUnderdown/jsf-avrules); [micro-os-plus coding style](https://micro-os-plus.github.io/develop/coding-style)
- Open enforcement: CodeQL's MIT-licensed `cpp/ql/src/jsf/` has 154 .ql queries covering 148 distinct "AV Rule N" numbers (my count). — [github/codeql cpp/ql/src](https://github.com/github/codeql/tree/main/cpp/ql/src)

**NASA/JPL "Power of Ten" and JPL Institutional Coding Standard for C (JPL D-60411, v1.0, 2009-03-03)**
- The JPL standard is listed as Active on EverySpec. The externally distributed PDF omits levels LOC-5 and LOC-6, the MISRA-derived ones, "because of copyright held by MIRA Ltd". — [EverySpec](https://everyspec.com/NASA/NASA-JPL/JPL-D-60411_VER-1_32832/); [external-distribution PDF copy](https://abougouffa.github.io/awesome-coding-standards/jpl-c-1.0.pdf); [NASA SWEHB](https://swehb.nasa.gov/x/9wH7)
- Open enforcement: CodeQL has `cpp/ql/src/JPL_C` (42 queries across LOC-2 Rules 03,04,05,07,09,11,12; LOC-3 Rules 13–19; LOC-4 Rules 20–31) and `cpp/ql/src/Power of 10` (23 queries, Rules 1–9). — [github/codeql cpp/ql/src](https://github.com/github/codeql/tree/main/cpp/ql/src)

**High Integrity C++ (HIC++ 4.0, PRQA → Perforce)**
- Perforce lists HIC++ 4.0 as the latest version, "155 coding rules", available after filling in a form. — [Perforce HIC++ page](https://perforce.com/resources/qac/high-integrity-cpp-coding-rules)
- clang-tidy's hicpp LICENSE pointed users to Perforce's "conditions-of-use" for "Any file referencing a High-Integrity C++ Coding guideline". — [hicpp/LICENSE.TXT (release/22.x)](https://github.com/llvm/llvm-project/blob/release/22.x/clang-tools-extra/clang-tidy/hicpp/LICENSE.TXT)
- An LLVM RFC (secondhand) reports that the document's terms allow sharing with attribution to PRQA and no implied endorsement, and that the standard has moved behind a registration wall. — [LLVM Discourse RFC](https://discourse.llvm.org/t/rfc-regarding-the-current-status-of-hicpp-checks/89883)
- LLVM deprecated `hicpp` in 22.x, citing license issues, and removed it in 23.x. — [llvm-branch-commits PR #194377](https://lists.llvm.org/pipermail/llvm-branch-commits/2026-April/091748.html); [LLVM 23 ReleaseNotes](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/ReleaseNotes.rst)

**BARR-C:2018 Embedded C Coding Standard (Barr Group / Integrated Embedded LLC)**
- It is a free PDF/HTML from barrgroup.com/coding-standard, with print copies on Amazon. An "editable (Microsoft Word) copy … is available for download and license" for teams that want to customize it. BARR-C is a trademark. — [Embedded Computing Design](https://embeddedcomputing.com/technology/software-and-os/barr-c-2018-updates-embedded-c-coding-standard)
- It defines a subset of C that is larger than MISRA C:2012's. — [arXiv 2003.06893](https://arxiv.org/abs/2003.06893)

**OpenSSF Compiler Options Hardening Guide for C and C++**
- License: "documentation is released under the CC-BY-4.0 license" and software under Apache-2.0. It can be redistributed with attribution. — [OpenSSF WG README](https://github.com/ossf/wg-best-practices-os-developers/blob/main/README.md)
- Baseline flags (verbatim): `-O2 -Wall -Wformat -Wformat=2 -Wconversion -Wimplicit-fallthrough -Werror=format-security -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -D_GLIBCXX_ASSERTIONS -D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST -fstrict-flex-arrays=3 -fstack-clash-protection -fstack-protector-strong -Wl,-z,nodlopen -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now -Wl,--as-needed -Wl,--no-copy-dt-needed-entries`. Conditional additions:
  - GCC: `-Wtrampolines -fzero-init-padding-bits=all`
  - `-Wbidi-chars=any`
  - executables: `-fPIE -pie`
  - x86_64: `-fcf-protection=full -fzero-call-used-regs=used-gpr`
  - aarch64: `-mbranch-protection=standard`
  - production: `-fno-delete-null-pointer-checks -fno-strict-overflow -fno-strict-aliasing -ftrivial-auto-var-init=zero`
  - C: `-Werror=implicit -Werror=incompatible-pointer-types -Werror=int-conversion`
  - development only: `-Werror`

  The repo ships a scraper script that converts the options table to JSON "for use in tooling". — [Compiler-Options-Hardening-Guide-for-C-and-C++.md](https://github.com/ossf/wg-best-practices-os-developers/blob/main/docs/Compiler-Hardening-Guides/Compiler-Options-Hardening-Guide-for-C-and-C++.md)

**CWE (MITRE)**
- "CWE™ is free to use by any organization or individual for any research, development, and/or commercial purposes". CWE, CWSS and CWRAF are trademarks. — [CWE Terms of Use](https://cwe.mitre.org/about/termsofuse.html) (via search snippet)

### Inferences
- License tiers for embedding in an offline assistant:
  - **Full text can ship:** SEI CERT C/C++ (CC BY 4.0, MIT code), OpenSSF guide (CC BY 4.0), CWE.
  - **IDs, titles and own paraphrase only:** MISRA, AUTOSAR, HIC++, JSF (Distribution A suggests full-text redistribution is probably fine, but no explicit license was found), JPL, BARR-C (terms unknown).
  - **C++ Core Guidelines:** only for internal use. Shipping the text to third parties needs permission; IDs and links are safe.
- MISRA support should follow the cppcheck pattern: ship check logic keyed by rule ID, and let the user import MISRA's CC BY-NC-ND headline file or their own licensed copy at runtime. The NC term means a commercial product cannot bundle it.
- CERT's Automated Detection tables are stale for open tools. They still cite `cert-*` names and an old cppcheck. Treat clang-tidy's alias table (Q2) as the current mapping and CERT's tables as historical.

### Gaps
- I could not open the current SEI CERT wiki or Legal page directly (host blocked). Whether wiki.sei.cmu.edu still serves the content or redirects is unverified. The GitHub repo LICENSE is the primary evidence used.
- MISRA's policy on tools quoting rule numbers or headlines (outside the CC BY-NC-ND file) was not found. No free MISRA C++:2023 or MISRA C:2025 headline file was found in MISRA's GitLab group (only C:2012+AMD1/2 and C:2023).
- The official MISRA C:2025 guideline count is unresolved (vendor figures range from 223 to 225).
- Exact current HIC++ 4.0 conditions-of-use text, BARR-C:2018 reuse terms and the total JSF AV rule count were not verified (sites blocked). The JPL total rule count is inferred only from CodeQL's directory numbering (Rules 1–31 in LOC-1..4).
- The AUTOSAR R19-03 / R22-11 reproduction clause was not confirmed to match R17-03.

## Q2. Tools: license, rule-ID scheme, standard mappings, output formats, determinism

### Takeaway
- **License-free, deterministic core:** clang-tidy (Apache-2.0 WITH LLVM-exception), the Clang Static Analyzer, GCC `-fanalyzer` (C only, CWE-tagged), cppcheck (GPLv3, CWE in output), IKOS (NOSA 1.3, sound abstract interpretation), Frama-C EVA/WP, flawfinder (GPLv2+, CWE + SARIF), Infer (MIT, SARIF) and IWYU (LLVM/NCSA).
- **License caveats:** CodeQL's queries are MIT but its CLI may only be used on OSI-licensed codebases without a paid GitHub Advanced Security (GHAS) license, and may not be redistributed. The Semgrep engine is LGPL-2.1, but the Semgrep-maintained rules are internal-use-only and C/C++ is "experimental" in the open-source engine.

### Cited Findings

**clang-tidy (LLVM; latest release tag llvmorg-23.1.5; main is LLVM 24)**
- Versions: release tags reach llvmorg-23.1.5 (`git ls-remote`), and main's LLVMVersion.cmake is 24.0. — [llvm-project tags](https://github.com/llvm/llvm-project/tags); [LLVMVersion.cmake](https://github.com/llvm/llvm-project/blob/main/cmake/Modules/LLVMVersion.cmake)
- Size in LLVM 23 (my count of list.rst): 406 primary checks plus 162 aliases. Primary checks by group: bugprone 109, readability 62, modernize 49, cppcoreguidelines 31, misc 29, performance 21, abseil 20, android 15, google 14, llvm 13, and smaller groups. Aliases: 97 `clang-analyzer-*`, 42 `cert-*`, 12 `cppcoreguidelines-*`, 6 google, 2 llvm, 2 fuchsia, 1 performance. — [release/23.x list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)
- `cert-*` history:
  - LLVM 21: 14 primary + 27 alias. The primary ones were cert-dcl50-cpp, dcl58-cpp, env33-c, err33-c, err34-c, err52-cpp, err58-cpp, err60-cpp, flp30-c, mem57-cpp, msc50-cpp, msc51-cpp, oop57-cpp and oop58-cpp.
  - LLVM 22: 5 primary + 40 alias.
  - LLVM 23: 0 primary + 42 alias. Every cert check is now a redirect.

  — [release/21.x list.rst](https://github.com/llvm/llvm-project/blob/release/21.x/clang-tools-extra/docs/clang-tidy/checks/list.rst); [release/22.x list.rst](https://github.com/llvm/llvm-project/blob/release/22.x/clang-tools-extra/docs/clang-tidy/checks/list.rst); [release/23.x list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)
- Complete LLVM 23 CERT alias table (alias → target; each alias name maps 1:1 to the CERT ID, e.g. cert-err33-c ↔ ERR33-C). — [release/23.x list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)
  - arr39-c → bugprone-sizeof-expression
  - con36-c, con54-cpp → bugprone-spuriously-wake-up-functions
  - ctr56-cpp → bugprone-pointer-arithmetic-on-polymorphic-object
  - dcl03-c → misc-static-assert
  - dcl16-c → readability-uppercase-literal-suffix
  - dcl37-c, dcl51-cpp → bugprone-reserved-identifier
  - dcl50-cpp → modernize-avoid-variadic-functions
  - dcl54-cpp → misc-new-delete-overloads
  - dcl58-cpp → bugprone-std-namespace-modification
  - dcl59-cpp → misc-anonymous-namespace-in-header
  - env33-c → bugprone-command-processor
  - err09-cpp, err61-cpp → misc-throw-by-value-catch-by-reference
  - err33-c → bugprone-unused-return-value
  - err34-c → bugprone-unchecked-string-to-number-conversion
  - err52-cpp → modernize-avoid-setjmp-longjmp
  - err58-cpp → bugprone-throwing-static-initialization
  - err60-cpp → bugprone-exception-copy-constructor-throws
  - exp42-c, flp37-c → bugprone-suspicious-memory-comparison
  - exp45-c → bugprone-assignment-in-selection-statement
  - fio38-c → misc-non-copyable-objects
  - flp30-c → bugprone-float-loop-counter
  - int09-c → readability-enum-initial-value
  - mem57-cpp → bugprone-default-operator-new-on-overaligned-type
  - msc24-c, msc33-c → bugprone-unsafe-functions
  - msc30-c, msc50-cpp → misc-predictable-rand
  - msc32-c, msc51-cpp → bugprone-random-generator-seed
  - msc54-cpp, sig30-c → bugprone-signal-handler
  - oop11-cpp → performance-move-constructor-init
  - oop54-cpp → bugprone-unhandled-self-assignment
  - oop57-cpp → bugprone-raw-memory-call-on-non-trivial-type
  - oop58-cpp → bugprone-copy-constructor-mutates-argument
  - pos44-c → bugprone-bad-signal-to-kill-thread
  - pos47-c → concurrency-thread-canceltype-asynchronous
  - str34-c → bugprone-signed-char-misuse

  Note that dcl03-c, dcl16-c, int09-c, msc24-c and err09-cpp/oop11-cpp refer to CERT *recommendations* or C++ IDs outside the current rule list, not rules (by the 00–29 numbering).
- `hicpp-*` history: LLVM 21 had 5 primary checks (exception-baseclass, ignored-remove-result, multiway-paths-covered, no-assembler, signed-bitwise) and 26 aliases. LLVM 23 "Removed the clang-tidy hicpp module. All checks have been moved to the other modules". — [release/21.x list.rst](https://github.com/llvm/llvm-project/blob/release/21.x/clang-tools-extra/docs/clang-tidy/checks/list.rst); [LLVM 23 ReleaseNotes](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/ReleaseNotes.rst)
- Replacements named in the LLVM 23 release notes:
  - hicpp-avoid-c-arrays → modernize-avoid-c-arrays
  - hicpp-avoid-goto → cppcoreguidelines-avoid-goto
  - hicpp-exception-baseclass → bugprone-std-exception-baseclass
  - hicpp-explicit-conversions → misc-explicit-constructor
  - hicpp-ignored-remove-result → bugprone-unused-return-value
  - hicpp-invalid-access-moved → bugprone-use-after-move
  - hicpp-member-init → cppcoreguidelines-pro-type-member-init
  - hicpp-multiway-paths-covered → bugprone-unhandled-code-paths + readability-trivial-switch
  - hicpp-no-array-decay → cppcoreguidelines-pro-bounds-array-to-pointer-decay
  - hicpp-no-assembler → portability-no-assembler
  - hicpp-no-malloc → cppcoreguidelines-no-malloc
  - hicpp-signed-bitwise → bugprone-signed-bitwise
  - hicpp-vararg → cppcoreguidelines-pro-type-vararg
  - the remaining checks moved to modernize-/readability-/performance-

  — [LLVM 23 ReleaseNotes](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/ReleaseNotes.rst)
- HIC++ rule references in the older docs: exception-baseclass → HIC++ rule 15.1; no-assembler → 7.5.1; multiway-paths-covered → 6.1.2 and 6.1.4; ignored-remove-result → 17.5.1. — [release/22.x hicpp docs](https://github.com/llvm/llvm-project/tree/release/22.x/clang-tools-extra/docs/clang-tidy/checks/hicpp)
- LLVM 23 `cppcoreguidelines-*` primary checks (31): avoid-capturing-lambda-coroutines, avoid-const-or-ref-data-members, avoid-do-while, avoid-goto, avoid-non-const-global-variables, avoid-reference-coroutine-parameters, init-variables, interfaces-global-init, macro-usage, misleading-capture-default-by-value, missing-std-forward, no-malloc, no-suspend-with-lock, owning-memory, prefer-member-initializer, pro-bounds-array-to-pointer-decay, pro-bounds-avoid-unchecked-container-access, pro-bounds-constant-array-index, pro-bounds-pointer-arithmetic, pro-type-const-cast, pro-type-cstyle-cast, pro-type-member-init, pro-type-reinterpret-cast, pro-type-static-cast-downcast, pro-type-union-access, pro-type-vararg, rvalue-reference-param-not-moved, slicing, special-member-functions, use-enum-class, virtual-class-destructor. — [release/23.x list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)
- LLVM 23 `cppcoreguidelines-*` aliases (12):
  - avoid-c-arrays → modernize-avoid-c-arrays
  - avoid-magic-numbers → readability-magic-numbers
  - c-copy-assignment-signature → misc-unconventional-assign-operator
  - explicit-constructor → misc-explicit-constructor
  - explicit-virtual-functions → modernize-use-override
  - macro-to-enum → modernize-macro-to-enum
  - narrowing-conversions → bugprone-narrowing-conversions
  - noexcept-destructor / noexcept-move-operations / noexcept-swap → performance-noexcept-*
  - non-private-member-variables-in-classes → misc-…
  - use-default-member-init → modernize-…

  — [release/23.x list.rst](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/docs/clang-tidy/checks/list.rst)
- Output: the clang-tidy CLI options in LLVM 23 include `--export-fixes` (YAML) and `--warnings-as-errors`, and no SARIF option (0 "sarif" mentions in ClangTidyMain.cpp). The MIT-licensed `clang-tidy-sarif` converter from sarif-rs fills the gap. — [ClangTidyMain.cpp release/23.x](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/clang-tidy/tool/ClangTidyMain.cpp); [psastras/sarif-rs](https://github.com/psastras/sarif-rs)

**Clang Static Analyzer (part of Clang)**
- Output formats (`-analyzer-output=`): html, plist, plist-html, **sarif**, sarif-html, text, text-minimal. — [Analyses.def release/23.x](https://github.com/llvm/llvm-project/blob/release/23.x/clang/include/clang/StaticAnalyzer/Core/Analyses.def)
- About 178 documented checkers in LLVM 23 (my count). By package: osx 27, security 21, core 15, optin 15, unix 14, cplusplus 9, nullability 5, plus alpha.* packages. In clang-tidy they appear as `clang-analyzer-<checker>` (97 aliases). — [checkers.rst release/23.x](https://github.com/llvm/llvm-project/blob/release/23.x/clang/docs/analyzer/checkers.rst)

**GCC warnings and -fanalyzer (GPLv3; releases/gcc-16.2.0 is the latest tag; master BASE-VER 17.0.0)**
- The analyzer "performs coverage-guided symbolic execution … It is neither sound nor complete … The analyzer is only suitable for use on C code in this release" (master docs). — [gcc invoke.texi](https://github.com/gcc-mirror/gcc/blob/master/gcc/doc/invoke.texi); [gcc tags](https://github.com/gcc-mirror/gcc/tags)
- There are 56 `-Wanalyzer-*` options. CWE IDs cited in the analyzer docs: 119, 129, 131, 1341, 200, 369, 401, 415, 416, 457, 469, 476, 479, 532, 590, 617, 666, 674, 685, 686, 690, 762, 775, 789, 823, 835. — [gcc invoke.texi](https://github.com/gcc-mirror/gcc/blob/master/gcc/doc/invoke.texi)
- SARIF output:
  - `-fdiagnostics-format=sarif-stderr|sarif-file` emits "SARIF Version 2.1.0".
  - The newer `-fdiagnostics-add-output=sarif:…` takes keys for file, serialization and version (2.2 is experimental) and can include `-fanalyzer` state information.

  — [gcc invoke.texi](https://github.com/gcc-mirror/gcc/blob/master/gcc/doc/invoke.texi)
- GCC sanitizers (`-fsanitize=`): address, hwaddress, kernel-address, kernel-hwaddress, leak, thread, undefined (plus sub-checks shift, signed-integer-overflow, bounds, bounds-strict, alignment, null, object-size, vptr, pointer-overflow, float-cast-overflow, etc.), pointer-compare/subtract, memtag-stack and shadow-call-stack. There is no `memory` (MSan) in the GCC list. — [gcc invoke.texi](https://github.com/gcc-mirror/gcc/blob/master/gcc/doc/invoke.texi)

**cppcheck (GPLv3; latest tag 2.22.0; main = 2.23 dev)**
- License is GPLv3. — [COPYING](https://github.com/danmar/cppcheck/blob/main/COPYING); [tags](https://github.com/danmar/cppcheck/tags); [releasenotes.txt](https://github.com/danmar/cppcheck/blob/main/releasenotes.txt)
- Output: `--output-format=text|sarif|xml (deprecated)|xmlv2|xmlv3`, plus `--template`. The XML has a `cwe` attribute, "only used when the CWE ID for the message is known". Rule IDs are camelCase message IDs (e.g. nullPointer, arrayIndexOutOfBounds, uninitvar). — [cmdlineparser.cpp](https://github.com/danmar/cppcheck/blob/main/cli/cmdlineparser.cpp); [manual.md](https://github.com/danmar/cppcheck/blob/main/man/manual.md)
- MISRA addon:
  - "The misra.py script does not provide rule texts, those should be downloaded from MISRA (gitlab.com/MISRA/MISRA-C/MISRA-C-2012/tools)". Load them via a `misra.json` with `--rule-texts=…`.
  - "Misra checkers in open source Cppcheck only cover MISRA rules partially and for full coverage use Cppcheck Premium."
  - misra.py defines checks for 130 distinct MISRA C:2012 rule numbers (my count, e.g. 1.2 … 22.10). The user supplies only the headline text file.

  — [manual.md](https://github.com/danmar/cppcheck/blob/main/man/manual.md); [addons/misra.py](https://github.com/danmar/cppcheck/blob/main/addons/misra.py)
- There is no open-source CERT addon (addons/cert.py returns 404). The CLI's `--premium=` options (autosar, cert-c, cert-c++, misra-c-2012/2023/2025, misra-c++-2008/2023) apply to commercial Cppcheck Premium only. — [cmdlineparser.cpp](https://github.com/danmar/cppcheck/blob/main/cli/cmdlineparser.cpp)

**CodeQL (queries MIT; CLI proprietary)**
- The github/codeql repo is MIT ("Copyright (c) 2006-2025 GitHub"). — [codeql LICENSE](https://github.com/github/codeql/blob/main/LICENSE)
- The CLI is under "GitHub CodeQL Terms and Conditions". It may be used for academic research, demos, and analysis of an "Open Source Codebase" (OSI-licensed). It may not be used "in connection with any codebase that is not an Open Source Codebase (e.g., code in a private repo)" unless the user has paid GitHub Advanced Security. It may not be shared or distributed or offered as a hosted solution. **It cannot be bundled into an offline assistant for proprietary code.** — [codeql-cli-binaries LICENSE.md](https://github.com/github/codeql-cli-binaries/blob/main/LICENSE.md); [tags (v2.27.4 latest)](https://github.com/github/codeql-cli-binaries/tags)
- C/C++ pack (`codeql/cpp-queries` 1.10.1-dev): 632 .ql files. Suites: cpp-code-scanning (default), security-extended, security-and-quality, code-quality(-extended), security-experimental. `Security/CWE/CWE-xxx` directories cover 40 CWE IDs (014, 020, 022, 078, 079, 089, 114, 119, 120, 121, 129, 131, 134, 170, 190, 191, 193, 253, 290, 295, 311, 313, 319, 326, 327, 367, 416, 428, 457, 468, 497, 570, 611, 676, 704, 732, 764, 807, 835, 843). Other queries also carry `external/cwe/cwe-NNN` tags, which I did not count. The jsf/, JPL_C/ and Power of 10/ directories are noted in Q1. — [github/codeql cpp/ql/src](https://github.com/github/codeql/tree/main/cpp/ql/src)
- CodeQL Coding Standards (MIT; tags to v2.63.0):
  - Covers AUTOSAR C++14 (R22-11, R20-11, R19-11, R19-03), CERT C++ 2016, CERT C 2016, MISRA C++:2023, MISRA C:2012 (+AMD2/3/4, TC2) and MISRA C:2023. MISRA C:2025 is "planned".
  - It is qualified as an ISO 26262 Part 8 tool "if it is used as distributed".
  - Output is SARIF: the user manual requires `--format=sarifv2.1.0` and supplies default/audit/strict suites, deviation records and recategorization.

  — [codeql-coding-standards README](https://github.com/github/codeql-coding-standards/blob/main/README.md); [user_manual.md](https://github.com/github/codeql-coding-standards/blob/main/docs/user_manual.md)

**Semgrep / Opengrep**
- The engine is LGPL-2.1. The semgrep-rules repo is "Semgrep Rules License v1.0", which allows internal business use only. Vendors cannot use the rules in competing products or SaaS; this changed in December 2024 with a grace period to 2025-01-31. Latest tags: semgrep v1.183.0, opengrep v1.33.1 (Opengrep is an LGPL fork). — [semgrep LICENSE](https://github.com/semgrep/semgrep/blob/develop/LICENSE); [semgrep-rules LICENSE](https://github.com/semgrep/semgrep-rules/blob/develop/LICENSE); [Semgrep licensing docs](https://docs.semgrep.dev/licensing); [Semgrep blog Dec 2024](https://semgrep.dev/blog/2024/important-updates-to-semgrep-oss)
- C/C++ is "Generally available" only in commercial Semgrep Code. In the open-source engine it "will stay experimental". — [Semgrep supported languages](https://docs.semgrep.dev/supported-languages) (search snippet)

**Frama-C (CEA List; EVA abstract interpretation, WP deductive verification)**
- Version 32.0 is in the opam docs. The Markdown Report plugin (`mdr`) has `-mdr-gen none|md|draft|sarif`, and `-mdr-sarif-deterministic` omits "absolute file URIs and timestamps" (documented in 29.0). Frama-C's own page calls the SARIF output "known to be incomplete". — [mdr_params.ml 32.0](https://ocaml.org/p/frama-c/32.0/doc/src/frama-c-markdown-report.core/mdr_params.ml.html); [mdr_params 29.0](https://ocaml.org/p/frama-c/29.0/doc/src/frama-c-markdown-report.core/mdr_params.ml.html); [Frama-C mdr plugin page](https://git.frama-c.com/pub/pub.frama-c.com/-/raw/cfdbccb5e4733dc8ba91735d18bd5f6760a0fecb/_fc-plugins/markdown-report.md)

**IKOS (NASA; v3.5)**
- License: "NASA Open Source Agreement version 1.3". It is an LLVM-based abstract interpreter "for detecting and proving the absence of runtime errors". Each check is classified safe / error / unreachable / warning. — [IKOS README](https://github.com/NASA-SW-VnV/ikos)
- Analyses (`-a=`): boa (buffer overflow), dbz (division by zero), nullity, prover (`__ikos_assert`), upa (unaligned pointer), uva (uninitialized), sio/uio (signed/unsigned overflow), shc (shift count), poa (pointer overflow), among others.
- Report formats are `--format` text, csv and json, plus an `ikos-view` web UI. SARIF is not documented. — [IKOS analyzer README](https://github.com/NASA-SW-VnV/ikos/blob/master/analyzer/README.md)

**Infer (Meta; MIT; tag v1.3.0)**
- License is MIT. `--sarif` outputs "issues in SARIF … in infer-out/report.sarif". Checkers include bufferoverrun, cost, siof, topl, Pulse and others. — [infer LICENSE](https://github.com/facebook/infer/blob/main/LICENSE); [infer-run man page](https://github.com/facebook/infer/blob/main/infer/man/man1/infer-run.txt); [infer-full man page](https://github.com/facebook/infer/blob/main/infer/man/man1/infer-full.txt)

**flawfinder (David A. Wheeler; GPL-2.0+; 2.0.20, PyPI upload 2026-05-17)**
- It is lexical pattern matching with CWE tags. Its SarifLogger emits SARIF "2.1.0" with a CWE taxonomy (CWE_v4.4.sarif). — [flawfinder.py](https://github.com/david-a-wheeler/flawfinder/blob/master/flawfinder.py); [PyPI flawfinder](https://pypi.org/project/flawfinder/)

**include-what-you-use (LLVM/NCSA license; 0.27 ↔ Clang 23)**
- Licensed under the University of Illinois/NCSA license. The version map runs Clang 21 → 0.25, 22 → 0.26, 23 → 0.27. Options include `--error`, `--error_always`, `--mapping_file`, `--no_fwd_decls` and `--cxx17ns`. No SARIF output was found in iwyu_globals.cc. — [IWYU LICENSE.TXT](https://github.com/include-what-you-use/include-what-you-use/blob/master/LICENSE.TXT); [IWYU README](https://github.com/include-what-you-use/include-what-you-use/blob/master/README.md); [iwyu_globals.cc](https://github.com/include-what-you-use/include-what-you-use/blob/master/iwyu_globals.cc)

### Inferences
- **Most deterministic for gating** (same compiler version, flags and compile_commands.json give the same output):
  - clang-tidy AST checks, compiler warnings and cppcheck. These are syntactic or bounded-dataflow checks.
  - flawfinder (lexical).
  - CodeQL queries over a fixed database.

  The path-sensitive engines (Clang Static Analyzer, GCC -fanalyzer, Infer Pulse, cppcheck value-flow) are deterministic for fixed inputs. Their findings can change with exploration budgets, timeouts and tool versions, so pin versions and budgets. The sound analyzers (IKOS, Frama-C EVA) give "proved safe" verdicts, which are the strongest deterministic evidence, but they need configuration (entry points, stubs).
- Because LLVM 23 turned all `cert-*` checks into aliases and removed `hicpp-*`, a rule map should key on the target check name (e.g. `bugprone-unused-return-value`) and keep a version-aware alias table so it still works with LLVM ≤22 installs.
- SARIF timestamps and absolute URIs (e.g. Frama-C, GCC) make byte-for-byte reproducibility harder. Normalize them, or use flags such as `-mdr-sarif-deterministic`.

### Gaps
- Not verified this session (sites blocked):
  - the Frama-C license (my prior knowledge says LGPL-2.1 for the kernel and most plugins) and the Frama-C 32 release date;
  - Infer's current C/C++ checker defaults and release date;
  - the cppcheck 2.22 release date and the version that first added SARIF;
  - the IKOS release date and whether any newer version adds SARIF.
- I did not verify Clang sanitizer docs (MSan/TSan/ASan/UBSan details) beyond GCC's list. MSan being Clang-only is inferred from its absence in GCC's docs.
- How cert-err33-c in LLVM 23 differs in configuration from bugprone-unused-return-value (the CERT-specific function list) was not checked.

## Q3. Which rules are decidable by static analysis versus needing a person

### Takeaway
- **MISRA:** formally classifies every rule as Decidable/Undecidable and as Single-TU/System scope. Directives are not classified and generally need process evidence. MISRA C++:2023 is 156 decidable, 20 undecidable, plus directives.
- **CERT:** now publishes per-rule "Detectable" and "Repairable" flags. Roughly half of CERT C rules (61/122) and under half of CERT C++ rules (38/83) are marked Detectable.

### Cited Findings
- MISRA C:2012 classifies rules (not directives) as Decidable or Undecidable, and each guideline as Single Translation Unit or System. One third-party source gives the 2012 original as Undecidable 26, STU 104, System 39 (143 rules). — [Wikipedia MISRA C](https://en.wikipedia.org/wiki/MISRA_C); [PRQA slides](https://www.slideshare.net/slideshow/misra-introduction-45757698/45757698)
- "A decidable rule [is one] a static analysis tool can check in every possible case"; for an undecidable rule, "no algorithm can be guaranteed to resolve the property check definitively". System-scope compliance "can only be established as a property of an entire system". — [Polyspace coverage page](https://www.mathworks.com/help/bugfinder/ug/polyspace_coverage_coding_standard.html); [Qt: MISRA C:2023 undecidability](https://www.qt.io/software-insights/misra-c2023-guidelines-the-intricacies-of-undecidability); [GrammaTech whitepaper](https://www.grammatech.com/wp-content/uploads/2023/03/GrammaTech-How_to_Avoid_Common_Pitfalls_in_MISRA_Compliance-T_Whitepaper-CSO-19.pdf)
- MISRA C++:2023: 156 Decidable and 20 Undecidable rules, 3 unclassified, and 4 directives marked not supportable by CodeQL. — [codeql-coding-standards rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv)
- AUTOSAR C++14 has its own "Enforcement level" column. CodeQL's data lists 329 Automated, 22 Partially Automated and 46 Non-Automated. — [rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv)
- CERT's "Detectable" splits (my parse): C rules 61 Yes / 60 No; C recommendations 69 Yes / 112 No; C++ rules 38 Yes / 45 No. Detectable means a tool can "automatically determine if code violates this guideline with high accuracy and precision". — [SEI organization page](https://github.com/cmu-sei/secure-coding-standards/blob/main/content/4.sei-cert-c-coding-standard/02.front-matter/2.introduction/03.how-this-coding-standard-is-organized.md); [SEI repo](https://github.com/cmu-sei/secure-coding-standards/tree/main/content)
- CodeQL's own supportability for CERT C is 101 Yes, 20 "OutOfScope" and 2 No. For CERT C++ it is 82 Yes and 1 No. — [rules.csv](https://github.com/github/codeql-coding-standards/blob/main/rules.csv)
- The C++ Core Guidelines give each rule an "Enforcement" paragraph (443 mentions). Several say enforcement is not possible or needs heuristics; I did not tally them. — [CppCoreGuidelines.md](https://github.com/isocpp/CppCoreGuidelines/blob/master/CppCoreGuidelines.md)

### Inferences
- A practical triage for deterministic checks:
  - (a) MISRA Decidable + STU rules and CERT "Detectable: Yes" rules become blocking checks.
  - (b) Undecidable and System rules become warnings with evidence of whole-program analysis (CodeQL, IKOS, Frama-C).
  - (c) Directives and CERT "Detectable: No" rules become human-review checklist items with recorded deviations, following MISRA Compliance:2020.
- The CodeQL rules.csv file (MIT) is a ready-made, machine-readable crosswalk. It covers rule ID, obligation, decidability or enforcement level, supportability and "Similar" cross-references (e.g. AUTOSAR A0-1-1 ↔ M0-1-6) for AUTOSAR, CERT C/C++, MISRA C:2012/2023 and MISRA C++:2023.

### Gaps
- No authoritative per-rule decidability list for MISRA C:2023/2025 was obtained (only the C:2012 aggregate and the C++:2023 per-rule data via CodeQL).
- CERT's "Detectable" flag is an expert judgment, not a formal decidability proof.

## Q4. Which of these tools produce SARIF

### Takeaway
Native SARIF output: GCC (2.1.0; 2.2 experimental), the Clang Static Analyzer, cppcheck, CodeQL, flawfinder, Infer, Semgrep/Opengrep (by prior knowledge; not re-verified) and Frama-C via mdr (incomplete). No native SARIF: clang-tidy (use the MIT `clang-tidy-sarif` converter), IKOS (text/csv/json) and IWYU.

### Cited Findings
- **GCC:** `-fdiagnostics-format=sarif-stderr|sarif-file` (SARIF 2.1.0) and `-fdiagnostics-add-output=sarif:version=…` (2.2 experimental). — [gcc invoke.texi](https://github.com/gcc-mirror/gcc/blob/master/gcc/doc/invoke.texi)
- **Clang Static Analyzer:** `-analyzer-output=sarif` and `sarif-html`. — [Analyses.def](https://github.com/llvm/llvm-project/blob/release/23.x/clang/include/clang/StaticAnalyzer/Core/Analyses.def)
- **clang-tidy:** no SARIF option in LLVM 23; the converter is `clang-tidy-sarif` (sarif-rs, MIT). — [ClangTidyMain.cpp](https://github.com/llvm/llvm-project/blob/release/23.x/clang-tools-extra/clang-tidy/tool/ClangTidyMain.cpp); [sarif-rs](https://github.com/psastras/sarif-rs)
- **cppcheck:** `--output-format=sarif`. — [cmdlineparser.cpp](https://github.com/danmar/cppcheck/blob/main/cli/cmdlineparser.cpp)
- **CodeQL:** `--format=sarifv2.1.0`, the only format allowed for functional-safety use of the coding-standards packs. — [user_manual.md](https://github.com/github/codeql-coding-standards/blob/main/docs/user_manual.md)
- **flawfinder:** SARIF 2.1.0 with a CWE taxonomy. — [flawfinder.py](https://github.com/david-a-wheeler/flawfinder/blob/master/flawfinder.py)
- **Infer:** `--sarif` writes infer-out/report.sarif. — [infer-run man page](https://github.com/facebook/infer/blob/main/infer/man/man1/infer-run.txt)
- **Frama-C:** `-mdr-gen sarif`, with `-mdr-sarif-deterministic`; described as incomplete. — [mdr_params 32.0](https://ocaml.org/p/frama-c/32.0/doc/src/frama-c-markdown-report.core/mdr_params.ml.html); [mdr plugin page](https://git.frama-c.com/pub/pub.frama-c.com/-/raw/cfdbccb5e4733dc8ba91735d18bd5f6760a0fecb/_fc-plugins/markdown-report.md)
- **IKOS:** formats text, csv and json only (documented). — [IKOS analyzer README](https://github.com/NASA-SW-VnV/ikos/blob/master/analyzer/README.md)

### Inferences
- A single SARIF 2.1.0 ingestion path, with small adapters for clang-tidy (YAML or text), IKOS (json) and IWYU (text), would cover every tool here. CWE IDs arrive natively from GCC -fanalyzer, cppcheck (xml `cwe`; SARIF mapping not checked), flawfinder and CodeQL (tags).

### Gaps
- I did not verify Semgrep/Opengrep `--sarif` or the exact SARIF rule-ID and CWE property layout for cppcheck, Infer and GCC.
