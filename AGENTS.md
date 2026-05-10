# AGENTS.md — FluxGauss (sp2java)

## What This Repo Does

Converts OpenGauss/PostgreSQL stored procedures (PL/pgSQL) into a Spring Boot + MyBatis Java project. The converter parses SQL via a Rust-based AST parser, then generates Service classes, Mapper interfaces, MyBatis XML mappers, unit tests, and optional integration tests.

## Key Files

- `converter/flux_gauss.py` — Single-file Python converter (~8500 lines). All logic lives here.
- `lib/ogsql-parser/` — Rust-based SQL parser (submodule). Produces JSON AST from SQL files.
- `demo-project/fluxgauss.yaml` — Example config referencing `demo-project/sql/*.sql` sources.
- `dest/` — Generated output (gitignored). Contains a complete Maven/Spring Boot project.
- `使用指南.md` — Full user guide (Chinese). Covers config, CLI, and supported PL/pgSQL features.
- `docs/plans/` — Implementation plans for features in progress.

## How to Run

### Convert SQL to Java

```bash
# Config-file mode (recommended, supports incremental)
python3 converter/flux_gauss.py -c fluxgauss.yaml

# CLI mode (one-shot, no caching)
python3 converter/flux_gauss.py -o ./dest -s sql/file1.sql sql/file2.sql

# Force full regeneration
python3 converter/flux_gauss.py -c fluxgauss.yaml --full

# Resume from checkpoint (skip already-generated packages)
python3 converter/flux_gauss.py -c fluxgauss.yaml --resume

# Specify report output path
python3 converter/flux_gauss.py -c fluxgauss.yaml --report ./report.md
```

### Verify Output

```bash
cd dest && mvn compile   # compile check
cd dest && mvn test      # run generated unit tests
```

### Requirements

- Python 3.9+
- Java 17+ (for `mvn compile` verification)
- `ogsql` binary — resolved automatically from `lib/ogsql-parser/target/{arch}/release/ogsql`, or set `OGSQL_BIN` env var

## Architecture

```
SQL files → ogsql-parser (Rust binary) → JSON AST
                                               ↓
                                     flux_gauss.py (Python)
                                               ↓
                    Spring Boot project: Service.java + Mapper.java + Mapper.xml + Test.java
                  + optional: integration tests (itest/) + BusinessException.java
```

Data flow inside `flux_gauss.py`:
1. `parse_sql_file()` calls ogsql binary → raw AST dict
2. `extract_procedures()` → `ProcedureInfo[]` (also extracts package variables, custom types)
3. `extract_comments()` + `_map_comments_to_procedures()` → attach `CommentInfo` to procedures
4. `analyze_procedure()` fills DML statements, Java logic lines, service calls, cursor tracking
5. `generate_project()` writes Java/XML files to output dir + optional integration tests
6. Conversion report (`ConversionReport`) saved with procedure mappings and skipped items

## Incremental Build

Config-file mode auto-caches in `dest/.fluxgauss/` (manifest.json + AST JSONs). Only changed SQL files are re-parsed. Transitive dependencies (cross-package calls) trigger re-generation of callers.

Resume from checkpoint: `--resume` flag skips packages already successfully generated (tracked via `dest/.fluxgauss/gen-checkpoint.json`).

Clear cache: `rm -rf dest/.fluxgauss`

## Config (`fluxgauss.yaml`)

```yaml
output_dir: ./dest
base_package: com.example.demo

# Optional: integration testing (generates itest classes)
integration_test:
  enabled: true
  mode: remote            # remote (connects to real DB)
  url: jdbc:postgresql://localhost:5432/postgres
  username: gaussdb
  password: secret

sources:
  - demo-project/sql/pkg_order.sql
  - demo-project/sql/pkg_product.sql
java_packages:                    # optional: map SQL files to different Java packages
  - package: com.example.order
    sources:
      - demo-project/sql/pkg_order.sql
# logger: slf4j                   # slf4j (default), log4j2, commons-logging, jul, or custom dict
# database:                       # optional: generates application.yml
#   url: jdbc:postgresql://...
```

## Conventions

- Each SQL package → 4 files: `{Name}Service.java`, `{Name}Mapper.java`, `{Name}Mapper.xml`, `{Name}ServiceTest.java`
- Skeleton files (`pom.xml`, `application.yml`, `DemoApplication.java`, `BusinessException.java`) are written **only if they don't exist**. To regenerate, delete them first.
- Integration test files are generated under `src/test/java/.../itest/` with `AbstractIntegrationTest` base class and `itest-schema.sql` fixtures.
- Source tracing comments (`// Source: file.sql:line-range`) are injected into all generated code for debugging.
- Naming: `pkg_order` → `OrderService`, `create_order` → `createOrder()`
- Comments from SQL sources are preserved: `leading_comments` (before procedure) and `inline_comments` (inside body) are carried into generated Java code.

