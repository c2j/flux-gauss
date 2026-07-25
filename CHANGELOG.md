# Changelog

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
