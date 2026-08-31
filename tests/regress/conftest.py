"""
Shared fixtures for flux-gauss regression guard tests.

Key concerns:
1. flux_gauss.py has extensive module-level mutable state that MUST be
   reset between tests (12 tracked containers, plus TYPE_OVERRIDES).
2. ogsql binary may not be available locally — AST caching + pytest.skip
   handles this gracefully.
3. Golden file tests use --regress-save / --regress-update CLI flags.
"""

import hashlib
import json
import os
import subprocess
import sys
import warnings

import pytest

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"flux-gauss requires Python >= 3.10 (converter/flux_gauss.py uses PEP 604 "
        f"`int | None` syntax, which raises TypeError on 3.9). Detected {sys.version.split()[0]}. "
        f"Create a venv with a 3.10+ interpreter, e.g.: "
        f"python3.14 -m venv .venv && .venv/bin/pip install pytest pyyaml"
    )

# Add project root so we can import converter.flux_gauss
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import converter.flux_gauss as fg

# ── Paths ───────────────────────────────────────────────────────
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
AST_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".ast_cache")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")


# ── CLI Flags for Golden Management ─────────────────────────────
def pytest_addoption(parser):
    parser.addoption(
        "--regress-save",
        action="store_true",
        default=False,
        help="Generate golden files from current output (first-time baseline).",
    )
    parser.addoption(
        "--regress-update",
        action="store_true",
        default=False,
        help="Overwrite golden files with current output (after intentional changes).",
    )


@pytest.fixture(scope="session")
def regress_save(request):
    return request.config.getoption("--regress-save")


@pytest.fixture(scope="session")
def regress_update(request):
    return request.config.getoption("--regress-update")


