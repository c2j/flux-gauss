# Regression Guard Implementation Plan

## Goal

Establish a regression testing framework covering **both Python and Rust engines** to catch conversion pipeline regressions before they ship. Three-layer defense per engine: pipeline integrity, golden file comparison, CI compile verification.

## Current State

| Aspect | Status |
|--------|--------|
| Python engine tests | 14 files in `tests/`, 434 pytest tests, all unit-level |
| Rust engine tests | Inline `#[cfg(test)]` modules in ~12 source files (~130 tests) |
| Regression tests | Only `test_integration.py` (cached-AST based, no output comparison) |
| CI runs pytest | **No** — CI only runs `flux_gauss.py -c` to verify converter doesn't crash |
| CI runs cargo test | **No** — CI only runs `cargo run --bin fluxgauss -- --config` |
| Golden/snapshot tests | **None** for either engine |
| `tests/regress/` | **Does not exist** |
| ogsql binary | Python engine: external binary (not on PATH locally); Rust engine: built-in git dependency |

## Key Design Decisions

1. **Do NOT move existing tests.** `tests/test_*.py` and Rust `#[cfg(test)]` modules are unit tests; regress tests are end-to-end. They serve different purposes and run independently.

2. **Shared fixtures, separate golden.** `tests/regress/fixtures/` holds input SQL for both engines. Golden files are per-engine: `golden/py/` for Python, `golden/ru/` for Rust. This enables future cross-engine equivalence comparison.

3. **Regress fixtures are copies, not symlinks.** Copied from `demo-project/sql/` to isolate regress tests from demo-project churn.

4. **AST caching for Python engine.** `tests/regress/.ast_cache/` stores ogsql-parser output so tests don't need the binary on every run. Rust engine doesn't need this (ogsql-parser is compiled in).

5. **Golden files are committed.** Each `.golden` file is a normalized version of one generated Java/XML file. Manual review required before first commit.

6. **Two flag modes for golden management:**
   - `--regress-save`: generate golden files from scratch (first time or full reset)
   - `--regress-update`: overwrite golden files with current output (after intentional changes)

7. **`ConversionReport` is stored but NOT compared.** `report.json.golden` is saved for reference/debugging only — it contains `generated_at` timestamps that would break comparison. The 4 code files (Service.java, Mapper.java, Mapper.xml, ServiceTest.java) are the compared artifacts.

## Directory Structure

```
tests/regress/                        # Python engine regression tests
├── __init__.py
├── conftest.py                       # fixtures: state reset, AST cache, ogsql detection
├── test_pipeline.py                  # Layer 1: pipeline integrity
├── test_golden.py                    # Layer 2: golden file comparison
├── fixtures/                         # Input SQL (shared with Rust engine via symlink or copy)
│   ├── pkg_order.sql
│   ├── pkg_dynamic_xml.sql
│   ├── complex_clearing_pkg.sql
│   ├── gauss_complete_examples.sql
│   └── PKG_WARPDRIVER_STRESS_TEST.sql
├── golden/                           # Expected output
│   ├── build_manifest.json           # engine version + fixture list
│   ├── py/                           # Python engine golden files
│   │   ├── order/
│   │   │   ├── OrderService.java.golden
│   │   │   ├── OrderMapper.java.golden
│   │   │   ├── OrderMapper.xml.golden
│   │   │   └── OrderServiceTest.java.golden
│   │   ├── dynamicXml/
│   │   │   └── ...
│   │   └── ...
│   └── ru/                           # Rust engine golden files (future, same structure)
│       └── ...
└── .ast_cache/                       # gitignored: cached ogsql parse results

crates/fluxgauss/tests/regress/       # Rust engine regression tests (future Step 5)
├── fixtures/                         # symlink or copy of tests/regress/fixtures/
├── test_golden.rs                    # Layer 2: golden file comparison for Rust engine
└── test_pipeline.rs                  # Layer 1: pipeline integrity for Rust engine
```

## Three-Layer Defense

### Layer 1: Pipeline Integrity (`test_pipeline.py` / `test_pipeline.rs`)
**What**: For each fixture SQL, run full conversion pipeline and verify structural invariants.
**Does NOT compare** output content — only checks that pipeline completes and key metrics are within expected ranges.