## When Modifying `converter/flux_gauss.py`

- The entire converter is one file (~8500 lines). Key dataclasses:
  - `Parameter` — procedure parameter with SQL/Java type mapping
  - `CommentInfo` — SQL comment (text, line range, type: single-line/block)
  - `DmlStatement` — extracted DML with method_id, SQL text, parameter types, optional filters
  - `ServiceCall` — cross-service method call reference
  - `ProcedureInfo` — main dataclass for a procedure, including cursor tracking, custom types, dynamic SQL templates, scheduler tasks, source location, comments
  - `PackageInfo` — all procedures in a package with package vars, custom types, comments
  - `SkippedItem` — non-procedure statements (DDL, grants, etc.) skipped during extraction
  - `ProcedureMapping` — per-procedure conversion result (Java service/method, stub status, notes)
  - `ConversionReport` — full conversion report with mappings, errors, statistics
- ogsql binary path is resolved at import time via `_resolve_ogsql_bin()`.
- After changes, run: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`
- Conversion reports are saved to `dest/.fluxgauss/reports/`.
- Generation checkpoint is saved to `dest/.fluxgauss/gen-checkpoint.json`.
- The ogsql-parser Rust binary is a separate build. Prebuilt binaries are in `lib/ogsql-parser/target/`. Do not modify unless intentionally updating the parser.

## Key Internal Modules (by line range)

| Section | Lines | Description |
|---|---|---|
| Constants & config | 1–175 | `OGSQL_BIN`, `BASE_PACKAGE`, logger presets, SQL type maps |
| `parse_table_ddl()` | 176–253 | Parses `CREATE TABLE` DDL for schema info |
| Type maps | 254–370 | `SQL_TO_JAVA`, `SQL_TO_JDBC_TYPE`, `TYPE_OVERRIDES`, stub tracking |
| Logging & progress | 371–438 | `_init_log()`, `_log()`, `_progress_bar()` |
| Tracking helpers | 439–470 | `_record_unsupported()`, `_record_todo()` |
| Type conversion | 471–624 | `sql_type_to_java()`, `sql_type_to_jdbc()`, `java_type_to_jdbc()` |
| Naming utilities | 626–700 | `snake_to_camel()`, `package_to_classname()`, `java_method_name()` |
| Dataclasses | 712–860 | `Parameter`, `CommentInfo`, `DmlStatement`, `ServiceCall`, `ProcedureInfo`, `PackageInfo`, `SkippedItem`, `ProcedureMapping`, `ConversionReport` |
| SQL parsing | 867–1000 | `_split_sql_statements()`, `_read_sql_file()`, `parse_sql_file()` |
| AST extraction | 1016–1260 | `extract_parameters()`, `extract_procedures()`, `_recover_constant_declarations()` |
| Comment handling | 1307–1390 | `extract_comments()`, `_map_comments_to_procedures()` |
| Non-procedure extraction | 1394–1515 | `extract_non_procedure_statements()` — DDL/grant/type skips |
| DML analysis | 1519–1710 | `_extract_dml_target()`, OUT param promotion, `analyze_procedure()` |
| Comment injection | 1778–1935 | `_inject_inline_comments()`, `_find_body_stmt_lines()` |
| Statement processing | 1937–3460 | `_process_statement()` dispatch → SQL, IF, FOR, WHILE, LOOP, cursor ops, assignments, procedure calls, EXECUTE, RAISE, CASE, etc. |
| SQL reconstruction | 3464–3780 | Dynamic SQL template handling, concat flattening, placeholder conversion |
| Expression → Java | 3780–5220 | `_expr_to_java()`, `SQL_FUNCTION_MAP`, `SPECIAL_FUNCTION_MAP`, type inference, coercion |
| Project generation | 5227–7110 | `generate_project()` → `_write_pom_xml()`, `_write_mapper_xml()`, `_write_service_class()`, `_write_service_test()`, etc. |
| Integration tests | 7178–7670 | `_itest_*()` functions — schema extraction, test data inference, fixture generation, itest class writing |
| Report & CLI | 7680–8514 | `ConversionReport`, `_save_gen_checkpoint()`, `_build_arg_parser()`, `main()` |

## Notes

- `ogsql.broken` at root is a broken binary — ignore it.
- `SQL` file at root is empty — ignore it.
- `docs/plans/` contains implementation plans for features in progress.
- No linter, formatter, or CI config exists in this repo.
- Dependencies: Python `pyyaml` (optional, for YAML config), no `requirements.txt`.
