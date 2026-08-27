# Changelog

## [0.6.27] - 2026-08-27

### Fixed
- R3 migration fixes — INSTR/CASE/EXCEPTION/SUBSTR/RETURN-type/BigDecimal-init (#60–#64, #65)
- Resolve 13 root causes of Java compilation errors in Rust engine exam output (188→57 errors) (#66)
- Prevent debug stderr prints from corrupting progress bar display (#67)
- Resolve 20+ compilation and runtime errors in generated Java code (#68)
- Merge same-schema packages, resolve cross-calls, String→numeric coercion, orphaned try (#70, #71, #72, #75, #76)

### Added
- 回归验证守护体系 — L0 framework + 7 bug fixes + integration tests all green (#69)

## [0.6.26] - 2026-07-26

### Fixed
- Normalize CRLF/CR line endings to LF in SQL file reading (#59)

## [0.6.25] - 2026-07-26

### Changed
- Update Python test references; make Rust compile non-blocking
- Rename regression fixture to match SQL package name
- CI: remove tar.gz from release, move tests to ci.yml

## [0.6.24] - 2026-07-26

### Fixed
- Empty string `''` → NUMBER emits null instead of `Long.parseLong("")` (#57)
- Nested exception_block handling in Rust GOTO Block handlers (#54)
- Swap ELSIF/ELSE ordering for GOTO Pattern D (#44)
- Ambiguous overloaded mapper method references in generated tests (#55)

### Changed
- Rust: format generated Java/XML code with proper indentation (#53)

## [0.6.23] - 2026-07-26

### Changed
- CI: auto-regenerate golden files in CI environment; env mapping for REGEN_RUST_GOLDEN; fix Rust regress test

## [0.6.21] - 2026-07-26

### Fixed
- Nested Block.exception_block, ThreadLocal, duplicate mappers (#52)

## [0.6.20] - 2026-07-25

### Fixed
- CI golden file fragility

## [0.6.19] - 2026-07-25

### Fixed
- Pre-existing test failures

## [0.6.18] - 2026-07-25

### Changed
- Separate CI from release; add test gates to release; update golden files; fix Rust test

## [0.6.17] - 2026-07-25

### Fixed
- Preserve IF conditions in dynamic SQL build lines (#44)
- Merge multi-WHEN exception handlers into single catch block (#45)
- Remove redundant `(int)` cast from ascii template (#46)
- Remove aggressive id/no/seq→Long column-name heuristic (#47, #49)
- Add String→Long coercion before BinaryOp early-return (#48)
- Resolve issues #35, #37, #38, #39, #40 (both Python & Rust engines)

### Added
- Dual-engine regression guard framework

### Changed
- Project cleanup, tooling, and structure improvements
- CI: restore version extraction from root Cargo.toml, fix MCP type mismatch

## [0.6.16] - 2026-07-16

### Changed
- Replace hardcoded `VERSION` constant with `env!("CARGO_PKG_VERSION")` in Rust CLI
- Workspace Cargo.toml: hoist shared dependencies and package metadata
- Remove unused `rayon` dependency (parallelism not yet implemented)

### Removed
- Dead `benches/parse_benchmark.rs` (empty stub)
- Empty placeholder files under `statements/` (unfinished refactor)
- Root-level garbage files: `SQL`, `.DS_Store`, empty `lib/`

### Security
- Demo YAML configs: replace hardcoded passwords with `${DB_PASSWORD}` env var

### Added
- MIT LICENSE
- `CONTRIBUTING.md`
- `rustfmt.toml`, `ruff.toml`, `mypy.ini` configs
- `.sisyphus/` added to `.gitignore`

## [0.6.15] - 2026-07-12

### Added
- MCP server mode (`--mcp`) for Python and Rust engines
- `fluxgauss-mcp` crate for Rust MCP server
- Multi-encoding support (`--encoding` flag and YAML config)

### Changed
- Conversion report now auto-generated in Markdown format
- Incremental build: SHA-256 content caching with transitive dependency tracking

## [0.6.14] - 2026-06-20

### Added
- PyInstaller packaging for standalone binary distribution
- Debug mode (`--debug`) for SQL source line tracing
- Integration test generation with Testcontainers support

### Changed
- Improved dynamic SQL → MyBatis XML conversion
- Enhanced GOTO statement conversion

## [0.6.13] - 2026-06-04

### Added
- Dynamic SQL → MyBatis Dynamic XML tags (`<if>`, `<where>`, `<set>`)
- 110+ built-in SQL function mappings
- Cross-package dependency auto-resolution

### Changed
- Python engine: expanded from ~8500 to ~15000 lines
- Rust engine: improved feature parity with Python engine

## [0.5.0] - 2026-05-01

### Added
- Initial Rust engine implementation
- DML statement extraction and analysis
- Spring Boot project generation (Service, Mapper, XML, Test)
- Support for 20+ PL/pgSQL statement types