Assertions per fixture SQL (both engines):
- Parse returns valid AST (no errors, has `statements`)
- `extract_procedures()` returns ≥ expected number of procedures
- Each procedure has non-null `name`, `package`, `proc_name`, `body`, `source_file`
- `analyze_procedure()` produces `java_logic_lines` for every procedure
- At least N procedures have DML statements (N defined per fixture)
- `generate_project()` writes all 4 expected files: Service.java, Mapper.java, Mapper.xml, ServiceTest.java

Test shape: `@pytest.mark.parametrize("sql_file", _fixture_sql_files())` (Python) / `rstest` parametrize (Rust).

### Layer 2: Golden File Comparison (`test_golden.py` / `test_golden.rs`)
**What**: Re-run full pipeline, compare every generated file byte-for-byte against committed golden files.
**This is the precise regression catch** — any output change is flagged.

Per package assertions (both engines):
- `{Pkg}Service.java` matches golden
- `{Pkg}Mapper.java` matches golden
- `{Pkg}Mapper.xml` matches golden
- `{Pkg}ServiceTest.java` matches golden

`ConversionReport` (report.json) is saved to golden directory for reference but NOT compared — it contains `generated_at` timestamps.

Normalization before comparison:
- Trailing whitespace stripped
- Consecutive blank lines collapsed to max 2

Golden files are generated once (`--regress-save`), manually reviewed for correctness, then committed. Subsequent runs compare against committed golden files. Update with `--regress-update` when intentional changes alter output.

### Layer 3: CI Compile + Test Verification
**What**: In CI, after regression tests pass, run `mvn compile` on generated output AND run `pytest` / `cargo test` on the converter's own tests.
Currently the CI only runs the converter itself — neither the hand-written tests nor generated output is verified.

**Python engine CI verification:**
```yaml
- name: Run Python unit tests
  run: python3 -m pytest tests/ -v --tb=short
- name: Run regression tests
  run: python3 -m pytest tests/regress/ -v --tb=short
- name: Convert + compile check
  run: |
    python3 converter/flux_gauss.py -c demo-project/fluxgauss_py.yaml
    cd dest_py && mvn compile
```

**Rust engine CI verification:**
```yaml
- name: Run Rust tests
  run: cargo test --workspace
- name: Convert + compile check
  run: |
    cargo run --release --bin fluxgauss -- --config demo-project/fluxgauss_ru.yaml
    cd dest_ru && mvn compile
```

## Python Engine: conftest.py — Critical Infrastructure

### Global State Reset
`flux_gauss.py` has 24 module-level mutable containers. Every regress test needs clean state.
Autouse fixture resets: `UNRESOLVED_CALLS`, `STUB_PROCEDURES`, `UNSUPPORTED_FUNCTIONS`, `TODO_SUMMARY`, `STUB_REASONS`, `_MISSING_OVERLOADS`, `_PACKAGE_CONSTANTS`, `_PACKAGE_VARIABLES`, `_UDF_RETURN_TYPES`, `_TABLE_DDL_SOURCE`, `_SQL_FILE_CACHE`, `TYPE_OVERRIDES`.

(Note: `_SQL_FILE_CACHE` is NOT reset by `tests/conftest.py` — added here because regress tests may parse the same SQL files repeatedly across test cases.)

### ogsql Binary Detection
```python
def _ogsql_available() -> bool:
    # Check OGSQL_BIN (resolved by converter) is callable
```
Tests skip with `pytest.skip` if ogsql not available AND AST not cached.

### AST Cache (`_get_cached_ast`)
```python
def _get_cached_ast(sql_file: str) -> dict:
    # 1. Check .ast_cache/{filename}.json
    # 2. If missing + ogsql available → parse → write cache
    # 3. If missing + ogsql unavailable → pytest.skip
```
Cache directory is gitignored. In CI, ogsql is always available so cache is populated on first run.

### Session-Scoped Fixtures
```python
@pytest.fixture(scope="session")
def cached_ast() -> dict:
    # {filename: AST dict} for all fixtures

@pytest.fixture(scope="session")
def cached_ast_by_pkg() -> dict:
    # {pkg_name: AST dict} for golden file tests
```

