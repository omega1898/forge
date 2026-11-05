# Weakness taxonomies, secure-coding references, rule registries, SAST benchmarks and SARIF: what can be bundled, and how to normalize many checkers into one view (status as of 2026-11-05)

Method note: this session's network policy blocked cwe.mitre.org, capec.mitre.org, semgrep.dev, docs.semgrep.dev, samate.nist.gov, docs.oasis-open.org, osv.dev, it-cisq.org and iso.org (curl got "CONNECT tunnel failed, response 403" and WebFetch got "ENOTFOUND"). raw.githubusercontent.com was reachable. So most licence texts below were read verbatim from the publishers' own GitHub repositories, and the CWE data came from MITRE's CWE 5.0 XML. That XML was fetched from a third-party mirror that pins MITRE's file by hash and was parsed locally. Facts about blocked sites come from search-engine snippets, which are marked as such.

## Q1. CWE (MITRE): terms of use, downloads, Top 25, views relevant to code quality, mappings to CERT and OWASP; CAPEC

### Takeaway
CWE may be copied, bundled and redistributed in commercial software, provided every copy reproduces MITRE's copyright designation and the licence paragraph. The current release is **CWE 5.0 (2026-10-28) with XML Schema 8.0**. It has breaking changes: "categories" are now called "Groupings". It holds 984 weaknesses, 431 groupings, 62 views and 1,081 external references. The XML carries the machine-readable crosswalks that matter here: CISQ/ISO 5055 views 1305–1309, CERT views and per-weakness CERT rule IDs with a fit grade, OWASP Top Ten 2021 and 2025 views, and the 2025 Top 25 view 1435.

