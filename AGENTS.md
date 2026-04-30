# AGENTS.md — FluxGauss (sp2java)

## What This Repo Does

Converts OpenGauss/PostgreSQL stored procedures (PL/pgSQL) into a Spring Boot + MyBatis Java project.

## Key Files

- `converter/flux_gauss.py` — Single-file Python converter (~4800 lines). All logic lives here.
- `lib/ogsql-parser/` — Rust-based SQL parser (submodule). Produces JSON AST from SQL files.
- `demo-project/fluxgauss.yaml` — Example config referencing `demo-project/sql/*.sql` sources.
- `dest/` — Generated output (gitignored). Contains a complete Maven/Spring Boot project.
- `使用指南.md` — Full user guide (Chinese). Covers config, CLI, and supported PL/pgSQL features.

## How to Run

### Convert SQL to Java

```bash
# Config-file mode (recommended, supports incremental)
python3 converter/flux_gauss.py -c fluxgauss.yaml

# CLI mode (one-shot, no caching)
python3 converter/flux_gauss.py -o ./dest -s sql/file1.sql sql/file2.sql

# Force full regeneration
python3 converter/flux_gauss.py -c fluxgauss.yaml --full
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
```

Data flow inside `flux_gauss.py`:
1. `parse_sql_file()` calls ogsql binary → raw AST dict
2. `extract_procedures()` → `ProcedureInfo[]`
3. `analyze_procedure()` fills DML statements, Java logic lines, service calls
4. `generate_project()` writes Java/XML files to output dir

## Incremental Build

Config-file mode auto-caches in `dest/.fluxgauss/` (manifest.json + AST JSONs). Only changed SQL files are re-parsed. Transitive dependencies (cross-package calls) trigger re-generation of callers.

Clear cache: `rm -rf dest/.fluxgauss`

## Config (`fluxgauss.yaml`)

```yaml
output_dir: ./dest
base_package: com.example.demo
sources:
  - sql/pkg_order.sql
  - sql/pkg_product.sql
java_packages:                    # optional: map SQL files to different Java packages
  - package: com.example.order
    sources:
      - sql/pkg_order.sql
# logger: slf4j                   # slf4j (default), log4j2, commons-logging, jul, or custom dict
# database:                       # optional: generates application.yml
#   url: jdbc:postgresql://...
```

## Conventions

- Each SQL package → 4 files: `{Name}Service.java`, `{Name}Mapper.java`, `{Name}Mapper.xml`, `{Name}ServiceTest.java`
- Skeleton files (`pom.xml`, `application.yml`, `DemoApplication.java`) are written **only if they don't exist**. To regenerate, delete them first.
- Source tracing comments (`// Source: file.sql:line-range`) are injected into all generated code for debugging.
- Naming: `pkg_order` → `OrderService`, `create_order` → `createOrder()`

## When Modifying `converter/flux_gauss.py`

- The entire converter is one file (~4800 lines). Key dataclasses: `ProcedureInfo`, `PackageInfo`, `DmlStatement`, `Parameter`, `CommentInfo`.
- ogsql binary path is resolved at import time via `_resolve_ogsql_bin()`.
- After changes, run: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`
- Conversion reports are saved to `dest/.fluxgauss/reports/`.
- The ogsql-parser Rust binary is a separate build. Prebuilt binaries are in `lib/ogsql-parser/target/`. Do not modify unless intentionally updating the parser.

## Notes

- `ogsql.broken` at root is a broken binary — ignore it.
- `SQL` file at root is empty — ignore it.
- `docs/plans/` contains implementation plans for features in progress.
- No linter, formatter, or CI config exists in this repo.
- Dependencies: Python `pyyaml` (optional, for YAML config), no `requirements.txt`.
