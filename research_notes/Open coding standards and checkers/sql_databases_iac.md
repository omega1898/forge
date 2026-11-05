# SQL, database code, and infrastructure-as-code: open standards and static checkers (status as of Nov 2026)

Method note: this session's network policy blocked direct fetches of most non-GitHub sites (docs.sqlfluff.com, squawkhq.com, atlasgo.io, wiki.postgresql.org, trivy.dev, docs.kics.io, semgrep.dev, *.github.io). The GitHub API was also blocked. Facts below therefore come from (a) LICENSE, README and source files fetched from raw.githubusercontent.com on 2026-11-05, which is the strongest evidence here, and (b) web-search result summaries, which are weaker and labeled as such. Rule lists for SQLFluff and Squawk were taken from the `main`/`master` source trees on that date, not from a tagged release.

## 1. SQL style guides and anti-pattern standards: publisher, license, redistributability

### Takeaway
Only two of the listed SQL standards are clearly openly licensed. Simon Holywell's sqlstyle.guide is CC BY-SA 4.0, so it can be redistributed with attribution and share-alike. The Trivadis PL/SQL & SQL Coding Guidelines (rule IDs G-xxxx, final version v4.4) are reported to be Apache 2.0, but I could not confirm that from the LICENSE file. The Trivadis checker, db* CODECOP, is archived and licensed CC BY-NC-ND or commercially. "SQL Antipatterns" is a copyrighted book. Its concepts survive in open tools, most directly in sqlcheck, whose README cites the book. I found no official Microsoft T-SQL style guide; the closest official artifact is the SR00xx code-analysis rule set. I could not verify the license of the PostgreSQL wiki "Don't Do This" page.

