# Contributing to FluxGauss

Thank you for your interest in contributing! FluxGauss is a PL/pgSQL → Spring Boot + MyBatis Java converter with dual Python and Rust engines.

## Getting Started

### Prerequisites
- Python 3.10+ (Python engine)
- Rust 1.80+ (Rust engine)
- Java 17+ (for verifying generated output)

### Setup

```bash
git clone https://github.com/c2j/flux-gauss.git
cd flux-gauss

# Python engine
pip install -r requirements.txt

# Rust engine
cargo build
```

### Running Tests

```bash
# Python tests
pytest tests/

# Rust tests
cargo test

# Lint checks
cargo fmt --check
cargo clippy -- -D warnings
ruff check converter/
mypy converter/

# For integration tests with a real database:
export DB_PASSWORD=your_password
```

### Running the Converter

```bash
# Python engine
python3 converter/flux_gauss.py -c demo-project/fluxgauss_py.yaml

# Rust engine
cargo run --bin fluxgauss -- --config demo-project/fluxgauss_ru.yaml

# Verify generated output (requires Java 17+)
cd dest_py && mvn compile && mvn test
```

> **Note**: Integration test mode requires `export DB_PASSWORD=your_password` before running `mvn verify -Pintegration`. The demo YAML configs pass `${DB_PASSWORD}` through to Spring Boot for runtime resolution.

## Development Guidelines

- **Python engine**: The main converter is in `converter/flux_gauss.py`. Before refactoring, review `AGENTS.md` for module boundaries and architecture notes.
- **Rust engine**: Organized under `crates/fluxgauss/`. Follow the existing module structure.
- **Tests**: Add tests for new features. Python tests in `tests/`, Rust tests inline with `#[cfg(test)]` modules.
- **Code style**: Python follows `ruff.toml`, Rust follows `rustfmt.toml`.
- **Commits**: Write clear, concise commit messages. Reference issue numbers when applicable.

## Submitting Changes

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Run the full test suite and lint checks
5. Submit a pull request with a clear description of your changes

## Questions?

Open an issue or discussion on GitHub.