### Cited Findings
**Terms of use (verbatim, from the TERMS.txt copy kept beside MITRE's file):**
- "CWE™ is free to use by any organization or individual for any research, development, and/or commercial purposes, per these CWE Terms of Use. Accordingly, The MITRE Corporation hereby grants you a non-exclusive, royalty-free license to use CWE for research, development, and commercial purposes. Any copy you make for such purposes is authorized on the condition that you reproduce MITRE's copyright designation and this license in any such copy. CWE is a trademark of The MITRE Corporation." — [lmodel/cwe upstream/TERMS.txt (copy of cwe.mitre.org/about/termsofuse.html)](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/TERMS.txt); original page: [cwe.mitre.org/about/termsofuse.html](https://cwe.mitre.org/about/termsofuse.html) (blocked here; a search snippet confirms the "non-exclusive, royalty-free license … research, development, and commercial purposes" wording and gives a last-updated date of July 20, 2023)
- The terms also include an "as is" disclaimer with no warranty. Contributors grant all users a "perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable license to reproduce, prepare derivative works of… and distribute such information and all derivative works." — [TERMS.txt copy](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/TERMS.txt)
- The copyright designation to reproduce, as used by the mirror: "Copyright (c) 2006-2026, The MITRE Corporation. CWE, CWSS, CWRAF, and the CWE logo are trademarks of The MITRE Corporation." — [lmodel/cwe NOTICE](https://raw.githubusercontent.com/lmodel/cwe/main/NOTICE)

**Current version and machine-readable format:**
- The CWE 5.0 XML root reads `<Weakness_Catalog Name="CWE" Version="5.0" Date="2026-10-28" xmlns="http://cwe.mitre.org/cwe-8" … cwe_schema_v8.0.xsd>`. Its top-level sections are Weaknesses (984), Groupings (431), Views (62) and External_References (1081). The file is `cwec_v5.0.xml.zip`, 2,119,263 bytes, SHA-256 `bcda44c9abf61ff977f50c3c6649eea5a97a7b0626b0396c2c3ec4bfb973a3dc`, and unzips to a 19.1 MB XML. It was parsed locally. — [mirror of MITRE file](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip); official downloads page [cwe.mitre.org/data/downloads.html](https://cwe.mitre.org/data/downloads.html) (blocked here)
- MITRE's site announced "CWE 5.0 and Schema 8.0 – With Breaking Changes – to Be Released on October 28, 2026". The prior schema was 7.3, from the 2025 cycle. — [search snippet of cwe.mitre.org and news2026](https://cwe.mitre.org/news/archives/news2026.html); [vulnerability-lookup PR #759 "support CWE 5.0 and schema 8"](https://github.com/vulnerability-lookup/vulnerability-lookup/pull/759) (reports its existing importer failing on CWE-1461)
- The mirror notes that the file behind MITRE's `cwe_schema_latest.xsd` link was an earlier copy of 8.0 that the 5.0 catalogue breaks in 69 places. Validate against `cwe_schema_v8.0.xsd`, not "latest". — [lmodel/cwe docs/about.md](https://raw.githubusercontent.com/lmodel/cwe/main/docs/about.md)
- A secondary source says MITRE planned JSON downloads. I could not confirm that JSON shipped with 5.0. — [mureinik/cwe-visualizer issue #210 (search snippet)](https://github.com/mureinik/cwe-visualizer/issues/210)
- Earlier 2025–26 releases: CWE 4.18 was announced in September 2025. CWE 4.20 reportedly added a view for AI-related weaknesses, which appears in 5.0 as view 1448 "Weaknesses Related to AI/ML Products". — [cwe-research-list post (search snippet)](https://www.mail-archive.com/cwe-research-list@mitre.org/msg00130.html); [PulseAugur (search snippet)](https://www.pulseaugur.com/cluster/10819); view 1448 confirmed in the [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**Per-entry metadata useful for normalization (counts from parsing CWE 5.0):**
- Abstraction: Base 536, Variant 300, Class 131, Pillar 10, Compound 7. Status: Incomplete 498, Draft 432, Deprecated 28, Stable 26. 605 weaknesses have `Detection_Methods`. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- `Mapping_Notes/Usage`: Allowed 755, Allowed-with-Review 100, Prohibited 85, Discouraged 44. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**2025 CWE Top 25:**
- Published by CISA and MITRE on 2025-12-11. It is based on 39,080 CVE records from June 2024 to June 2025, weighted by prevalence, severity and KEV appearances. CWE mappings were used as provided by CNAs, without normalizing them to View-1003. — [CISA bulletin](https://content.govdelivery.com/accounts/USDHSCISA/bulletins/3ff772a); [MITRE 2025 key insights (search snippet)](https://cwe.mitre.org/top25/archive/2025/2025_key_insights.html)
- Members of view 1435 "Weaknesses in the 2025 CWE Top 25", in XML order: 79, 89, 352, 862, 787, 22, 416, 125, 78, 94, 120, 434, 476, 121, 502, 122, 863, 20, 284, 200, 306, 918, 77, 639, 770. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip). The XML order matches published ranks wherever secondary sources give them: 79 #1, 89 #2, 352 #3, Missing Authorization #4, 787 #5, 416 #7, 78 #9. — [SecurityWeek](https://www.securityweek.com/mitre-releases-2025-list-of-top-25-most-dangerous-software-vulnerabilities/); [@IT](https://atmarkit.itmedia.co.jp/ait/articles/2601/08/news021.html)
- Earlier Top 25 views also ship: 1430 (2024), 1425 (2023), 1387 (2022), 1337 (2021), 1350 (2020), 1200 (2019). — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**Views relevant to code quality (parsed from CWE 5.0; type and status as given in the XML):**
- 1000 "Complete Weakness Hierarchy" (Research view): Graph, 10 top-level pillars. 699 "Software Development": 40 groupings covering 405 entries. 1003 "Weaknesses for Simplified Mapping of Published Vulnerabilities": 37 entries. 1400 "Comprehensive Categorization for Software Assurance Trends": 22 groupings covering 958 weaknesses, nearly the whole catalogue. 1040 "Quality Weaknesses with Indirect Security Impacts" (implicit). 884 "CWE Cross-section". 700 "Seven Pernicious Kingdoms". — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- CISQ: 1305 "CISQ Quality Measures (2020)" holds groupings 1306 Reliability (34 member entries), 1307 Maintainability (28), 1308 Security (34) and 1309 Efficiency (15). 1128 "CISQ Quality Measures (2016)" holds 1129–1132. 1340 "CISQ Data Protection Measures" has 36 entries. A search snippet of the CWE site marks 1305 as prohibited for mapping real-world vulnerabilities. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip); [cwe.mitre.org/data/definitions/1305.html (search snippet)](https://cwe.mitre.org/data/definitions/1305.html)
  - 1306 Reliability members: 119 170 252 390 394 404 424 459 476 480 484 562 595 662 665 672 681 682 703 704 758 835 908 1045 1051 1066 1070 1077 1079 1082 1083 1087 1088 1098
  - 1307 Maintainability members: 407 478 480 484 561 570 571 783 1041 1045 1047 1048 1051 1052 1054 1055 1062 1064 1074 1075 1079 1080 1084 1085 1086 1087 1090 1095
  - 1308 Security members: 22 77 79 89 90 91 99 119 129 134 252 404 424 434 477 480 502 570 571 606 611 643 652 662 665 672 681 682 732 778 783 789 798 835
  - 1309 Efficiency members: 404 424 1042 1043 1046 1049 1050 1057 1060 1067 1072 1073 1089 1091 1094
- CERT: 1154 "SEI CERT C Coding Standard" (Stable; 17 groupings covering 78 entries). 1133 "SEI CERT Oracle Coding Standard for Java" (Stable; 21 groupings covering 88 entries). 868 "SEI CERT C++ (2016)" (Obsolete; 95 entries). 1178 "SEI CERT Perl" (Stable; 27 entries). 734 "CERT C (2008)" and 844 "CERT Oracle Java (2011)" are Obsolete. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- OWASP: 1344 "OWASP Top Ten (2021)" and 1450 "Weaknesses in OWASP Top Ten RC1 (2025)". View 1450 has groupings 1436–1445 with member counts A01 40, A02 16, A03 6, A04 32, A05 37, A06 39, A07 36, A08 14, A09 5, A10 24. Older views remain: 1026 (2017), 928 (2013), 809 (2010), 629 (2007), 711 (2004). — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- Other new crosswalk views: 1449 "OpenSSF Secure Coding Guide for Python (2026)" (48 entries), 1468 "OWASP Golang Secure Coding Practices Guide" (27), 1460 "Top 20 Secure PLC Coding Practices" (13), 1424 "ISA/IEC 62443 Requirements", 1448 "AI/ML Products" (18 entries under 2 groupings). — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**Per-weakness `Taxonomy_Mappings` (the CWE→CERT/OMG crosswalk, with a fit grade):**
- Mapping counts by taxonomy (mappings / distinct weaknesses): Software Fault Patterns 304/304; PLOVER 291/289; CERT C Secure Coding 227/118; ISA/IEC 62443 199/39; CERT Oracle Java (2011) 139/106; CLASP 105/104; 7PK 87/87; OWASP Top Ten 2004 68/61; WASC 55/52; OpenSSF Secure Coding Guide for Python 50/48; OWASP Top Ten 2007 31/27; OMG ASCRM 29/28; SEI CERT Perl 29/26; OWASP Golang SCP 27/27; OMG ASCSM 22/22; OMG ASCMM 20/20; PLC Top 20 20/13; OMG ASCPEM 15/15; Landwehr 9/9; SEI CERT Oracle Java 8/7. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- Each mapping carries Entry_ID, Entry_Name and Mapping_Fit. Examples: CWE-476 → CERT C EXP34-C "Do not dereference null pointers" (Exact). CWE-134 → FIO30-C (Exact), FIO47-C (CWE More Specific), IDS06-J, IDS30-PL (Exact), ASCSM-CWE-134, pyscg-0008. CWE-190 → INT30-C and INT18-C (CWE More Abstract), INT32-C (Imprecise). — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**CAPEC:**
- CAPEC 3.9 is the version the CWE mirror cross-references. Its terms are parallel to CWE's: "The MITRE Corporation (MITRE) hereby grants you a non-exclusive, royalty-free license to use Common Attack Pattern Enumeration and Classification (CAPEC™) for research, development, and commercial purposes. Any copy you make for such purposes is authorized provided that you reproduce MITRE's copyright designation and this license in any such copy." The designation is "Copyright (c) 2007-2026, The MITRE Corporation." — [lmodel/cwe NOTICE quoting capec.mitre.org/about/termsofuse.html](https://raw.githubusercontent.com/lmodel/cwe/main/NOTICE)

### Inferences
- **Bundling verdict:** CWE (XML plus derived JSON) and CAPEC can ship inside an offline assistant. Keep a NOTICE that reproduces the MITRE copyright line and licence paragraph verbatim, and avoid implying MITRE endorsement or using the CWE logo. The licence says nothing about modification beyond derivative-work language in the contributor clause, so mark transformed data as derived.
- **Normalization backbone:** Use view 1000's ChildOf graph to roll any tool's CWE up or down. Prefer entries whose Mapping Usage is "Allowed", and flag Discouraged or Prohibited ones (Pillars, groupings, views) as low-specificity. Answer "is this finding in Top 25 / CISQ-Security / OWASP A05:2025 / CERT C?" by membership tests on views 1435, 1308, 1440 and 1154 respectively.
- **CERT crosswalk:** CERT rule IDs come straight from `Taxonomy_Mappings`, with Mapping_Fit as a confidence signal. Only "Exact" fits should be presented as equivalent.
- **Migration risk:** CWE 5.0 renamed categories to groupings and changed the namespace to `cwe-8`. Pin a specific `cwec_vX.Y.xml` by SHA-256 rather than "latest", for deterministic builds.

### Gaps
- I could not open cwe.mitre.org to confirm whether 5.0 also ships CSV or JSON downloads; CSV per view existed in 4.x. Treat the formats as XML plus XSD (confirmed) and others as unverified.
- View 1450 is titled "RC1 (2025)" in CWE 5.0, while OWASP released Top 10:2025 Final. Whether MITRE updated memberships to the final list is unverified. The counts differ slightly from OWASP's own intro text (A03: 6 vs 5; A05: 37 vs 38; see Q3).
- The exact 2025 Top 25 scores and KEV counts per entry were not retrieved, only the ordering.

## Q2. CISQ / OMG Automated Source Code Quality Measures = ISO/IEC 5055:2021

### Takeaway
ISO/IEC 5055:2021 counts severe CWE weaknesses for four ISO 25010 characteristics (Reliability, Security, Performance Efficiency, Maintainability). The ISO text is paid. The content-identical OMG ASCQM 1.1 (July 2022) is free as PDF plus XMI. The CWE IDs that make up each measure ship inside CWE (views 1305–1309), so an assistant can tag findings with ISO 5055 measures without bundling ISO text.

### Cited Findings
- ISO 5055 covers reliability, security, performance efficiency and maintainability. Each measure is a count of severe CWE weaknesses detectable by static analysis. "The OMG and ISO Automated Source Code Quality Measures standards are identical." — [CISQ, Software Quality Standards – ISO 5055 (search snippet)](https://www.it-cisq.org/standards/code-quality-standards/)
- OMG ASCQM is "Version 1.1, Document Status formal, Publication Date July 2022", with a PDF and a machine-readable XMI. It is freely available, while the ISO edition is sold (ANSI and IEC stores offer previews only). — [OMG ASCQM (search snippet)](https://www.omg.org/spec/ASCQM/); [ANSI preview](https://webstore.ansi.org/preview-pages/ISO/preview_ISO+IEC+5055-2021.pdf)
- Size: 138 severe structural weaknesses in total. Per CISQ, Security has 73, Reliability 74, Performance Efficiency 18 and Maintainability 29, with "38 weaknesses" shared by reliability and security. — [CISQ response hosted by NIST (search snippet)](https://www.nist.gov/document/responses-enhancing-software-supply-chain-security-cisq); [CISQ "Measuring the Structural Quality of Software Systems"](https://www.it-cisq.org/wp-content/uploads/sites/6/2022/09/Measuring-the-Structural-Quality-of-Software-Systems.pdf)
- In CWE 5.0, view 1305 "CISQ Quality Measures (2020)" lists 34 / 28 / 34 / 15 member entries for Reliability, Maintainability, Security and Efficiency (full ID lists in Q1). The 2016 version (view 1128) lists 28 / 20 / 22 / 14. CWE weaknesses also carry OMG IDs such as `ASCSM-CWE-134` under taxonomies "OMG ASCSM / ASCRM / ASCMM / ASCPEM". — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)
- "MITRE has included all ISO/IEC 5055 weakness[es] in the Common Weakness Enumeration Repository." — [search snippet, CISQ/CWE pages](https://cwe.mitre.org/data/definitions/1305.html)

### Inferences
- The gap between CISQ's per-measure totals (74/73/18/29) and the CWE 1305 member counts (34/34/15/28) most likely means the ISO measures also count child weaknesses of listed parents, or count contributing weaknesses separately. This is an inference, not verified. An assistant should claim "ISO 5055-aligned tagging via CWE view 1305", never "ISO 5055 conformance", unless it implements the ASCQM detection patterns from the free OMG spec.
- Bundling: CWE view IDs and memberships are under CWE terms and can be bundled. OMG ASCQM text is free to download, but I did not verify its redistribution licence, so reference it by URL. The ISO text must not be bundled.

### Gaps
- I could not fetch the OMG ASCQM PDF or XMI licence terms. OMG specifications usually carry their own copyright and licence page, but this was not verified.
- I did not find any open-source tool that verifiably claims ISO 5055 measurement. Commercial vendors' claims were not researched for lack of budget, so treat this as open.

## Q3. OWASP: ASVS 5.0, Top 10 (2021/2025), Cheat Sheet Series, Benchmark, Dependency-Check

### Takeaway
OWASP text content (ASVS, Top 10, Cheat Sheets) is CC BY-SA 4.0. It can be redistributed, but the bundled content and any adaptations must be attributed and shared alike; program code is unaffected only if the content is kept as a separable data file. ASVS 5.0.0 ships ready-made JSON, flat JSON, XML, CSV and CycloneDX 1.6 exports. Top 10:2025 is final and supersedes 2021. OWASP Benchmark is GPL-2.0, so use it as an external test target rather than bundling it. Dependency-Check is Apache-2.0 and emits SARIF.

### Cited Findings
**ASVS:**
- ASVS 5.0.0 is the "Latest Stable Version … (dated May 2025)", "Released LIVE on stage at Global AppSec EU Barcelona 2025". The next release is planned as patch 5.0.1. "The entire project content is under the Creative Commons Attribution-Share Alike v4.0 license." — [OWASP/ASVS README](https://raw.githubusercontent.com/OWASP/ASVS/master/README.md); [LICENSE.md (CC BY-SA 4.0)](https://raw.githubusercontent.com/OWASP/ASVS/master/LICENSE.md)
- Stable citation format: requirement IDs should be cited as `v<version>-<chapter>.<section>.<requirement>`, e.g. `v5.0.0-1.2.5` (OS command injection). — [OWASP/ASVS README](https://raw.githubusercontent.com/OWASP/ASVS/master/README.md)
- Machine-readable exports at tag v5.0.0 (all fetched, HTTP 200): `…_5.0.0_en.json` (149 KB), `.flat.json` (163 KB), `.xml` (129 KB), `.cdx.json` (160 KB; CycloneDX specVersion 1.6 "declarations.standards", licence id CC-BY-SA-4.0, supplier OWASP Foundation, timestamp 2025-06-12) and `.csv` (columns chapter_id, chapter_name, section_id, section_name, req_id, req_description, L). Parsing the JSON gives 17 chapters and 345 requirements, each with a level "L". — [ASVS 5.0.0 docs_en JSON](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.json); [CSV](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv); [CycloneDX](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.cdx.json)
- The ASVS 5.0.0 JSON contains no "CWE" strings (grep count 0), so this release's exports carry no CWE mapping. — [ASVS 5.0.0 JSON](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/docs_en/OWASP_Application_Security_Verification_Standard_5.0.0_en.json)

**Top 10:**
- "OWASP Top 10 2025 - RELEASED … Top10:2025 (Final)". The 2021 edition is marked "SUPERSEDED" and 2017 "HISTORIC". The repository LICENSE is "Creative Commons Attribution-ShareAlike 4.0 International Public License". — [OWASP/Top10 README](https://raw.githubusercontent.com/OWASP/Top10/master/README.md); [OWASP/Top10 LICENSE](https://raw.githubusercontent.com/OWASP/Top10/master/LICENSE)
- The 2025 categories are: A01 Broken Access Control; A02 Security Misconfiguration; A03 Software Supply Chain Failures; A04 Cryptographic Failures; A05 Injection; A06 Insecure Design; A07 Authentication Failures; A08 Software or Data Integrity Failures; A09 Security Logging and Alerting Failures; A10 Mishandling of Exceptional Conditions (new). — [Top10 2025 index](https://raw.githubusercontent.com/OWASP/Top10/master/2025/docs/en/index.md)
- The intro states the CWE counts per category: A01 40 (SSRF rolled in), A02 16, A03 5, A04 32, A05 38, A07 36, A10 24. Each category page has a "List of Mapped CWEs" section in markdown, for example A10 lists CWE-209, 215, 234, 235 and others. — [Top10 2025 introduction](https://raw.githubusercontent.com/OWASP/Top10/master/2025/docs/en/0x00_2025-Introduction.md); [A10 page](https://raw.githubusercontent.com/OWASP/Top10/master/2025/docs/en/A10_2025-Mishandling_of_Exceptional_Conditions.md)
- The CWE side has view 1450 ("OWASP Top Ten RC1 (2025)") and 1344 (2021). Membership counts are in Q1. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**Cheat Sheet Series:**
- Licensed CC BY-SA 4.0 (`SPDX-License-Identifier: CC-BY-SA-4.0`). — [OWASP/CheatSheetSeries LICENSE.md](https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/LICENSE.md)

**OWASP Benchmark:**
- The repo moved to the OWASP-Benchmark organization (BenchmarkJava, BenchmarkUtils). "The current latest release is v1.2". The latest content is always the head of the repository. It is a runnable web app scored by SAST, DAST and IAST tools, and BenchmarkUtils holds the scorecard generators. — [BenchmarkJava README](https://raw.githubusercontent.com/OWASP-Benchmark/BenchmarkJava/master/README.md); [OWASP/Benchmark README (moved notice)](https://raw.githubusercontent.com/OWASP/Benchmark/master/README.md)
- Licence: GNU GPL v2. — [BenchmarkJava LICENSE](https://raw.githubusercontent.com/OWASP-Benchmark/BenchmarkJava/master/LICENSE)
- v1.2 has 2,740 test cases, each a servlet that is either a true vulnerability or a false-positive trap for one CWE. Ground truth is in `expectedresults-1.2.csv`. The split is roughly 51.6% true and 48.4% false. — [OWASP Benchmark project page (search snippet)](https://owasp.org/projects/benchmark); [emergentmind summary (secondary)](https://www.emergentmind.com/topics/owasp-benchmark-project-1-2)

**Dependency-Check:**
- Licence Apache-2.0. CLI output formats are "HTML, XML, CSV, JSON, JUNIT, SARIF, JENKINS, GITLAB, ALL". — [DependencyCheck LICENSE.txt](https://raw.githubusercontent.com/jeremylong/DependencyCheck/main/LICENSE.txt); [arguments.md](https://raw.githubusercontent.com/jeremylong/DependencyCheck/main/cli/src/site/markdown/arguments.md)

### Inferences
- **Bundling verdicts:**
  - ASVS, Top 10 and Cheat Sheets can be bundled as separate data files with CC BY-SA attribution and a licence link. Derived summaries or rewrites also become CC BY-SA. Keep the assistant's own code separate and mark adapted text.
  - Do not bundle the GPL-2.0 Benchmark in a non-GPL distribution. Download it at evaluation time and use `expectedresults-1.2.csv` to compute TPR and FPR per CWE (78, 79, 89, 90, 22, 327, 328, 330, 501, 614, 643 are typical; see Gaps).
- ASVS 5.0 has no CWE column, so an ASVS↔CWE crosswalk must come from elsewhere (for example OpenCRE, not researched here) or be authored and labelled as the assistant's own mapping.
- Use the ASVS `v5.0.0-x.y.z` ID form in findings for stability across versions.

### Gaps
- The full per-CWE breakdown of Benchmark v1.2 was not retrieved; only a partial table appeared in search snippets. The CWE list in the inference above is from memory and must be checked against `expectedresults-1.2.csv`.
- I did not verify whether the ASVS 5.0 CSV or JSON has a separate CWE mapping file elsewhere in the repo (4.0.x had a CWE column).
- The Dependency-Check current version number was not retrieved.

## Q4. NIST SAMATE: SARD, Juliet (C/C++, Java), SATE

### Takeaway
Juliet 1.3 (October 2017) is a US-government public-domain corpus of tens of thousands of small labelled good/bad test cases. The C/C++ suite has 64,099 cases across 118 CWEs and the Java suite has 28,886. It is the most legally bundleable benchmark for measuring an assistant's checkers, though it is synthetic and has known systematic defects. SATE VI is the latest exposition found.

### Cited Findings
- Juliet C/C++ 1.3 has 64,099 test cases and more than 100,000 files, organized under 118 CWEs. The Java suite has 28,886 test cases. "Not subject to copyright protection and is in the public domain." NIST additionally offers CC0 1.0 "to the extent NIST holds any foreign rights". — [samate.nist.gov releaseJuliet1.3Doc.txt (search snippet; site blocked)](https://samate.nist.gov/SARD/downloads/documents/releaseJuliet1.3Doc.txt). The CC0 statement could not be confirmed by a second search: [SATE search results](https://www.nist.gov/itl/csd/secure-systems-and-applications/static-analysis-tool-exposition-sate-vi)
- Juliet 1.3 was released in October 2017 and added increment and decrement overflow cases (CWE-190/191). It fixed systematic bugs, including about 104 C cases that contained no real buffer overflow. NIST acknowledges remaining problems such as "hardcoded values are all over the place". — [Juliet 1.3 Changes From 1.2 (search snippet)](https://samate.nist.gov/SARD/downloads/documents/Juliet_1.3_Changes_From_1.2.pdf)
- A community mirror adds per-test-case good and bad executables, a CMake build and a run harness, with timeouts that record exit code 124. — [arichardson/juliet-test-suite-c README](https://raw.githubusercontent.com/arichardson/juliet-test-suite-c/master/README.md)
- SATE VI: the C and Java tracks merged into a "Classic Track" with injected realistic bugs, alongside a Mobile track and an "Ockham Sound Analysis" track. In Ockham, Astrée was run on 28 sets of Juliet 1.3 C test cases (18,954 buggy sites) and reported all of them; only Astrée and Frama-C met the criteria. "Juliet 2.0 is in development", with C# planned. NIST also states that Juliet 1.3 has over 140,000 cases across C, C++, Java, PHP, C# and Python (search-snippet summary). — [NIST SATE VI page (search snippet)](https://www.nist.gov/itl/csd/secure-systems-and-applications/static-analysis-tool-exposition-sate-vi); [SATE VI Ockham presentation](https://www.nist.gov/document/sate-vi-presentation-08ockham); [CSIAC article](https://csiac.dtic.mil/articles/improving-software-assurance-through-static-analysis-tool-expositions)

### Inferences
- Juliet can be shipped or used offline as a regression corpus. Each case's CWE label (in the file and directory name) plus the good and bad function split gives ground truth for per-CWE recall and false-positive rate. Because it is synthetic and has known defects, report Juliet scores alongside a realistic corpus such as OWASP Benchmark or SATE Classic test cases, never alone.
- The "64,099 C/C++ + 28,886 Java" figure and the "over 140,000 across six languages" figure are not contradictory. The latter counts the whole SARD Juliet family, including C#, PHP and Python sets. This is an inference; the composition was not verified.

### Gaps
- samate.nist.gov was blocked, so current SARD terms, Juliet C# 1.3 details, any Juliet 2.0 release and SATE VII status are unverified.
- The licence of the SATE VI Classic track test programs (real open-source projects with injected bugs) inherits from those projects and was not checked.

## Q5. ISO/IEC TR 24772-1 (language-independent programming-language vulnerabilities)

### Takeaway
TR 24772-1:2019 is withdrawn and replaced by ISO/IEC 24772-1:2024. No free official copy was found. Treat it as reference-only: cite clause names, do not bundle text.

### Cited Findings
- ISO/IEC TR 24772-1:2019 has been withdrawn since 2024-10-29 and is "revised by ISO/IEC 24772-1:2024". It is sold through the IEC webstore and national bodies, and one reseller's single-user licence forbids reproduction. Only previews are free. — [GlobalSpec listing](https://standards.globalspec.com/std/14226719/iso-iec-tr-24772-1); [IEC webstore](https://webstore.iec.ch/publication/66282); [iTeh preview](https://iteh.es/catalog/standards/iso/2160a28c-6e50-48b8-a921-bfd9b239bfd0/iso-iec-tr-24772-1-2019)
- Scope: generic descriptions of programming-language vulnerabilities for security-, safety- and mission-critical systems. Language-specific parts are separate documents in the 24772 series. — [GlobalSpec listing](https://standards.globalspec.com/std/14226719/iso-iec-tr-24772-1)

### Inferences
- An assistant can map its findings to 24772 vulnerability identifiers only if the user supplies a licensed copy. The "licensed-standard import" pattern applies: ship no text, and store user-imported content locally.

### Gaps
- Whether ISO lists 24772-1 among its free "Publicly Available Standards" was not confirmed; the search found none. Language-part status (-2 Ada, -3 C, -4 Python, …) was not researched.

## Q6. Open rule registries and engines: Semgrep, Opengrep, CodeQL, SonarSource, Bearer, ast-grep, tree-sitter

### Takeaway
Licences differ sharply in what an offline assistant may bundle:
- **Can bundle:** CodeQL queries (MIT), ast-grep engine (MIT), tree-sitter and core grammars (MIT), ast-grep-essentials rules (Apache-2.0), and the Semgrep and Opengrep engines (LGPL-2.1, with LGPL obligations).
- **Cannot bundle:** Semgrep registry rules (internal use only; no distribution or SaaS) and the CodeQL CLI (no redistribution; open-source codebases only, or academic research). Opengrep-rules (LGPL-2.1 plus Commons Clause, "for research, testing & benchmarking") cannot be sold. Bearer rules and CLI (ELv2) cannot be offered as a hosted service. Sonar analyzers (Sonar Source-Available License v1.0) explicitly exclude use by "artificial intelligence technology that is not part of the Program", which is directly fatal for an AI coding assistant.

### Cited Findings
**Semgrep:**
- `semgrep-rules` LICENSE: "Semgrep Rules License v1.0. For more details, visit https://semgrep.dev/legal/rules-license". — [semgrep-rules LICENSE](https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/LICENSE)
- Licence text (ScanCode LicenseDB copy; category "Proprietary Free"; key `LicenseRef-scancode-semgrep-rules-1.0`):
  - "non-exclusive, royalty-free, worldwide, non-sublicensable, non-transferable license to use the rules…"
  - "**You may use the rules only for your own internal business purposes. This license does not allow you to distribute the rules, or to make them available to others as a service.**"
  - Notices must be kept, and modified copies must say they were modified.
  - The patent licence terminates on a patent claim. — [ScanCode semgrep-rules-1.0.LICENSE](https://raw.githubusercontent.com/aboutcode-org/scancode-toolkit/develop/src/licensedcode/data/licenses/semgrep-rules-1.0.LICENSE)
- Semgrep's docs say the licence covers Community and Pro rules in semgrep-rules; vendors "cannot use Semgrep-maintained rules in competing products or SaaS offerings". Third-party registry rules keep their source licences, for example Trail of Bits rules under AGPL-3.0. Vendors had a grace period until 2025-01-31. — [Semgrep licensing docs (search snippet; site blocked)](https://docs.semgrep.dev/licensing); [Semgrep blog "Important updates to Semgrep OSS" (search snippet)](https://semgrep.dev/blog/2024/important-updates-to-semgrep-oss)
- The rule metadata carries standard mappings: for example `metadata.cwe: "CWE-78: Improper Neutralization of Special Elements used in an OS Command ('OS Command Injection')"`, plus `owasp`, `confidence` and `likelihood`. — [subprocess-shell-true.yaml](https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/python/lang/security/audit/subprocess-shell-true.yaml)
- Engine: GNU LGPL v2.1. The latest CHANGELOG entry is 1.183.0, dated 2026-10-29. SARIF output via `--sarif` / `--sarif-output`. — [semgrep LICENSE](https://raw.githubusercontent.com/semgrep/semgrep/develop/LICENSE); [semgrep CHANGELOG](https://raw.githubusercontent.com/semgrep/semgrep/develop/CHANGELOG.md)

**Opengrep:**
- Engine licence LGPL-2.1. Outputs JSON and SARIF (`opengrep scan --sarif-output=sarif.json -f rules code`). — [opengrep LICENSE](https://raw.githubusercontent.com/opengrep/opengrep/main/LICENSE); [opengrep README](https://raw.githubusercontent.com/opengrep/opengrep/main/README.md)
- Latest CHANGELOG entry: 1.30.0 (07-09-2026). A Codacy post cites 1.31.0, released 2026-10-15 (unconfirmed). The changelog includes "Ensure fingerprint is unique" and "Adapt Sarif output for --taint-intrafile". — [opengrep CHANGELOG](https://raw.githubusercontent.com/opengrep/opengrep/main/CHANGELOG.md); [DEV/Codacy](https://dev.to/codacy/opengrep-vs-semgrep-some-thoughts-140m)
- Opengrep was forked from Semgrep CE (reportedly v1.100.0, January 2025) to restore cross-function and intrafile taint and fingerprinting under LGPL. — [appsecsanta (secondary)](https://appsecsanta.com/opengrep-vs-semgrep); [everydev.ai (secondary)](https://www.everydev.ai/tools/opengrep/llms.txt)
- `opengrep-rules` LICENSE: "Commons Clause" License Condition v1.0 over "Software: semgrep-rules … License: LGPL 2.1 … Licensor: Semgrep, Inc.". The clause bars selling "a product or service whose value derives, entirely or substantially, from the functionality of the Software". The README says "These rules are intended for research, testing & benchmarking." — [opengrep-rules LICENSE](https://raw.githubusercontent.com/opengrep/opengrep-rules/main/LICENSE); [opengrep-rules README](https://raw.githubusercontent.com/opengrep/opengrep-rules/main/README.md)

**CodeQL:**
- Queries and libraries (github/codeql): MIT, "Copyright (c) 2006-2025 GitHub, Inc." The README says: "The CodeQL CLI (including the CodeQL engine) is … licensed separately. If you'd like to use the CodeQL CLI to analyze closed-source code, you will need a separate commercial license." — [codeql LICENSE](https://raw.githubusercontent.com/github/codeql/main/LICENSE); [codeql README](https://raw.githubusercontent.com/github/codeql/main/README.md)
- CLI terms (verbatim excerpts):
  - Permitted uses are academic research, demonstration, testing OSI-licensed queries and, "only with an Open Source Codebase", analysis.
  - Not permitted: "use the Software in connection with any codebase that is not an Open Source Codebase (e.g., code in a private repo in GitHub)", and "share, publish, distribute or lend the Software, provide or make available the Software as a hosted solution… or transfer the Software".
  - A paid GitHub Advanced Security licence lifts the CI and non-OSS restrictions.
  - Source: [codeql-cli-binaries LICENSE.md](https://raw.githubusercontent.com/github/codeql-cli-binaries/main/LICENSE.md); [codeql-cli-binaries README](https://raw.githubusercontent.com/github/codeql-cli-binaries/main/README.md)
- Query metadata convention: `@id <lang>/<name>`, `@tags … external/cwe/cwe-563`, `@precision`, `@problem.severity`, and `@security-severity` (0.0–10.0 for `@tags security`). — [CodeQL query-metadata style guide](https://raw.githubusercontent.com/github/codeql/main/docs/query-metadata-style-guide.md)

**SonarSource:**
- Analyzers (e.g. sonar-java) use the "SONAR Source-Available License v1.0, Last Updated November 13, 2024". The grant is only "for any Non-competitive Purpose". "Non-competitive Purpose" excludes "(c) employing, using, or engaging artificial intelligence technology that is not part of the Program to ingest, interpret, analyze, train on, or interact with the data provided by the Program, or to engage with the Program in any manner", as well as offering "substantially similar functionality as SonarQube". — [sonar-java LICENSE.txt](https://raw.githubusercontent.com/SonarSource/sonar-java/master/LICENSE.txt)
- RSPEC, the rule-description repository, holds AsciiDoc rule specs for SonarLint, SonarCloud and SonarQube. A GitHub org listing shows it as LGPL-3.0, but raw fetches of every LICENSE/README path returned 404, so the repo may be private, renamed or on another branch. — [ecosyste.ms / org listing (search snippet)](https://repos.ecosyste.ms/hosts/GitHub/repositories/SonarSource%2Frspec); [TyIsI/rspec fork](https://github.com/TyIsI/rspec)

**Bearer (Cycode):**
- The CLI and the bearer-rules repo are both under Elastic License 2.0: "You may not provide the software to third parties as a hosted or managed service…". Bearer is now part of Cycode ("Bearer Pro … available through Cycode"). Its built-in rules cover the OWASP Top 10 and CWE Top 25. — [Bearer LICENSE.txt](https://raw.githubusercontent.com/Bearer/bearer/main/LICENSE.txt); [bearer-rules LICENSE.txt](https://raw.githubusercontent.com/Bearer/bearer-rules/main/LICENSE.txt); [Bearer README](https://raw.githubusercontent.com/Bearer/bearer/main/README.md)

**ast-grep:**
- Engine: MIT (Copyright 2022 Herrington Darkholme). Latest CHANGELOG entry 0.51.1. — [ast-grep LICENSE](https://raw.githubusercontent.com/ast-grep/ast-grep/main/LICENSE); [CHANGELOG](https://raw.githubusercontent.com/ast-grep/ast-grep/main/CHANGELOG.md)
- Community rules "ast-grep-essentials" (coderabbitai): Apache-2.0 security and best-practice YAML rules, organized per language. — [ast-grep-essentials LICENSE](https://raw.githubusercontent.com/coderabbitai/ast-grep-essentials/main/LICENSE); [README](https://raw.githubusercontent.com/coderabbitai/ast-grep-essentials/main/README.md)
- No SARIF string was found in ast-grep's CLI printer sources (`print/mod.rs`, `json_print.rs`). — [print/mod.rs](https://raw.githubusercontent.com/ast-grep/ast-grep/main/crates/cli/src/print/mod.rs)

**tree-sitter:**
- Core: MIT (Copyright 2018 Max Brunsfeld). The tree-sitter-python, tree-sitter-c and tree-sitter-java grammars are MIT. — [tree-sitter LICENSE](https://raw.githubusercontent.com/tree-sitter/tree-sitter/master/LICENSE); [tree-sitter-python LICENSE](https://raw.githubusercontent.com/tree-sitter/tree-sitter-python/master/LICENSE); [tree-sitter-c LICENSE](https://raw.githubusercontent.com/tree-sitter/tree-sitter-c/master/LICENSE); [tree-sitter-java LICENSE](https://raw.githubusercontent.com/tree-sitter/tree-sitter-java/master/LICENSE)

### Inferences
**Bundling matrix for an offline, possibly commercial, AI assistant:**

| Item | Licence | Bundle? | Notes |
|---|---|---|---|
| Semgrep engine | LGPL-2.1 | Yes | Ship as a separate executable; offer source; allow relinking or replacement |
| Opengrep engine | LGPL-2.1 | Yes | Same as Semgrep; restores intrafile and cross-function taint |
| Semgrep registry rules | Semgrep Rules License v1.0 | **No** | Redistribution forbidden. At most the end user fetches them for their own internal use, and even then the AI "service" angle is risky. Rules must be self-authored. |
| opengrep-rules | LGPL-2.1 + Commons Clause | Not in a sold product | Benchmarking use only |
| CodeQL queries (.ql) | MIT | Yes | Can be translated or adapted with attribution |
| CodeQL CLI | GitHub CodeQL T&C | **No** | Cannot be redistributed; analysis of closed-source code needs a licence. At most, detect a user-installed CLI and run it only where the user's licence allows. |
| Sonar analyzers | SSAL v1.0 | **No** | The AI clause excludes exactly this use |
| Bearer CLI and rules | ELv2 | Possibly, offline | Bundling inside a local, non-hosted tool appears allowed; any hosted offering is barred. Needs legal review. |
| ast-grep, tree-sitter and core grammars | MIT | Yes | |
| ast-grep-essentials | Apache-2.0 | Yes | Keep NOTICE |

- Semgrep and CodeQL rule metadata conventions (`metadata.cwe` strings; `external/cwe/cwe-NNN` tags) let a normalizer extract CWE IDs with a simple regex `CWE-(\d+)` / `cwe-(\d+)`, then validate them against the pinned CWE XML.

### Gaps
- The semgrep.dev page itself was blocked. The licence text above is ScanCode's copy, so confirm it has not changed since.
- The RSPEC licence and the licence of rules.sonarsource.com descriptions are unverified (LGPL-3.0 per a secondary listing only).
- Licences of non-core tree-sitter grammars (e.g. community Kotlin and COBOL grammars) vary and were not checked individually.
- The latest CodeQL CLI version number was not retrieved.

## Q7. SARIF 2.1.0 (OASIS): emitters, rule metadata and taxonomies, deterministic merge and deduplication

### Takeaway
SARIF 2.1.0 is the interchange format. Many tools emit it natively:
- confirmed in their own sources: GCC, Clang Static Analyzer, Cppcheck, Ruff, PMD, SpotBugs, Gitleaks, OSV-Scanner, Dependency-Check, Semgrep, Opengrep and Scorecard (gated)
- emits SARIF by design: CodeQL

Standard mappings live either in rule `properties.tags` (the CodeQL and GitHub convention) or in SARIF's first-class `taxonomies`/`taxa` and `reportingDescriptor.relationships`. Cross-run deduplication rests on `partialFingerprints`. GitHub uses only `primaryLocationLineHash`, a whitespace-insensitive rolling hash of line content with an occurrence counter, which is reproducible offline.

### Cited Findings
**Status and licence:**
- GitHub code scanning "supports a subset of the SARIF 2.1.0 JSON schema", referencing docs.oasis-open.org/sarif/sarif/v2.1.0. — [GitHub docs: SARIF support](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md)
- The OASIS TC repo is governed by the OASIS IPR policy; it is not an open-source-licensed repository. — [oasis-tcs/sarif-spec README](https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/README.md); [LICENSE.md](https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/LICENSE.md)
- SARIF 2.2: a starter document was submitted in October 2023, and 17 open "2.2" issues were noted. No Committee Specification Draft for 2.2 was found. — [OASIS SARIF list 2023-10 (search snippet)](https://lists.oasis-open.org/archives/sarif/202310/msg00005.html); [OASIS SARIF TC discussion (search snippet)](https://groups.oasis-open.org/discussion/sarif-no-meeting-today-sorry-next-meeting-suggested-is-december-4)

**Emitters, confirmed in each project's own source or docs:**
- GCC: `-fdiagnostics-format=sarif-file`, `sarif-stderr`, `-fdiagnostics-add-output=sarif`. — [gcc invoke.texi](https://raw.githubusercontent.com/gcc-mirror/gcc/master/gcc/doc/invoke.texi)
- Clang Static Analyzer: `sarif` and `sarif-html` output. — [Analyses.def](https://raw.githubusercontent.com/llvm/llvm-project/main/clang/include/clang/StaticAnalyzer/Core/Analyses.def)
- Cppcheck: `--output-format=sarif`. — [cmdlineparser.cpp](https://raw.githubusercontent.com/danmar/cppcheck/main/cli/cmdlineparser.cpp)
- Ruff: `sarif` output format. — [ruff settings/types.rs](https://raw.githubusercontent.com/astral-sh/ruff/main/crates/ruff_linter/src/settings/types.rs)
- PMD: `sarif` renderer. — [PMD report formats](https://raw.githubusercontent.com/pmd/pmd/main/docs/pages/pmd/userdocs/pmd_report_formats.md)
- SpotBugs: `-sarif=path` (since 4.5.0). — [SpotBugs running.rst](https://raw.githubusercontent.com/spotbugs/spotbugs/master/docs/running.rst)
- Gitleaks: `--report-format sarif`. — [gitleaks README](https://raw.githubusercontent.com/gitleaks/gitleaks/master/README.md)
- OSV-Scanner: `--format sarif`; "Each vulnerability (grouped by aliases) is a separate rule". — [osv-scanner output.md](https://raw.githubusercontent.com/google/osv-scanner/main/docs/output.md)
- Dependency-Check: `-f SARIF`. — [arguments.md](https://raw.githubusercontent.com/jeremylong/DependencyCheck/main/cli/src/site/markdown/arguments.md)
- Semgrep and Opengrep: `--sarif`. — [semgrep CHANGELOG](https://raw.githubusercontent.com/semgrep/semgrep/develop/CHANGELOG.md); [opengrep README](https://raw.githubusercontent.com/opengrep/opengrep/main/README.md)
- Scorecard: the SARIF format is added only when `isSarifEnabled()`. — [scorecard options/flags.go](https://raw.githubusercontent.com/ossf/scorecard/main/options/flags.go)
- No SARIF reference was found in the Bandit README, the ShellCheck man page, ESLint's built-in formatter docs, the golangci-lint `output.go`, or ast-grep's printers. These may still support SARIF through plugins or other files; not verified.

**Rule metadata representation:**
- GitHub reads `reportingDescriptor` fields:
  - `shortDescription.text` and `fullDescription.text` (required; up to 1024 chars)
  - `name` (up to 255)
  - `properties.tags[]` (e.g. `security`; up to 20 tags, 10 displayed)
  - `properties.security-severity` ("greater than 0.0 up to 10.0"; mapped as >9.0 critical, 7.0–8.9 high, 4.0–6.9 medium, 0.1–3.9 low)
  - `properties.precision`
  - Source: [GitHub docs: SARIF support](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md)
- CodeQL encodes CWE as tags `external/cwe/cwe-NNN`. — [CodeQL style guide](https://raw.githubusercontent.com/github/codeql/main/docs/query-metadata-style-guide.md)
- Standard taxonomies (CWE) are represented as:
  - `run.taxonomies[]`: toolComponent objects with a `guid` and a `taxa[]` subset; the log may include only the relevant taxa.
  - `tool.driver.supportedTaxonomies[]`: references by guid.
  - `reportingDescriptor.relationships[]`: pointing a rule at a taxon, e.g. CWE-401.
  - `result.taxa[]`: per-result categorization.
  - Source: [Microsoft SARIF tutorials, "Beyond basics: Taxonomies"](https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/docs/3-Beyond-basics.md)

**Fingerprints and deduplication:**
- "GitHub uses the `partialFingerprints` property in the OASIS standard to detect when two results are logically identical… Code scanning only uses the `primaryLocationLineHash`." The `ruleId` and file paths must be consistent across runs, or alerts duplicate. If fingerprints are missing, `upload-sarif` computes them, while the REST API upload does not, so duplicates may appear. — [GitHub docs: SARIF support](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md)
- Algorithm in codeql-action `fingerprints.ts`:
  - It computes "a rolling hash for every line… using the first BLOCK_SIZE [=100] non-space/tab characters counted from the start of the line".
  - Line endings are normalized to `\n`.
  - MOD = 37 over 64-bit Long arithmetic, output in hex.
  - A per-hash occurrence count gives values like `39fa2ee980eb94b0:1`.
  - Source: [codeql-action src/fingerprints.ts](https://raw.githubusercontent.com/github/codeql-action/main/src/fingerprints.ts); [GitHub docs example values](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md)
- `runAutomationDetails.id` identifies a run category. Run and result `guid` and `correlationGuid` exist for identity across runs. — [GitHub docs](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md); [SARIF tutorials](https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/docs/3-Beyond-basics.md)
- In the SARIF 2.0 CSD01 table of contents, `partialFingerprints` was §3.19.13, `baselineState` §3.19.20, and "Distinguishing logically identical from logically distinct results" §3.19.3. Section numbers differ in 2.1.0. — [SARIF v2.0 csprd01](https://docs.oasis-open.org/sarif/sarif/v2.0/csprd01/sarif-v2.0-csprd01.html)

**GitHub ingestion limits:**
- 20 runs per file.
- 25,000 results per run (top 5,000 kept).
- 25,000 rules per run.
- 10,000 thread-flow locations (top 1,000 kept).
- 1,000 locations per result (100 kept).
- 20 tags per rule (10 kept).
- 10 MB gzip per upload.
- Source: [sarif-limits reusable](https://raw.githubusercontent.com/github/docs/main/data/reusables/code-scanning/sarif-limits.md); [GitHub docs](https://raw.githubusercontent.com/github/docs/main/content/code-security/reference/code-scanning/sarif-files/sarif-support.md)

### Inferences
**Deterministic merge recipe (design inference from the sources above):**
1. Ingest each tool's SARIF.
2. Resolve every artifact URI to repo-relative form via `originalUriBaseIds` / `uriBaseId`.
3. Normalize `ruleId` as `<tool>/<ruleId>`.
4. Extract CWE IDs from `relationships` / `taxa`, `properties.tags` (`external/cwe/cwe-N`, `CWE-N:` strings) and `properties.cwe`. Validate them against the pinned CWE 5.0. Drop or flag Prohibited entries and walk Discouraged ones down via view 1000.
5. Compute your own `primaryLocationLineHash` with the published algorithm so fingerprints do not depend on each tool.
6. Define a cross-tool duplicate key such as (CWE set ∩ allowed, artifact, line hash, start line ± window).
7. Keep all contributing (tool, ruleId) pairs as provenance.
8. Emit one SARIF run with a `taxonomies` block holding CWE 5.0 plus a custom taxonomy for ASVS, OWASP and CISQ tags. Sort results by (artifact, startLine, startColumn, ruleId) and use stable GUIDs derived by hashing (UUIDv5) so output is byte-reproducible.

Also:
- Severity normalization: use SARIF `level` (error/warning/note), GitHub-style `security-severity` buckets, and CodeQL-style `precision` as the cross-tool common denominator.
- Respect GitHub's limits when exporting to GitHub. Internally, no limit is needed.

### Gaps
- The OASIS spec HTML was blocked, so the normative text of `fingerprints` vs `partialFingerprints`, the `baselineState` values (new/unchanged/updated/absent) and the result-matching appendix were not quoted from the 2.1.0 spec. They should be checked against docs.oasis-open.org/sarif/sarif/v2.1.0.
- Whether GCC's and Clang's SARIF include CWE taxa was not checked.
- SARIF 2.2 status in late 2026 is unknown.

## Q8. OpenSSF: Scorecard, Concise Guide, Compiler Options Hardening Guide, Secure Coding Guide for Python, OSV (format and data licences)

### Takeaway
OpenSSF guides are CC-BY-4.0 and their code is Apache-2.0, so they are fully bundleable with attribution. CWE 5.0 even ships a view mapping the OpenSSF Python guide. The Compiler Options Hardening Guide (2026-08-20 edition) gives a ready-made, citable flag baseline. The OSV schema is Apache-2.0 at version 1.9.2 (2026-10-13). OSV data, however, is licensed per source. Most sources are CC-BY 4.0, CC0, MIT or Apache, but some are CC-BY-SA (Ubuntu, Alpine) and Chainguard/Wolfi is CC-BY-NC-ND. A blanket bundle of `all.zip` therefore needs per-ecosystem filtering.

### Cited Findings
**Working-group licensing and guides:**
- "Unless otherwise specifically noted, software released by this working group is released under the Apache 2.0 license, and documentation is released under the CC-BY-4.0 license. Formal specifications would be licensed under the Community Specification License." — [ossf/wg-best-practices-os-developers README](https://raw.githubusercontent.com/ossf/wg-best-practices-os-developers/main/README.md)
- Published guides include:
  - Concise Guide for Developing More Secure Software
  - Concise Guide for Evaluating Open Source Software
  - Compiler Options Hardening Guide for C and C++
  - Compiler Annotations for C and C++ (incubating)
  - Secure Coding Guide for Python
  - Security Focused Guide for AI Code Assistant Instructions
  - CRA Brief Guide for OSS Developers
  - npm Best Practices Guide
  - All rendered at best.openssf.org. — [wg README](https://raw.githubusercontent.com/ossf/wg-best-practices-os-developers/main/README.md)
- The Compiler Options Hardening Guide is dated "2026-08-20". Its TL;DR baseline includes `-O2 -Wall -Wformat -Wformat=2 -Wconversion -Wimplicit-fallthrough -Werror=format-security -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -D_GLIBCXX_ASSERTIONS -D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST -fstrict-flex-arrays=3 -fstack-clash-protection -fstack-protector-strong -Wl,-z,nodlopen -Wl,-z,noexecstack …`. It advises against blanket `-Werror` when distributing source. — [Compiler-Options-Hardening-Guide-for-C-and-C++.md](https://raw.githubusercontent.com/ossf/wg-best-practices-os-developers/main/docs/Compiler-Hardening-Guides/Compiler-Options-Hardening-Guide-for-C-and-C++.md)
- CWE 5.0 view 1449 is "Weaknesses Addressed by the OpenSSF Secure Coding Guide for Python (2026)" (48 entries). Weaknesses carry `pyscg-NNNN` IDs, e.g. CWE-476 → pyscg-0034 "Check for None Values"; CWE-190 → pyscg-0002; CWE-134 → pyscg-0008. — [CWE 5.0 XML](https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip)

**Scorecard:**
- Licence Apache-2.0. "Data provided by the REST API is licensed under the CDLA Permissive 2.0." SARIF output is feature-gated (`isSarifEnabled()`). — [scorecard LICENSE](https://raw.githubusercontent.com/ossf/scorecard/main/LICENSE); [scorecard README](https://raw.githubusercontent.com/ossf/scorecard/main/README.md); [flags.go](https://raw.githubusercontent.com/ossf/scorecard/main/options/flags.go)

**OSV:**
- The schema repo is Apache-2.0. The schema document reads "Version 1.9.2 (October 13, 2026)". Records carry `schema_version`, plus `database_specific` and `ecosystem_specific` extension blocks. — [osv-schema LICENSE](https://raw.githubusercontent.com/ossf/osv-schema/main/LICENSE); [osv-schema docs/schema.md](https://raw.githubusercontent.com/ossf/osv-schema/main/docs/schema.md)
- The OSV.dev code is Apache-2.0. — [google/osv.dev LICENSE](https://raw.githubusercontent.com/google/osv.dev/master/LICENSE)
- Data licences per source:

  | Licence | Sources |
  |---|---|
  | CC-BY 4.0 | GitHub Advisory DB, PyPI, Go vulndb, OSS-Fuzz, PSF, Erlang EEF, Android, Julia, Red Hat |
  | CC0 1.0 | RustSec, Haskell, opam, GSD |
  | MIT | Drupal, AlmaLinux, Azure Linux, BellSoft, curl |
  | Apache 2.0 | R Consortium, OpenSSF Malicious Packages, Bitnami, CleanStart, Chromium |
  | BSD | Rocky Linux |
  | CC-BY-SA 4.0 | Ubuntu, Alpine (converted) |
  | **CC-BY-NC-ND 4.0** | Chainguard/Wolfi |

  Debian and NVD-derived records are produced by OSV's converters; their data licences were not listed alongside the converters. — [osv.dev docs/data.md](https://raw.githubusercontent.com/google/osv.dev/master/docs/data.md)
- Offline bulk access: `gs://osv-vulnerabilities/all.zip` (all ecosystems, including withdrawn records), `<ECOSYSTEM>/all.zip`, `modified_id.csv` for incremental sync, and `ecosystems.txt`. Prefixed ecosystems such as `Alpine:*` are no longer exported separately. — [osv.dev docs/data.md](https://raw.githubusercontent.com/google/osv.dev/master/docs/data.md)

### Inferences
- An offline assistant can bundle OSV data for ecosystems whose sources are CC0, MIT, Apache or CC-BY. Keep per-record source attribution, for example from the record ID prefix and `database_specific`. Exclude Chainguard/Wolfi, since its NC-ND terms bar commercial redistribution and modification. Isolate CC-BY-SA sources (Ubuntu, Alpine) as share-alike data files.
- OSV record aliases (CVE, GHSA) plus `database_specific` CWE fields, where sources provide them (e.g. GHSA `cwe_ids`; not verified here), can link dependency findings into the same CWE-normalized view as SAST findings.
- The OpenSSF Python guide → CWE view 1449, combined with the `pyscg` IDs, gives a free, bundleable Python secure-coding rule taxonomy analogous to CERT for C and Java.

### Gaps
- The latest Scorecard release version and the exact environment variable that enables SARIF (`ENABLE_SARIF` is likely) were not verified.
- The Concise Guide's last-updated date was not retrieved.
- Licence terms for Debian- and NVD-derived OSV records were not stated in the fetched doc.
- Whether OSV records carry CWE IDs in a standard field, as opposed to per-database `database_specific`, was not checked in the 1.9.2 schema.