### Cited Findings
**sqlstyle.guide (Simon Holywell)**
- LICENCE file text: "SQL style guide (c) by Simon Holywell <https://www.simonholywell.com/> SQL style guide is licensed under a Creative Commons Attribution-ShareAlike 4.0 International License." — [treffynnon/sqlstyle.guide LICENCE](https://raw.githubusercontent.com/treffynnon/sqlstyle.guide/master/LICENCE)

**SQL Antipatterns (Bill Karwin)**
- The sqlcheck README lists "(1) SQL Anti-patterns: Avoiding the Pitfalls of Database Programming, Bill Karwin" as a reference, so the book is the conceptual source of sqlcheck's checks. — [jarulraj/sqlcheck README](https://raw.githubusercontent.com/jarulraj/sqlcheck/master/README.md)
- sqlcheck encodes these anti-patterns with numeric IDs in four groups. Logical design: 1001 Multi-Valued Attribute, 1002 Recursive Dependency, 1003 Primary Key Does Not Exist, 1004 Generic Primary Key, 1005 Foreign Key Does Not Exist, 1006 Entity-Attribute-Value, 1007 Metadata Tribbles. Physical design: 2001 Imprecise Data Type, 2002 Values In Definition, 2003 Files Are Not SQL Data Types, 2004 Too Many Indexes, 2005 Index Attribute Order. Query: 3001 SELECT *, 3002 NULL Usage, 3003 NOT NULL Usage, 3004 String Concatenation, 3005 GROUP BY Usage, 3006 ORDER BY RAND, 3007 Pattern Matching, 3008 Spaghetti Query, 3009 Reduce Number of JOINs, 3010 Unnecessary DISTINCT, 3011 Implicit Column Usage, 3012 HAVING, 3013 Nested sub queries, 3014 OR Usage, 3015 UNION Usage, 3016 DISTINCT & JOIN. Application: 4001 Readable Passwords. — [sqlcheck README](https://raw.githubusercontent.com/jarulraj/sqlcheck/master/README.md)

**Trivadis PL/SQL & SQL Coding Guidelines**
- Rule IDs use the form G-NNNN, for example "G-1010: Try to label your sub blocks", "G-2110: Try to use anchored declarations…" and "G-3110: Always specify the target columns when coding an insert statement". The latest version is v4.4. — search summary of [Trivadis guidelines v4.4 PDF](https://trivadis.github.io/plsql-and-sql-coding-guidelines/v4.4/9-appendix/PLSQL-and-SQL-Coding-Guidelines.pdf), [v3.6 PDF](https://trivadis.github.io/plsql-and-sql-coding-guidelines/v3.6/9-appendix/PLSQL-and-SQL-Coding-Guidelines.pdf), and [releases](https://github.com/trivadis/plsql-and-sql-coding-guidelines/releases)
- Naming-convention guidelines G-9101 to G-9111 are implemented as a custom validator (TrivadisPlsqlNaming, 15 guidelines), for example "G-9101 Always name global variables to match '^g_.+$'" and "G-9108 Always name in parameters to match '^in_.+$'". — [Trivadis/plsql-cop-validators README](https://raw.githubusercontent.com/Trivadis/plsql-cop-validators/main/README.md)
- License of the guidelines: one search summary says they are "licensed under the Apache License, Version 2.0, per the project's documentation site". A second search did not confirm this. — [search result, trivadis.github.io](https://trivadis.github.io/plsql-and-sql-coding-guidelines/main). **Not verified from a LICENSE file.** Raw GitHub paths for the guidelines repo (README, LICENSE, docs/) all returned 404 on 2026-11-05.
- A vendor blog (Bytebase) says the standalone guidelines were archived in July 2026 with v4.4 as the final version, and that the rules continue in dbLinter. — [Bytebase, Top Oracle SQL Review Tools in 2026](https://www.bytebase.com/blog/top-oracle-sql-review-tools.md). This is a secondary source; I found no primary announcement.

**db* CODECOP (Trivadis, now Accenture contact)**
- CLI: "This project is no longer maintained! Therefore, it is in archive state!" It checks SQL*Plus files against Guidelines v4.4 and computes McCabe, Halstead and maintainability-index metrics. Output is XML, HTML, Excel, and JSON in SonarQube Generic Issue Import format. License: "The preview/trial version … is licensed under the Creative Commons Attribution-NonCommercial-NoDerivs 3.0 … limited in time and volume. For production use a separate software license agreement is required. Please contact dbstar-info@accenture.com". — [Trivadis/plsql-cop-cli README](https://raw.githubusercontent.com/Trivadis/plsql-cop-cli/main/README.md); [LICENSE.txt](https://raw.githubusercontent.com/Trivadis/plsql-cop-cli/main/LICENSE.txt)
- The SQL Developer extension is "a free extension" and is archived. — [Trivadis/plsql-cop-sqldev README](https://raw.githubusercontent.com/Trivadis/plsql-cop-sqldev/main/README.md). The validators repo is CC BY-NC-ND 3.0 and archived. Its validators include SQLInjection ("unasserted parameters in dynamic SQL") and Hint. — [plsql-cop-validators LICENSE.txt](https://raw.githubusercontent.com/Trivadis/plsql-cop-validators/main/LICENSE.txt)
- dbLinter is "a joint project between United Codes and Grisselbav". It is sold through commercial subscription plans and ships as a VS Code extension, a CLI and a SonarQube plugin. Configuration is "centrally managed in the dbLinter Web GUI". — [Grisselbav/dbLinter README](https://raw.githubusercontent.com/Grisselbav/dblinter/main/README.md)
- Oracle SQLcl's `CODESCAN` command "identifies issues with the code using the Trivadis Coding Guidelines". It takes `-path` and `-ignore` options and is enabled with `set codescan on` (default off). Example output: "SQL best practice warning (1,7): G-1010". — search summary of [Oracle SQLcl 26.1 CODESCAN docs](https://docs.oracle.com/en/database/oracle/sql-developer-command-line/26.1/sqcug/codescan.html) and [25.3](https://docs.oracle.com/en/database/oracle/sql-developer-command-line/25.3/sqcug/codescan.html)

**Oracle built-in**
- The `PLSQL_WARNINGS` parameter enables, disables or promotes compiler warnings by category (`SEVERE`, `PERFORMANCE`, `INFORMATIONAL`, `ALL`) or by number. The default is `'DISABLE:ALL'`. — search summary of [Oracle REFRN PLSQL_WARNINGS](https://docs.oracle.com/database/122/REFRN/PLSQL_WARNINGS.htm). A third-party page puts SEVERE at PLW-05000–05999 and INFORMATIONAL at PLW-06000–06249. — [psoug.org](https://psoug.org/reference/plsql_warnings.html). I did not verify the PERFORMANCE range.

**SQL Server / T-SQL**
- Microsoft's SQL database projects ship code analysis rules in design, naming and performance categories. Confirmed examples: SR0001 (avoid SELECT * in procs/views/TVFs), SR0008 (prefer SCOPE_IDENTITY over @@IDENTITY), SR0011 (special characters in object names), SR0012 (reserved words as type names), SR0016 (sp_ prefix). The rule set is extensible via package references with Microsoft.Build.Sql. — search summary of [Microsoft Learn: SQL code analysis](https://learn.microsoft.com/en-us/sql/tools/sql-database-projects/concepts/sql-code-analysis/sql-code-analysis) and [extensibility](https://learn.microsoft.com/sql/ssdt/overview-of-extensibility-for-database-code-analysis-rules). The rule count ("14 rules") comes from a third-party summary and is unverified.
- The DacFx repo (Microsoft.Build.Sql SDK, ScriptDom links) is MIT licensed. — [microsoft/DacFx LICENSE.txt](https://raw.githubusercontent.com/microsoft/DacFx/main/LICENSE.txt); [README](https://raw.githubusercontent.com/microsoft/DacFx/main/README.md)

**PostgreSQL wiki "Don't Do This"**
- The page was started in 2018 and had 18 rules by 2020. Examples: Don't use SQL_ASCII, NOT IN, timestamp (without time zone), money, serial. Each entry has "Why not?" and "When should you?". — [depesz 2020](https://depesz.com/2020/01/28/dont-do-these-things-in-postgresql/); [Postgres.FM episode](https://postgres.fm/episodes/dont-do-this); [FOSDEM 2023 talk](https://archive.fosdem.org/2023/schedule/event/postgresql_dont_do_this/)
- I could not fetch the page or its license because wiki.postgresql.org was network-blocked.

### Inferences
- For a redistributable embedded rule corpus, sqlstyle.guide (CC BY-SA 4.0) is safe to ship with attribution. Shipping it may impose share-alike on derived rule text. The Trivadis guideline text is safe only if Apache 2.0 is confirmed, so treat it as "verify before bundling".
- Several "Don't Do This" items map directly onto Squawk rule names: `prefer-timestamp-tz` (timestamp without tz), `prefer-identity` (serial), `prefer-text-field` (varchar(n)), and `ban-char-field` (char(n)). This mapping is by rule name and was not checked against Squawk's docs.
- sqlcheck names are close paraphrases of Karwin's chapters, for example "Multi-Valued Attribute" ≈ Jaywalking, "Recursive Dependency" ≈ Naive Trees, and "Generic Primary Key" ≈ ID Required. This is my name-based mapping, not a mapping published by sqlcheck.
- The Trivadis-rules checker market moved from free/trial (db* CODECOP) to commercial (dbLinter). The only free current checker for G-xxxx rules found is Oracle SQLcl CODESCAN. SQLcl is free to download, but I did not verify whether its license permits redistribution.

### Gaps
- Trivadis guideline license: not verified from a LICENSE file. The repo returns 404 for all tried raw paths, so the repo state (archived, moved or private) is unknown.
- PostgreSQL wiki content license and the current full item list: page blocked.
- I found no official Microsoft T-SQL *style* guide (naming or formatting conventions). The full SR0001–SR0016 list was not obtained.
- "SQL Antipatterns" is a commercial book. Its publisher and edition details are not sourced in this session.
- IBM, Oracle and Microsoft documentation is publicly readable, but I did not verify any redistribution terms. Assume it is not redistributable.

## 2. SQL and database static checkers: license, dialects, rule IDs, detection type, SARIF, offline, determinism

### Takeaway
SQLFluff (MIT) is the broad, deterministic, offline baseline. It covers about 30 dialects including Db2, Oracle, T-SQL, Teradata and Snowflake, has 76 rule codes across 9 bundles, and its `main` source has a `sarif` output format. Squawk (MIT/Apache-2.0) is the strongest open PostgreSQL migration-safety linter, with about 75 rules, but it has no SARIF output. TSQLLint (MIT, 30 rules) covers T-SQL. ZPA (LGPL-3.0) covers PL/SQL via SonarQube or CLI. plpgsql_check (MIT-style) is the deep PL/pgSQL checker but needs a live PostgreSQL. Atlas's linter source is Apache 2.0, but `migrate lint` in the default binaries has been paywalled since v0.38. Liquibase moved to FSL-1.1, and its policy checks are paid. Flyway's OSS core is Apache 2.0, but its code analysis is paid.

### Cited Findings
**SQLFluff**
- License MIT ("Copyright (c) 2018-2026 Alan Cruickshank"). `main` pyproject shows version 4.5.0 and requires Python ≥3.10. — [LICENSE.md](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/LICENSE.md); [pyproject.toml](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/pyproject.toml)
- Dialects: ANSI, Athena, BigQuery, ClickHouse, Databricks, Db2, Doris, DuckDB, Exasol, FlinkSQL, Greenplum, Hive, Impala, MariaDB, Materialize, MySQL, Oracle, PostgreSQL, Redshift, Snowflake, SOQL, SparkSQL, SQLite, StarRocks, Teradata, Transact-SQL, Trino, Vertica ("though perhaps not in full"). Templating: Jinja, placeholders, Python format strings, and dbt via plugin. — [README](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/README.md)
- Output formats (`FormatType` enum on main): human, json, yaml, **sarif**, github-annotation, github-annotation-native, gitlab, none. — [src/sqlfluff/core/types.py](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/src/sqlfluff/core/types.py)
- Rule codes found in the `main` source tree. Each probed file under `src/sqlfluff/rules/<bundle>/<CODE>.py` gave the name, legacy alias, and whether `is_fix_compatible`. Source: [rules tree, e.g. LT01.py](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/src/sqlfluff/rules/layout/LT01.py)
  - Aliasing: AL01 aliasing.table (L011, fix), AL02 aliasing.column (L012), AL03 aliasing.expression (L013), AL04 aliasing.unique.table (L020), AL05 aliasing.unused (L025, fix), AL06 aliasing.length (L066), AL07 aliasing.forbid (L031, fix), AL08 aliasing.unique.column, AL09 aliasing.self_alias.column (fix), AL10 aliasing.required.
  - Ambiguous: AM01 distinct (L021), AM02 union (L033, fix), AM03 order_by (L037, fix), AM04 column_count (L044), AM05 join (L051, fix), AM06 column_references (L054), AM07 set_columns (L068), AM08 join_condition (fix), AM09 order_by_limit.
  - Capitalisation: CP01 keywords (L010), CP02 identifiers (L014), CP03 functions (L030), CP04 literals (L040), CP05 types (L063). All are fixable.
  - Convention: CV01 not_equal (L061), CV02 coalesce (L060), CV03 select_trailing_comma (L038), CV04 count_rows (L047), CV05 is_null (L049), CV06 terminator (L052), CV07 statement_brackets (L053), CV08 left_join (L055), CV09 blocked_words (L062), CV10 quoted_literals (L064), CV11 casting_style (L067), CV12 join_condition, CV13 last_select_star.
  - Jinja: JJ01 padding (L046).
  - Layout: LT01 spacing, LT02 indent, LT03 operators, LT04 commas, LT05 long_lines, LT06 functions, LT07 cte_bracket, LT08 cte_newline, LT09 select_targets, LT10 select_modifiers, LT11 set_operators, LT12 end_of_file, LT13 start_of_file, LT14 keyword_newline, LT15 newlines. All are fixable.
  - References: RF01 from (L026), RF02 qualification (L027), RF03 consistent (L028, fix), RF04 keywords (L029), RF05 special_chars (L057), RF06 quoting (L059, fix), RF07 window_alias.
  - Structure: ST01 else_null, ST02 simple_case, ST03 unused_cte, ST04 nested_case, ST05 subquery, ST06 column_order, ST07 using, ST08 distinct, ST09 join_condition_order, ST10 constant_expression, ST11 unused_join, ST12 consecutive_semicolons.
  - T-SQL: TQ01 sp_prefix (L056), TQ02 procedure_begin_end, TQ03 empty_batch, TQ04 prefer_as_alias.
  - Probes for AL11, AM10, CP06, CV14, LT16, RF08 and ST13 returned 404.

**sqlcheck**
- License Apache-2.0. — [LICENSE](https://raw.githubusercontent.com/jarulraj/sqlcheck/master/LICENSE)
- "targets all major SQL dialects". It is a C++/cmake build with prebuilt v1.3 .deb, .rpm, .dmg, .exe and .zip releases. CLI options: `-f`, `-r` risk level 1–3, `-c` color, `-v` verbose. No JSON or SARIF option is listed. Rule IDs 1001–4001 are listed in section 1. — [README](https://raw.githubusercontent.com/jarulraj/sqlcheck/master/README.md)

**Squawk (PostgreSQL migrations)**
- Dual license: MIT and Apache-2.0. — [LICENSE-MIT](https://raw.githubusercontent.com/sbdchd/squawk/master/LICENSE-MIT); [LICENSE-APACHE](https://raw.githubusercontent.com/sbdchd/squawk/master/LICENSE-APACHE)
- Install via npm (`squawk-cli`), pip (`squawk-cli`), release binaries, Docker (`ghcr.io/sbdchd/squawk`) or VS Code. It also runs a language server. Flags include `--pg-version`, `--exclude`, and `--include` for opt-in rules such as `require-table-schema`. Reporters: tty, gcc, json, gitlab. **No SARIF.** — [README](https://raw.githubusercontent.com/sbdchd/squawk/master/README.md)
- Rule names found as string literals in the linter crate: adding-field-with-default, adding-foreign-key-constraint, adding-not-nullable-field, adding-required-field, adding-serial-primary-key-field, ban-add-column, ban-add-composite-attribute, ban-add-enum-value, ban-alter-database-options, ban-alter-domain-with-add-constraint, ban-alter-extension, ban-alter-function-options, ban-alter-generated-expression, ban-alter-identity, ban-alter-policy-condition, ban-alter-policy-roles, ban-alter-role-options, ban-alter-row-level-security, ban-alter-sequence-values, ban-alter-system-options, ban-alter-view-options, ban-char-field, ban-concurrent-index-creation-in-transaction, ban-create-domain-with-constraint, ban-create-policy, ban-detach-inheritance, ban-disable-trigger, ban-drop-column, ban-drop-constraint, ban-drop-database, ban-drop-default, ban-drop-domain, ban-drop-extension, ban-drop-function, ban-drop-generated-expression, ban-drop-index, ban-drop-not-null, ban-drop-policy, ban-drop-schema, ban-drop-sequence, ban-drop-table, ban-drop-trigger, ban-drop-type, ban-drop-view, ban-duplicate-column-assignments, ban-new-write-restriction, ban-replace-view-function, ban-replica-identity, ban-revoke, ban-set-default, ban-set-schema, ban-truncate-cascade, ban-uncommitted-transaction, changing-column-type, constraint-missing-not-valid, disallowed-unique-constraint, identifier-too-long, prefer-bigint-over-int, prefer-bigint-over-smallint, prefer-identity, prefer-repack, prefer-robust-stmts, prefer-text-field, prefer-timestamp-tz (alias prefer-timestamptz), renaming-column, renaming-object, renaming-table, require-concurrent-index-creation, require-concurrent-index-deletion, require-concurrent-partition-detach, require-concurrent-reindex, require-enum-value-ordering, require-lock-timeout, require-statement-timeout, require-table-schema, require-timeout-settings (an alias that expands to other rules), transaction-nesting, unused-ignore. A string `invalid-rule-name` also appears in the crate. — [crates/squawk_linter/src/lib.rs](https://raw.githubusercontent.com/sbdchd/squawk/master/crates/squawk_linter/src/lib.rs)

**strong_migrations (Rails, for comparison)**
- License MIT, "2015-2026 Andrew Kane". — [LICENSE.txt](https://raw.githubusercontent.com/ankane/strong_migrations/master/LICENSE.txt)

**TSQLLint**
- MIT. Install via dotnet tool, Homebrew or NPM. Integrates with VS Code and SSMS. Custom rules are .NET plugin DLLs. — [LICENSE](https://raw.githubusercontent.com/tsqllint/tsqllint/main/LICENSE); [README](https://raw.githubusercontent.com/tsqllint/tsqllint/main/README.md)
- The 30 rules in the default config are: case-sensitive-variables, conditional-begin-end, count-star, cross-database-transaction, data-compression, data-type-length, delete-where, disallow-cursors, duplicate-empty-line, duplicate-go, full-text, information-schema, keyword-capitalization, linked-server, multi-table-alias, named-constraint, non-sargable, object-property, print-statement, schema-qualify, select-star, semicolon-termination, set-ansi, set-nocount, set-quoted-identifier, set-transaction-isolation-level, set-variable, update-where, upper-lower, unicode-string. Rules not present in `.tsqllintrc` are off. The README mentions no SARIF output. — [README](https://raw.githubusercontent.com/tsqllint/tsqllint/main/README.md)

**SQL Server code-analysis rule packs**
- SqlServer.Rules ("Yet Another Rule-set for SQL Server DataTools") is MIT (© 2018 Tim Cartwright). Its rules derive from `SqlCodeAnalysisRule`, the DacFx extensibility API. — [tcartwright/SqlServer.Rules LICENSE](https://raw.githubusercontent.com/tcartwright/SqlServer.Rules/master/LICENSE); [README](https://raw.githubusercontent.com/tcartwright/SqlServer.Rules/master/README.md)
- It is packaged for Microsoft.Build.Sql as ErikEJ.DacFX.SqlServer.Rules, and the TSQLSmellSCA rules were merged into it. "140+ rules" is a vendor-blog claim. — [NuGet ErikEJ.DacFX.SqlServer.Rules](https://www.nuget.org/packages/erikej.dacfx.sqlserver.rules); [NuGet TSQLSmellSCA](https://www.nuget.org/packages/ErikEJ.DacFX.TSQLSmellSCA/); [Bytebase blog](https://www.bytebase.com/blog/top-sql-server-sql-review-tools.md)

**ZPA (PL/SQL and Oracle SQL)**
- License LGPL-3.0. "ZPA is parser and static code analysis tool for PL/SQL and Oracle SQL". It ships as a SonarQube plugin (ZPA 4.2.1 supports SonarQube 26.2–26.10) and as `zpa-cli` for SonarCloud. — [felipebz/zpa LICENSE](https://raw.githubusercontent.com/felipebz/zpa/main/LICENSE); [README](https://raw.githubusercontent.com/felipebz/zpa/main/README.md)

**plpgsql_check (PostgreSQL extension)**
- License is MIT-style ("Permission is hereby granted, free of charge…", Pavel Stehule). — [LICENSE](https://raw.githubusercontent.com/okbob/plpgsql_check/master/LICENSE)
- Supports "PostgreSQL 14 - 20". It checks fields and types in embedded SQL, unused variables and arguments, dead code, missing RETURN, hidden casts (performance) and "EXECUTE statements against SQL injection vulnerability". It has `performance_warnings`, `security_warnings` and `extra_security_warnings_level` (0..4), PRAGMA markers, a profiler and a tracer. Output formats are text, json and xml. Caveat: "The SQL injection check can detect only some SQL injection vulnerabilities. This tool cannot be used for security audit!" — [README](https://raw.githubusercontent.com/okbob/plpgsql_check/master/README.md)

**Formatters and simple linters**
- sqlfmt: Apache-2.0 (© 2021 Ted Conbeer). "sqlfmt is not a linter." It lexes rather than parses, uses a "polyglot" dialect plus `clickhouse`, supports Jinja, and is installed as `shandy-sqlfmt`. — [LICENSE](https://raw.githubusercontent.com/tconbeer/sqlfmt/main/LICENSE); [README](https://raw.githubusercontent.com/tconbeer/sqlfmt/main/README.md)
- pgFormatter: "distributed under the PostgreSQL Licence". An embedded modified SQL::Beautify module is under Artistic License 2.0. It supports SQL-92 through SQL-2011 plus PostgreSQL keywords and has `--anonymize`. — [README](https://raw.githubusercontent.com/darold/pgFormatter/master/README); [LICENSE](https://raw.githubusercontent.com/darold/pgFormatter/master/LICENSE)
- sqlint: MIT (© 2015 Powershop NZ). A Ruby gem that checks syntax "against the ANSI syntax, and uses the PostgreSQL SQL parser"; it supports no MySQL or other variants. — [LICENSE.txt](https://raw.githubusercontent.com/purcell/sqlint/master/LICENSE.txt); [README](https://raw.githubusercontent.com/purcell/sqlint/master/README.md)

**Schema linters (need a live DB)**
- schemalint: MIT, works with Postgres, and connects to a database via a `.schemalintrc.js` connection config. Example rules: name-casing, name-inflection, prefer-jsonb-to-json, prefer-text-to-varchar. It emits suggested-fix DDL. — [LICENSE](https://raw.githubusercontent.com/kristiandupont/schemalint/main/LICENSE); [README](https://raw.githubusercontent.com/kristiandupont/schemalint/main/README.md)
- SchemaSpy: GPL-3.0. It can "locate and report anomalies such as missing indexes, implied relationships…". — [COPYING](https://raw.githubusercontent.com/schemaspy/schemaspy/master/COPYING); [README](https://raw.githubusercontent.com/schemaspy/schemaspy/master/README.md)
- SchemaCrawler: the LICENSE.md only points to "SchemaCrawler licensing options" on schemacrawler.com, which was not fetched. — [LICENSE.md](https://raw.githubusercontent.com/schemacrawler/SchemaCrawler/main/LICENSE.md)

**Atlas (ariga)**
- Repo license Apache-2.0. The README claims "50+ safety analyzers" for destructive changes, data-dependent modifications, table locks and backward incompatibility. Usage example: `atlas migrate lint --dev-url "docker://postgres/16/dev"`, which needs a dev database or Docker. — [LICENSE](https://raw.githubusercontent.com/ariga/atlas/master/LICENSE); [README](https://raw.githubusercontent.com/ariga/atlas/master/README.md)
- Per release notes quoted in search results, starting with v0.38 `migrate lint` is no longer in the free Starter plan. Default binaries are under the Atlas EULA, community binaries are Apache 2.0, and you can "build from source or use the community binary". A DEV article (Oct 2025) says the PostgreSQL rules PG301–PG311 are Pro-only. — [DEV article](https://dev.to/mickelsamuel/atlas-paywalled-their-migration-linter-here-are-your-free-alternatives-4god); [release notes mirror (unofficial)](https://github-users.yhajoa.workers.dev/ariga/atlas/releases). Both are secondary sources.

**Liquibase and Flyway**
- Liquibase Community `LICENSE.txt` is now "Functional Source License, Version 1.1, ALv2 Future License (FSL-1.1-ALv2) … Copyright © 2025 Liquibase Inc." Quarterly Community releases started with 5.0.2. A commercial `liquibase-secure` image exists. — [LICENSE.txt](https://raw.githubusercontent.com/liquibase/liquibase/master/LICENSE.txt); [README](https://raw.githubusercontent.com/liquibase/liquibase/master/README.md)
- Liquibase policy (quality) checks need a Pro or "Secure" license; the docs are inconsistent on which tier. Checks can be customized with Java regex, and custom checks can be written in Python. — [Liquibase policy checks](https://docs.liquibase.com/policy-checks); [checks run](https://docs.liquibase.com/commands/policy-checks/commands/run.html); [quality checks](https://docs.liquibase.com/commands/quality-checks/home.html)
- The Flyway OSS CLI is Apache-2.0. "Redgate editions … are not part of the Flyway open source project". Community DB support includes IBM DB2 z/OS. — [flyway README](https://raw.githubusercontent.com/flyway/flyway/main/README.md); [LICENSE.md](https://raw.githubusercontent.com/flyway/flyway/main/LICENSE.md)
- Flyway code analysis (`flyway check -code`) is "Enterprise only", has about 10 general rules, and can integrate SQLFluff. This comes from a competitor's blog only. — [Bytebase Flyway vs Liquibase](https://bytebase-website.pages.dev/blog/flyway-vs-liquibase.md)

### Inferences
- Deterministic offline file linters: SQLFluff (with the jinja/placeholder templaters; the dbt templater needs a dbt project and possibly a DB connection), sqlcheck, Squawk, TSQLLint, sqlfmt, pgFormatter and sqlint. All are pure functions of file plus config plus tool version, so pin the versions.
- DB-dependent checkers are deterministic only against a fixed schema snapshot: plpgsql_check, schemalint, SchemaSpy, SchemaCrawler and Atlas (`--dev-url`). An offline assistant would need an embedded or ephemeral PostgreSQL to run them.
- For migration safety, Squawk is the best fully open option for PostgreSQL. For MySQL, SQL Server and Oracle I found no equivalent fully open migration-safety linter in this session. Atlas covers several engines, but its lint is gated in the default binaries.
- SARIF among SQL tools: SQLFluff (`main` source) yes. Squawk, TSQLLint and sqlcheck: no SARIF found. plpgsql_check: no (text/json/xml). db* CODECOP: SonarQube generic JSON. Converting Squawk's JSON or gcc output to SARIF is a thin adapter.

### Gaps
- I did not confirm which SQLFluff tagged release first shipped `--format sarif`, or whether it is in the current PyPI release. I only saw it in the `main` source.
- I could not obtain the Atlas analyzer code list (DSxxx, BCxxx, MFxxx and so on) because atlasgo.io was blocked. I did not invent codes.
- Squawk rule list: taken from source string literals, so per-rule descriptions and the opt-in/default status were not verified (squawkhq.com was blocked).
- schemalint full rule list, SchemaCrawler license and lint rules, and SchemaCrawler/SchemaSpy output formats were not obtained.
- sqlcheck internals (whether regex-based or parser-based) and maintenance status were not verified. The README still references v1.3 binaries.

## 3. Mainframe databases (Db2 for z/OS, IMS, VSAM)

### Takeaway
IBM publishes Db2 for z/OS performance and EXPLAIN guidance openly (readable, under IBM copyright). I found no open-source static checker specific to Db2 for z/OS embedded SQL, IMS DL/I or VSAM. SQLFluff's `db2` dialect and generic SQL linters are the only open options, and their z/OS coverage is unverified.

### Cited Findings
- IBM Db2 12 for z/OS documents EXPLAIN tables for capturing access-path information. Rows are written at BIND with EXPLAIN(YES/ONLY), or for dynamic SQL via CURRENT EXPLAIN MODE. Db2 11-format EXPLAIN tables are deprecated. — search summary of [IBM Db2 12 EXPLAIN tables](https://www.ibm.com/docs/SSEPEK_12.0.0/usrtab/src/tpc/db2z_explaintables.html)
- Third-party guidance (Craig Mullins) covers Stage 1 vs Stage 2 predicates and static vs dynamic SQL costs. — [Mullins, DB2 Performance Roadmap](https://www.craigsmullins.com/index_htm_files/DB2%20Performance%20Roadmap%20[long].pdf)
- SQLFluff lists a "Db2" dialect linking to IBM Db2 analytics. — [SQLFluff README](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/README.md)
- Flyway community DB submissions include "IBM DB2 z/OS". — [flyway README](https://raw.githubusercontent.com/flyway/flyway/main/README.md)
- The search found no open-source static analyzer for COBOL embedded SQL, VSAM or IMS DL/I. — [search results, see above]

### Inferences
- A practical deterministic approach for Db2 z/OS: extract EXEC SQL blocks from COBOL/PL/I and lint them with SQLFluff `db2` plus custom rules, for example banning `SELECT *`, requiring `FOR FETCH ONLY` or `WITH UR` where policy dictates, flagging non-Stage-1 predicate patterns, and flagging missing `WHERE` on UPDATE/DELETE. EXPLAIN-based checks need a real Db2 subsystem and are not offline.
- For IMS and VSAM, any checks would have to be custom. Examples: DL/I call-pattern checks on CBLTDLI argument lists, and checking FILE STATUS after VSAM I/O in COBOL. No open checker exists to adopt.

### Gaps
- I could not confirm whether SQLFluff's `db2` dialect covers z/OS-specific syntax (for example `WITH UR`, `FOR FETCH ONLY`, `OPTIMIZE FOR n ROWS`).
- IBM's terms for reusing documentation text were not checked.
- IMS and VSAM checkers: none found, and the search was limited.

## 4. MS Access / VBA databases

### Takeaway
There is no open-source Access schema linter. The available open pieces are mdbtools for extracting schema DDL and data from .mdb/.accdb files on Linux (LGPL libraries, GPL utilities), msaccess-vcs-addin for exporting Access objects to text (BSD-style), and Rubberduck for VBA code inspections (GPL-3.0, a Windows/VBE add-in).

### Cited Findings
- mdbtools: "Files in libmdb, libmdbsql, and libmdbodbc are licensed under LGPL and the utilities and gui program are under the GPL". Utilities include `mdb-ver` (prints JET 3 or 4), `mdb-schema` (prints DDL), `mdb-tables` and others, plus a SQL engine (libmdbsql) and an ODBC driver. — [mdbtools README](https://raw.githubusercontent.com/mdbtools/mdbtools/master/README.md); [COPYING (GPL-2.0)](https://raw.githubusercontent.com/mdbtools/mdbtools/master/COPYING)
- msaccess-vcs-addin license: "Use of msaccess-vcs-integration and documentation are subject to the following BSD-style license: Permission to use, copy, modify, and/or distribute…". — [joyfullservice/msaccess-vcs-addin LICENSE.txt](https://raw.githubusercontent.com/joyfullservice/msaccess-vcs-addin/main/LICENSE.txt)
- Rubberduck (VBA IDE add-in) is GPL-3.0. — [Rubberduck LICENSE](https://raw.githubusercontent.com/rubberduck-vba/Rubberduck/next/LICENSE); [README](https://raw.githubusercontent.com/rubberduck-vba/Rubberduck/next/README.md)

### Inferences
- An offline pipeline is possible: `mdb-schema` → SQL DDL → SQLFluff (ANSI) or custom deterministic schema rules (missing primary keys, missing FK constraints, mixed naming, memo/text type choices) → sqlcheck logical-design checks (1003, 1005).
- Rubberduck inspections run inside the VBE on Windows and are not a headless CLI, so they cannot be invoked directly by an offline Linux assistant. The VBA code would need a separate parser or rules.

### Gaps
- Rubberduck's inspection list and any headless or CLI mode were not verified this session; the README did not mention inspections in the lines grepped.
- msaccess-vcs-addin functionality was not verified from its README; only the license was fetched.
- I found no open "Access schema lint" tool.

## 5. Security: SQL injection (CWE-89) detection in host languages

### Takeaway
All of the major open SAST tools have explicit SQL-injection rules except eslint-plugin-security, which has none. Use CodeQL queries `<lang>/sql-injection` (all tagged cwe-089), Bandit B608/B610/B611, gosec G201/G202/G701, SpotBugs SQL_* plus the FindSecBugs SQL_INJECTION_* family, and the Semgrep or Opengrep engines. CodeQL query source is MIT, but the CodeQL CLI binary has its own GitHub terms. The Semgrep-maintained rules are no longer under an OSI license.

### Cited Findings
- CodeQL query IDs (all `@tags … external/cwe/cwe-089`): `py/sql-injection`, `java/sql-injection`, `java/concatenated-sql-query` (precision medium), `js/sql-injection`, `go/sql-injection`, `cs/sql-injection`, `rb/sql-injection`, `cpp/sql-injection`, `rust/sql-injection`. All except java/concatenated-sql-query have precision high. — [python SqlInjection.ql](https://raw.githubusercontent.com/github/codeql/main/python/ql/src/Security/CWE-089/SqlInjection.ql); [java SqlTainted.ql](https://raw.githubusercontent.com/github/codeql/main/java/ql/src/Security/CWE/CWE-089/SqlTainted.ql); [java SqlConcatenated.ql](https://raw.githubusercontent.com/github/codeql/main/java/ql/src/Security/CWE/CWE-089/SqlConcatenated.ql); [js](https://raw.githubusercontent.com/github/codeql/main/javascript/ql/src/Security/CWE-089/SqlInjection.ql); [go](https://raw.githubusercontent.com/github/codeql/main/go/ql/src/Security/CWE-089/SqlInjection.ql); [csharp](https://raw.githubusercontent.com/github/codeql/main/csharp/ql/src/Security%20Features/CWE-089/SqlInjection.ql); [ruby](https://raw.githubusercontent.com/github/codeql/main/ruby/ql/src/queries/security/cwe-089/SqlInjection.ql); [cpp](https://raw.githubusercontent.com/github/codeql/main/cpp/ql/src/Security/CWE/CWE-089/SqlTainted.ql); [rust](https://raw.githubusercontent.com/github/codeql/main/rust/ql/src/queries/security/CWE-089/SqlInjection.ql)
- The CodeQL queries repo is MIT ("Copyright (c) 2006-2025 GitHub, Inc."). — [github/codeql LICENSE](https://raw.githubusercontent.com/github/codeql/main/LICENSE). The CodeQL CLI binaries are under "GitHub CodeQL Terms and Conditions", not an OSS license. — [github/codeql-cli-binaries LICENSE.md](https://raw.githubusercontent.com/github/codeql-cli-binaries/main/LICENSE.md)
- Bandit (Apache-2.0): B608 `hardcoded_sql_expressions` ("Test for SQL injection"), B610 `django_extra_used`, B611 `django_rawsql_used`. SARIF output comes via the optional extra `bandit[sarif]`, which uses sarif-om and registers formatter entry point `sarif = bandit.formatters.sarif:report`. — [injection_sql.py](https://raw.githubusercontent.com/PyCQA/bandit/main/bandit/plugins/injection_sql.py); [django_sql_injection.py](https://raw.githubusercontent.com/PyCQA/bandit/main/bandit/plugins/django_sql_injection.py); [setup.cfg](https://raw.githubusercontent.com/PyCQA/bandit/main/setup.cfg); [LICENSE](https://raw.githubusercontent.com/PyCQA/bandit/main/LICENSE)
- gosec (Apache-2.0): G201 "SQL query construction using format string (AST)", G202 "SQL query construction using string concatenation (AST)", G701 "SQL injection via taint analysis (Taint)". The G7xx taint family also covers command, path, SSRF, XSS, log, SMTP, SSTI and deserialization. SARIF via `-fmt sarif`. — [RULES.md](https://raw.githubusercontent.com/securego/gosec/master/RULES.md); [README](https://raw.githubusercontent.com/securego/gosec/master/README.md); [LICENSE.txt](https://raw.githubusercontent.com/securego/gosec/master/LICENSE.txt)
- SpotBugs (LGPL-2.1) core patterns: `SQL_NONCONSTANT_STRING_PASSED_TO_EXECUTE` and `SQL_PREPARED_STATEMENT_GENERATED_FROM_NONCONSTANT_STRING`, plus correctness patterns `SQL_BAD_PREPARED_STATEMENT_ACCESS` and `SQL_BAD_RESULTSET_ACCESS`. It emits SARIF; the CHANGELOG has multiple SARIF output fixes. The latest release is 4.10.5 (2026-10-11). — [spotbugs etc/findbugs.xml](https://raw.githubusercontent.com/spotbugs/spotbugs/master/spotbugs/etc/findbugs.xml); [CHANGELOG](https://raw.githubusercontent.com/spotbugs/spotbugs/master/CHANGELOG.md); [LICENSE](https://raw.githubusercontent.com/spotbugs/spotbugs/master/LICENSE)
- FindSecBugs (OWASP SpotBugs plugin, LGPL-3.0): `SQL_INJECTION`, `SQL_INJECTION_JDBC`, `SQL_INJECTION_JPA`, `SQL_INJECTION_HIBERNATE`, `SQL_INJECTION_SPRING_JDBC`, `SQL_INJECTION_JDO`, `SQL_INJECTION_ANDROID`, `SQL_INJECTION_TURBINE`, `SQL_INJECTION_VERTX`, `SCALA_SQL_INJECTION_ANORM`, `SCALA_SQL_INJECTION_SLICK`. — [findsecbugs findbugs.xml](https://raw.githubusercontent.com/find-sec-bugs/find-sec-bugs/master/findsecbugs-plugin/src/main/resources/metadata/findbugs.xml); [LICENSE](https://raw.githubusercontent.com/find-sec-bugs/find-sec-bugs/master/LICENSE); [README](https://raw.githubusercontent.com/find-sec-bugs/find-sec-bugs/master/README.md)
- eslint-plugin-security (Apache-2.0) rules: detect-bidi-characters, detect-buffer-noassert, detect-child-process, detect-disable-mustache-escape, detect-eval-with-expression, detect-invisible-characters, detect-new-buffer, detect-no-csrf-before-method-override, detect-non-literal-fs-filename, detect-non-literal-regexp, detect-non-literal-require, detect-object-injection, detect-possible-timing-attacks, detect-pseudoRandomBytes, detect-unsafe-regex. **The README does not mention "SQL" anywhere**, so there is no SQL-injection rule. — [README](https://raw.githubusercontent.com/eslint-community/eslint-plugin-security/main/README.md); [LICENSE](https://raw.githubusercontent.com/eslint-community/eslint-plugin-security/main/LICENSE)
- Semgrep CE engine: LGPL-2.1. — [semgrep LICENSE](https://raw.githubusercontent.com/semgrep/semgrep/develop/LICENSE)
- The semgrep-rules `develop` branch LICENSE reads "Semgrep Rules License v1.0". — [semgrep-rules develop LICENSE](https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/LICENSE). The `main` branch still carries the old "Commons Clause + LGPL 2.1" text. — [semgrep-rules main LICENSE](https://raw.githubusercontent.com/semgrep/semgrep-rules/main/LICENSE)
- Per Semgrep's licensing docs, the rules are available "only for internal business use", with competing products and SaaS excluded. — search summary of [Semgrep licensing](https://docs.semgrep.dev/licensing) and [FAQ](https://semgrep.dev/docs/faq/overview)
- Opengrep is an LGPL-2.1 fork of Semgrep 1.100.0. — [InfoQ](https://www.infoq.com/news/2025/02/semgrep-forked-opengrep). opengrep-rules carries the Commons Clause + LGPL text. — [opengrep-rules LICENSE](https://raw.githubusercontent.com/opengrep/opengrep-rules/main/LICENSE)
- plpgsql_check checks dynamic `EXECUTE` in PL/pgSQL for SQL injection, with the caveat "cannot be used for security audit". — [plpgsql_check README](https://raw.githubusercontent.com/okbob/plpgsql_check/master/README.md)
- The Trivadis "SQLInjection" validator for PL/SQL dynamic SQL is CC BY-NC-ND and archived. — [plsql-cop-validators README](https://raw.githubusercontent.com/Trivadis/plsql-cop-validators/main/README.md)

### Inferences
- For a redistributable offline assistant, the cleanest sources are CodeQL query *logic* (MIT, but running it needs the CodeQL CLI and its terms), Bandit, gosec, SpotBugs and FindSecBugs (all OSI-licensed and offline), and the Semgrep/Opengrep engines with self-authored rules. Bundling Semgrep-maintained rules in a product is restricted. The Commons Clause on opengrep-rules forbids "selling" the rules.
- JavaScript/TypeScript has no open SQLi rule in eslint-plugin-security. Use CodeQL `js/sql-injection` or custom Semgrep/Opengrep patterns.
- All of these map to CWE-89, so CWE-89 is the natural join key for findings.

### Gaps
- I did not enumerate the specific Semgrep rule IDs for SQLi; the registry was blocked.
- I did not check whether the Opengrep rules fork includes Semgrep's post-2024 rule changes.
- The current CodeQL CLI terms for commercial private-code use were not re-read; only the file header was seen.

## 6. Infrastructure-as-code and container checkers (license, IDs, SARIF)

### Takeaway
All of the requested IaC tools are open source and run offline once installed. Licenses: hadolint GPL-3.0, tflint MPL-2.0 plus some BUSL-1.1 files, checkov Apache-2.0, KICS Apache-2.0, Trivy Apache-2.0, yamllint GPL-3.0, actionlint MIT, kube-linter Apache-2.0. SARIF is native in hadolint, tflint, checkov, KICS, Trivy and kube-linter, and available via a Go template in actionlint. yamllint has no SARIF output.

### Cited Findings
- **hadolint**: GPL-3.0. A Haskell Dockerfile linter that runs ShellCheck on RUN instructions. `--format` accepts tty, json, checkstyle, codeclimate, gitlab_codeclimate, gnu, codacy, sonarqube, **sarif** and junit. The README table has 106 rule rows: DL1001, DL3000–DL3068 (with gaps such as DL3005, DL3017, DL3031 and DL3039 not in the table), DL4000–DL4006 (no DL4002), and embedded ShellCheck codes such as SC2086 and SC2046. — [README](https://raw.githubusercontent.com/hadolint/hadolint/master/README.md); [LICENSE](https://raw.githubusercontent.com/hadolint/hadolint/master/LICENSE)
- **tflint**: "although much of this project is licensed under MPL 2.0, some files in the `terraform` package are licensed under BUSL 1.1… the executable forms (release binaries) is bound by both licenses". Formats: default, json, checkstyle, junit, compact, **sarif**. The plugin rulesets (terraform bundled; aws, azurerm and google installed via `tflint --init`, which needs network) and the rule names were not enumerated. — [README](https://raw.githubusercontent.com/terraform-linters/tflint/master/README.md); [LICENSE](https://raw.githubusercontent.com/terraform-linters/tflint/master/LICENSE)
- **checkov**: Apache-2.0. "Over 1000 built-in policies" across Terraform/plan/JSON, CloudFormation, SAM, Kubernetes, Helm, Kustomize, Dockerfile, Serverless, Ansible, Bicep, ARM and OpenTofu. Check IDs look like `CKV_AWS_21`. Outputs: CLI, CycloneDX, JSON, JUnit XML, CSV, **SARIF**, GitHub markdown. Some severity-based filtering examples use `--bc-api-key`, a platform feature. — [README](https://raw.githubusercontent.com/bridgecrewio/checkov/main/README.md); [LICENSE](https://raw.githubusercontent.com/bridgecrewio/checkov/main/LICENSE)
- **KICS (Checkmarx)**: Apache-2.0. Its "queries" cover Terraform, Kubernetes, Docker, CloudFormation, Ansible, Helm, OpenAPI, gRPC, ARM, GDM, CDK, SAM, Docker Compose, Knative, Crossplane, Pulumi, Serverless, Azure Blueprints, CI/CD, Bicep and NIFCLOUD. `--report-formats "sarif"` is supported, sorted by severity, per SARIF v2.1.0. — [README](https://raw.githubusercontent.com/Checkmarx/kics/master/README.md); [docs/results.md](https://raw.githubusercontent.com/Checkmarx/kics/master/docs/results.md); [LICENSE](https://raw.githubusercontent.com/Checkmarx/kics/master/LICENSE)
- **Trivy (Aqua)**: Apache-2.0. Formats: Table, JSON, **SARIF** ("complying with SARIF 2.1.0 OASIS standard … `--format sarif`"), Template, SBOM, GitHub dependency snapshot. — [docs/guide/configuration/reporting.md](https://raw.githubusercontent.com/aquasecurity/trivy/main/docs/guide/configuration/reporting.md); [LICENSE](https://raw.githubusercontent.com/aquasecurity/trivy/main/LICENSE)
- **yamllint**: GPL-3.0. Output formats in the CLI source are standard, parsable, colored, github and auto. No SARIF. `--strict` returns exit code 2 on warnings only. — [cli.py](https://raw.githubusercontent.com/adrienverge/yamllint/master/yamllint/cli.py); [configuration.rst](https://raw.githubusercontent.com/adrienverge/yamllint/master/docs/configuration.rst); [LICENSE](https://raw.githubusercontent.com/adrienverge/yamllint/master/LICENSE)
- **actionlint**: MIT. SARIF output is produced through the Go-template `-format` option, with a provided `sarif_template.txt`. — [docs/usage.md](https://raw.githubusercontent.com/rhysd/actionlint/main/docs/usage.md); [LICENSE.txt](https://raw.githubusercontent.com/rhysd/actionlint/main/LICENSE.txt)
- **kube-linter (StackRox)**: Apache-2.0. Analyzes Kubernetes YAML, Helm charts and Kustomize "with a focus on production readiness and security". Supports `--format sarif` and multiple formats in one run. — [README](https://raw.githubusercontent.com/stackrox/kube-linter/main/README.md); [LICENSE](https://raw.githubusercontent.com/stackrox/kube-linter/main/LICENSE)

### Inferences
- Copyleft matters for bundling. hadolint and yamllint are GPL-3.0, so invoke them as separate executables rather than linking them in. tflint release binaries are partly BUSL-1.1, which restricts competing hosted offerings. The others are permissive.
- Offline caveats: tflint provider plugins and Trivy's checks bundle and DB need a pre-download or mirror step. checkov and KICS ship their policies in the package or binary. Determinism holds when tool and policy-bundle versions are pinned.

### Gaps
- KICS query IDs are UUIDs per the docs structure; not verified this session.
- The Trivy misconfiguration check ID format (AVD-* versus newer IDs) and the offline bundle flags were not verified because trivy.dev was blocked.
- I did not enumerate the kube-linter check names, the tflint-ruleset-terraform rule names, or the hadolint codes missing from the README table.

## 7. Which tools emit SARIF (summary) and overall determinism and offline notes

### Takeaway
**Native SARIF:** SQLFluff (`main` source), hadolint, tflint, checkov, KICS, Trivy, kube-linter, gosec, Bandit (with the `[sarif]` extra), SpotBugs/FindSecBugs, and CodeQL (by design).
**SARIF via template:** actionlint.
**No SARIF:** Squawk (tty/gcc/json/gitlab), TSQLLint (none found), sqlcheck (text only), plpgsql_check (text/json/xml), yamllint (standard/parsable/colored/github), and db* CODECOP (SonarQube generic JSON, archived).

### Cited Findings
- SQLFluff `FormatType.sarif = "sarif"`. — [types.py](https://raw.githubusercontent.com/sqlfluff/sqlfluff/main/src/sqlfluff/core/types.py)
- hadolint `--format … sarif`. — [README](https://raw.githubusercontent.com/hadolint/hadolint/master/README.md)
- tflint `-f … sarif`. — [README](https://raw.githubusercontent.com/terraform-linters/tflint/master/README.md)
- checkov SARIF output. — [README](https://raw.githubusercontent.com/bridgecrewio/checkov/main/README.md)
- KICS `--report-formats "sarif"`. — [results.md](https://raw.githubusercontent.com/Checkmarx/kics/master/docs/results.md)
- Trivy `--format sarif`. — [reporting.md](https://raw.githubusercontent.com/aquasecurity/trivy/main/docs/guide/configuration/reporting.md)
- kube-linter `--format sarif`. — [README](https://raw.githubusercontent.com/stackrox/kube-linter/main/README.md)
- gosec `-fmt sarif`. — [README](https://raw.githubusercontent.com/securego/gosec/master/README.md)
- Bandit sarif formatter extra. — [setup.cfg](https://raw.githubusercontent.com/PyCQA/bandit/main/setup.cfg)
- SpotBugs SARIF. — [CHANGELOG](https://raw.githubusercontent.com/spotbugs/spotbugs/master/CHANGELOG.md)
- actionlint SARIF template. — [usage.md](https://raw.githubusercontent.com/rhysd/actionlint/main/docs/usage.md)
- Squawk reporters tty/gcc/json/gitlab. — [README](https://raw.githubusercontent.com/sbdchd/squawk/master/README.md)
- plpgsql_check text/json/xml. — [README](https://raw.githubusercontent.com/okbob/plpgsql_check/master/README.md)
- yamllint formats. — [cli.py](https://raw.githubusercontent.com/adrienverge/yamllint/master/yamllint/cli.py)
- db* CODECOP formats. — [plsql-cop-cli README](https://raw.githubusercontent.com/Trivadis/plsql-cop-cli/main/README.md)

### Inferences
- A deterministic offline SQL gate could be: SQLFluff (dialect-pinned, `--format sarif`) + Squawk for PostgreSQL migrations (JSON → SARIF adapter) + TSQLLint for SQL Server + sqlcheck anti-pattern IDs (text → SARIF adapter) + host-language CWE-89 rules (Bandit, gosec, SpotBugs/FindSecBugs, CodeQL where its license allows) + plpgsql_check against an ephemeral PostgreSQL for PL/pgSQL.
- License red flags for redistribution: db* CODECOP (CC BY-NC-ND/commercial), dbLinter (commercial), Liquibase (FSL-1.1, not OSI until the Apache conversion), Atlas default binaries (EULA; use the community build), tflint binaries (part BUSL-1.1), the Semgrep-maintained rules (Semgrep Rules License v1.0), the opengrep-rules Commons Clause, and the CodeQL CLI terms.

### Gaps
- TSQLLint and sqlcheck output formats were checked only via README text, not source. A JSON or SARIF option may exist undocumented.
- I did not re-verify any tool's determinism empirically (for example by running twice and diffing). The determinism claims above are inferences from each tool's design: rule-based linting with no network calls once installed.