## Rust Engine: Test Design Notes

The Rust engine regression tests will mirror the Python structure but follow Rust conventions:

- **Location**: `crates/fluxgauss/tests/regress/` (Rust integration test convention — files here are separate crates linked against the library)
- **Fixture sharing**: Read SQL fixtures from `tests/regress/fixtures/` (relative path from crate root)
- **Golden files**: Stored at `tests/regress/golden/ru/`
- **Layer 1**: `test_pipeline.rs` — calls `extract_procedures()`, `analyze_procedure()`, `generate_project()` via the Rust library API
- **Layer 2**: `test_golden.rs` — compares generated output against golden files

Implementation order: Python first (Steps 1–4), then Rust engine (Step 5). This is because:
1. Python engine is the reference implementation (rated A- vs Rust's B+)
2. Python testing infrastructure is already established (pytest, conftest patterns)
3. Rust engine needs the golden baseline from Python to verify

## Fixture SQL Selection (5 initial files, shared by both engines)

| File | Est. Procs | Key Features Covered |
|------|-----------|---------------------|
| `pkg_order.sql` | ~6 | CRUD, IF/ELSE, cross-package calls, FOR LOOP |
| `complex_clearing_pkg.sql` | ~4 | FUNCTION + PROCEDURE, nested blocks, cursors |
| `gauss_complete_examples.sql` | ~5+ | Exception handling, WHILE, CASE, dynamic SQL |
| `pkg_dynamic_xml.sql` | ~3 | Dynamic SQL → MyBatis `<if>` `<where>` `<set>` |
| `PKG_WARPDRIVER_STRESS_TEST.sql` | 5+ | Large procedures, complex cross-package deps |

## CI Integration

### New jobs in `.github/workflows/release.yml`:

**Python regression guard** (new job):
```yaml
test-python-regress:
  name: Python regression guard
  runs-on: ubuntu-22.04
  needs: [build-ogsql-linux-x86_64]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: "3.12" }
    - run: pip install pyyaml pytest
    - name: Download ogsql
      uses: actions/download-artifact@v4
      with: { name: ogsql-linux-x86_64, path: ogsql-artifact }
    - name: Prepare ogsql
      run: |
        unzip ogsql-artifact/ogsql-*.zip -d ogsql-artifact || tar xzf ogsql-artifact/ogsql-*.tar.gz -C ogsql-artifact
        chmod +x ogsql-artifact/ogsql
        echo "OGSQL_BIN=$PWD/ogsql-artifact/ogsql" >> "$GITHUB_ENV"
    - name: Run Python unit tests
      run: python3 -m pytest tests/ -v --tb=short --ignore=tests/regress
    - name: Run regression tests
      run: python3 -m pytest tests/regress/ -v --tb=short
```

**Enhance existing `test-python-engine` job** — add compile verification:
```yaml
    # Add after existing converter run step:
    - uses: actions/setup-java@v4
      with: { java-version: "17", distribution: "temurin" }
    - name: Compile generated output
      run: cd dest_py && mvn compile --batch-mode
```

**Enhance existing `test-rust-engine` job** — add tests and compile:
```yaml
    # Add after existing converter run step:
    - name: Run Rust tests
      run: cargo test --workspace
    - uses: actions/setup-java@v4
      with: { java-version: "17", distribution: "temurin" }
    - name: Compile generated output
      run: cd dest_ru && mvn compile --batch-mode
```

**Rust regression guard** (future Step 5, new job):
```yaml
test-rust-regress:
  name: Rust regression guard
  runs-on: ubuntu-22.04
  steps:
    - uses: actions/checkout@v4
    - uses: dtolnay/rust-toolchain@stable
    - uses: actions/setup-java@v4
      with: { java-version: "17", distribution: "temurin" }
    - name: Run Rust regression tests
      run: cargo test --test regress -- --test-threads=1
```

## Implementation Steps

### Step 0: Prerequisites
- [ ] Verify ogsql binary can be built/downloaded (CI already downloads it)
- [ ] Confirm `pytest` and `pyyaml` are installable in CI
- [ ] Confirm Java 17 + Maven available in CI runner image (yes — ubuntu-22.04 has both via setup-java)

### Step 1: Directory + Python conftest
- [ ] Create `tests/regress/` directory structure (including `golden/py/` and `golden/ru/`)
- [ ] Write `tests/regress/__init__.py`
- [ ] Write `tests/regress/conftest.py` (state reset including `_SQL_FILE_CACHE` + AST cache + ogsql detection + `--regress-save/update` hooks)
- [ ] Copy 5 SQL fixtures from `demo-project/sql/` to `tests/regress/fixtures/`
- [ ] Add `.ast_cache/` to `.gitignore`
- [ ] Verify: `pytest tests/regress/ --collect-only`

### Step 2: Layer 1 — Pipeline Integrity Tests (Python)
- [ ] Write `tests/regress/test_pipeline.py`
- [ ] Implement `test_parse_sql_file`, `test_extract_procedures`, `test_analyze_procedures`, `test_generate_project`
- [ ] Define `EXPECTED` per-fixture baselines (min procs, min DML)
- [ ] Run locally: `pytest tests/regress/test_pipeline.py -v`

### Step 3: Layer 2 — Golden File Tests (Python)
- [ ] Write `tests/regress/test_golden.py`
- [ ] Implement golden comparison for 4 file types per package (NOT report.json)
- [ ] Save report.json to golden dir for reference only
- [ ] Run `pytest tests/regress/ --regress-save` to generate baseline golden files
- [ ] Manually review golden files for correctness
- [ ] Commit golden files + `build_manifest.json`
- [ ] Run `pytest tests/regress/` (no flags) to verify comparison passes

### Step 4: CI Integration (Python)
- [ ] Add `test-python-regress` job to `release.yml`
- [ ] Enhance `test-python-engine`: add `pip install pytest` + `pytest tests/` + `mvn compile`
- [ ] Enhance `test-rust-engine`: add `cargo test --workspace` + `mvn compile`
- [ ] Trigger CI run and verify all green

### Step 5: Rust Engine Regression ✓ (smoke + golden)
- [x] Create `crates/fluxgauss/tests/regress.rs` (195 lines)
- [x] Share fixtures from `tests/regress/fixtures/`
- [x] Pipeline smoke test: 4 packages, all generate 4 file types, all non-empty
- [x] Golden comparison: 4 packages × 4 file types = 16 goldens, fully deterministic
- [x] Fixed HashMap → sorted iteration in service.rs and mapper.rs (6 iteration points)
- [x] CI: `cargo test --test regress` added to `test-rust-engine` job
- [x] Golden files committed at `tests/regress/golden/ru/` with `{FIXTURES_ROOT}` path normalization

### Step 6: Ongoing Expansion (continuous)
- [ ] Per bug fix: add minimal repro SQL → record golden (both engines)
- [ ] Per new feature: add demo SQL → record golden (both engines)
- [ ] Cross-engine equivalence check: compare `golden/py/` vs `golden/ru/` for same fixtures
- [ ] When fixture count > 10, categorize: `fixtures/dml/`, `fixtures/control/`, etc.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| ogsql binary missing locally (Python) | AST caching + `pytest.skip` when neither binary nor cache available |
| Golden files get stale after intentional changes | `--regress-update` flag for intentional updates; PR review catches accidental changes |
| ogsql version upgrade changes AST → all Python golden break | `build_manifest.json` records version; intentional bulk update with `--regress-update` |
| Rust/Python engines produce different output for same SQL | This is EXPECTED while engines are at different maturity levels (A- vs B+). Golden files per engine track each independently. Cross-engine equivalence comparison is Step 6. |
| `ConversionReport` timestamp makes `report.json.golden` non-deterministic | report.json is saved for reference only; NOT compared in Layer 2 assertions |
| CI slower with regress tests | Session-scoped AST caching; Layer 1 + 2 combined should complete in < 2 minutes per engine |
| Rust engine crate structure changes | Rust regress tests live in `crates/fluxgauss/tests/` — standard Rust integration test location, follows crate conventions |