# ── Global State Reset ──────────────────────────────────────────
# flux_gauss.py uses many module-level mutable containers.
# Every test must start with clean state.
# This is MORE thorough than tests/conftest.py — adds _SQL_FILE_CACHE
# which is critical when regress tests re-parse the same SQL fixtures.


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset ALL module-level mutable state before each regress test."""
    # Tracking lists
    fg.UNRESOLVED_CALLS.clear()
    fg.STUB_PROCEDURES.clear()
    fg.UNSUPPORTED_FUNCTIONS.clear()
    fg.TODO_SUMMARY.clear()

    # Tracking dicts
    fg.STUB_REASONS.clear()
    fg._MISSING_OVERLOADS.clear()
    fg._PACKAGE_CONSTANTS.clear()
    fg._PACKAGE_VARIABLES.clear()
    fg._PACKAGE_VAR_WRITTEN.clear()
    fg._DML_COUNTER_BY_PKG.clear()
    fg._DML_CTR_TRACKER = None
    fg._UDF_RETURN_TYPES.clear()
    fg._TABLE_DDL_SOURCE.clear()
    fg._SQL_FILE_CACHE.clear()

    # TYPE_OVERRIDES is a config dict — save/restore
    original_overrides = dict(fg.TYPE_OVERRIDES)
    yield
    fg.TYPE_OVERRIDES.clear()
    fg.TYPE_OVERRIDES.update(original_overrides)


# ── ogsql Binary Detection ──────────────────────────────────────


def _ogsql_available() -> bool:
    """Check if the ogsql binary resolved by flux_gauss is callable."""
    try:
        result = subprocess.run(
            [fg.OGSQL_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return False


_OGSQL_CHECKED = None


def _check_ogsql():
    """Cached check — only runs once per session."""
    global _OGSQL_CHECKED
    if _OGSQL_CHECKED is None:
        _OGSQL_CHECKED = _ogsql_available()
    return _OGSQL_CHECKED


# ── AST Cache ───────────────────────────────────────────────────


def _run_ogsql_parse(sql_path: str) -> dict:
    """Call ogsql binary to parse a SQL file, return AST dict."""
    raw, encoding = fg._read_sql_file(sql_path)
    result = subprocess.run(
        [fg.OGSQL_BIN, "parse", "-j"],
        input=raw,
        capture_output=True,
        text=True,
        timeout=30,
        encoding=encoding,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ogsql parse failed for {sql_path}:\n{result.stderr[:500]}")
    if not result.stdout.strip():
        raise RuntimeError(f"ogsql produced empty output for {sql_path}")
    return json.loads(result.stdout)


def _resolve_fixture_path(sql_file: str):
    sql_full = os.path.join(FIXTURES_DIR, sql_file) if not os.path.isabs(sql_file) else sql_file
    if os.path.isfile(sql_full):
        return sql_full
    alt_path = os.path.join(os.path.dirname(__file__), "..", "..", "demo-project", "sql", sql_file)
    return alt_path if os.path.isfile(alt_path) else None


def _sql_content_hash(sql_full: str) -> str:
    with open(sql_full, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _get_cached_ast(sql_file: str) -> dict:
    """Get AST for a SQL file, reparsing when the SQL no longer matches the cache.

    The cache is committed so the suite can run without the ogsql binary. It is
    keyed by filename only, so a stale entry would otherwise make an edited
    fixture silently test its OLD content. A sidecar .sha256 of the SQL guards
    that: on mismatch (or a legacy entry with no sidecar) the file is reparsed
    when ogsql is available, and only falls back to the stale cache when it
    isn't.
    """
    cache_key = sql_file.replace("/", "_").replace("\\", "_")
    cache_path = os.path.join(AST_CACHE_DIR, f"{cache_key}.json")
    hash_path = f"{cache_path}.sha256"

    sql_full = _resolve_fixture_path(sql_file)
    current_hash = _sql_content_hash(sql_full) if sql_full else None

    if os.path.exists(cache_path):
        cached_hash = None
        if os.path.exists(hash_path):
            with open(hash_path, encoding="utf-8") as f:
                cached_hash = f.read().strip()
        if current_hash is not None and cached_hash == current_hash:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        if not _check_ogsql():
            warnings.warn(
                f"AST cache for {sql_file} does not match the fixture content and "
                f"ogsql is unavailable to reparse; using the stale cache. "
                f"Set OGSQL_BIN to refresh.",
                stacklevel=2,
            )
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    if not _check_ogsql():
        pytest.skip(
            f"ogsql binary not available and AST not cached for {sql_file}. "
            f"Set OGSQL_BIN env var or place ogsql on PATH."
        )

    if sql_full is None:
        pytest.skip(f"SQL fixture not found: {sql_file}")

    ast = _run_ogsql_parse(sql_full)

    os.makedirs(AST_CACHE_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(ast, f, indent=2)
    with open(hash_path, "w", encoding="utf-8") as f:
        f.write(current_hash or "")

    return ast


# ── Session-Scoped AST Fixtures ─────────────────────────────────


@pytest.fixture(scope="session")
def cached_ast():
    """{filename: AST dict} mapping for all fixtures in FIXTURES_DIR."""
    cache = {}
    if os.path.isdir(FIXTURES_DIR):
        for f in sorted(os.listdir(FIXTURES_DIR)):
            if f.endswith(".sql"):
                cache[f] = _get_cached_ast(f)
    return cache


@pytest.fixture(scope="session")
def cached_ast_by_pkg():
    """{pkg_name: AST dict} mapping.
    pkg_name is derived from filename by stripping 'pkg_' prefix.
    e.g. "pkg_order.sql" → "order"
    """
    cache = {}
    if os.path.isdir(FIXTURES_DIR):
        for f in sorted(os.listdir(FIXTURES_DIR)):
            if f.endswith(".sql"):
                name = os.path.splitext(f)[0]
                if name.startswith("pkg_") or name.startswith("PKG_"):
                    name = name[4:]
                cache[name] = _get_cached_ast(f)
    return cache


# ── Fixture Discovery Helpers ───────────────────────────────────


def _fixture_sql_files() -> list:
    """Return sorted list of .sql fixture filenames (excluding known-broken)."""
    if not os.path.isdir(FIXTURES_DIR):
        return []
    _excluded = KNOWN_BROKEN_FIXTURES | MULTI_FILE_FIXTURES
    return sorted(f for f in os.listdir(FIXTURES_DIR) if f.endswith(".sql") and f not in _excluded)


def _fixture_pkg_name(sql_file: str) -> str:
    """Derive package name from SQL filename.
    e.g. "pkg_order.sql" → "order", "PKG_WARPDRIVER_STRESS_TEST.sql" → "WARPDRIVER_STRESS_TEST"
    """
    name = os.path.splitext(sql_file)[0]
    if name.startswith("pkg_") or name.startswith("PKG_"):
        return name[4:]
    return name


# ── Expected Baselines (per-fixture) ────────────────────────────
# These are floor values, not exact — tests check "≥ expected".

EXPECTED_BASELINES = {
    "pkg_order.sql": {"min_procs": 5, "min_procs_with_dml": 3},
    "pkg_dynamic_xml.sql": {"min_procs": 2, "min_procs_with_dml": 1},
    "complex_clearing_pkg.sql": {"min_procs": 3, "min_procs_with_dml": 2},
    "gauss_complete_examples.sql": {"min_procs": 4, "min_procs_with_dml": 2},
    "PKG_WARPDRIVER_STRESS_TEST.sql": {"min_procs": 5, "min_procs_with_dml": 1},
    # Issue regression fixtures (#34–#41)
    "issue_34_35_dto_naming.sql": {"min_procs": 3, "min_procs_with_dml": 2},
    "issue_38_map_put.sql": {"min_procs": 3, "min_procs_with_dml": 0},
    "issue_39_thread_safety.sql": {"min_procs": 3, "min_procs_with_dml": 0},
    "issue_40_string_compare.sql": {"min_procs": 3, "min_procs_with_dml": 0},
    "issue_41_type_system.sql": {"min_procs": 3, "min_procs_with_dml": 2},
    # Issue regression fixtures (#44–#49)
    "issue_44_if_elsif_goto.sql": {"min_procs": 5, "min_procs_with_dml": 3},
    "issue_45_exception_handling.sql": {"min_procs": 3, "min_procs_with_dml": 3},
    "issue_46_chr_ascii_substr.sql": {"min_procs": 5, "min_procs_with_dml": 0},
    "issue_47_long_parse_string.sql": {"min_procs": 3, "min_procs_with_dml": 0},
    "issue_48_long_compareto_string.sql": {"min_procs": 4, "min_procs_with_dml": 1},
    "issue_49_varchar2_concat.sql": {"min_procs": 3, "min_procs_with_dml": 0},
    # Issue regression fixtures (#54)
    "issue_54_nested_exception.sql": {"min_procs": 2, "min_procs_with_dml": 1},
    # Issue regression fixtures (#56)
    "pkg_issue56_return_handler.sql": {"min_procs": 3, "min_procs_with_dml": 3},
}

# complex_clearing_pkg.sql crashes ogsql v0.8.32's Python engine
# (AttributeError in _expr_to_java with None procedure context).
# Skip until upstream converter bug is fixed.
KNOWN_BROKEN_FIXTURES = {
    "complex_clearing_pkg.sql",
}

# Not broken: the single-file harness derives one package per filename, but #70
# is about several files collapsing into ONE package. Covered instead by the
# CLI-level multi-file regression in test_issues.py.
MULTI_FILE_FIXTURES = {
    "issue_70_fnc_a.sql",
    "issue_70_fnc_b.sql",
    "issue_70_casefold_upper.sql",
    "issue_70_casefold_lower.sql",
    "issue_71_parity_callee.sql",
    "issue_71_parity_caller.sql",
}
