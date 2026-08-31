"""
Shared fixtures for flux_gauss unit tests.

Key concern: flux_gauss.py has extensive module-level mutable state
(UNRESOLVED_CALLS, STUB_PROCEDURES, etc.) that MUST be reset between tests.
"""

import sys

import pytest

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"flux-gauss requires Python >= 3.10 (converter/flux_gauss.py uses PEP 604 "
        f"`int | None` syntax, which raises TypeError on 3.9). Detected {sys.version.split()[0]}. "
        f"Create a venv with a 3.10+ interpreter, e.g.: "
        f"python3.14 -m venv .venv && .venv/bin/pip install pytest pyyaml"
    )

import converter.flux_gauss as fg

# ── Global State Reset ──────────────────────────────────────────
# flux_gauss.py uses many module-level mutable containers.
# Every test must start with clean state.


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset ALL module-level mutable state before each test."""
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
    fg._TABLE_CONSTRAINTS.clear()
    fg._ENUM_TYPES.clear()

    # TYPE_OVERRIDES is a config dict — save/restore to avoid test cross-contamination
    original_overrides = dict(fg.TYPE_OVERRIDES)
    yield
    fg.TYPE_OVERRIDES.clear()
    fg.TYPE_OVERRIDES.update(original_overrides)


# ── Mock Object Factories ──────────────────────────────────────


@pytest.fixture
def make_parameter():
    """Factory to create Parameter instances with sensible defaults."""

    def _make(name="p_test", java_type="String", sql_type="varchar", mode="IN"):
        return fg.Parameter(name=name, java_type=java_type, sql_type=sql_type, mode=mode)

    return _make


@pytest.fixture
def make_procedure():
    """Factory to create ProcedureInfo instances with minimal valid state."""

    def _make(
        name="pkg_test.proc_a",
        package="pkg_test",
        proc_name="proc_a",
        is_function=False,
        return_type=None,
        parameters=None,
        body=None,
        sql_text="BEGIN NULL; END;",
        **overrides,
    ):
        proc = fg.ProcedureInfo(
            name=name,
            package=package,
            proc_name=proc_name,
            is_function=is_function,
            return_type=return_type,
            parameters=parameters or [],
            body=body or {"Block": {"body": {"statements": []}}},
            sql_text=sql_text,
        )
        for k, v in overrides.items():
            setattr(proc, k, v)
        return proc

    return _make


@pytest.fixture
def make_package():
    """Factory to create PackageInfo instances."""

    def _make(package_name="pkg_test", procedures=None, **overrides):
        pkg = fg.PackageInfo(
            package_name=package_name,
            procedures=procedures or [],
        )
        for k, v in overrides.items():
            setattr(pkg, k, v)
        return pkg

    return _make


@pytest.fixture
def make_comment():
    """Factory to create CommentInfo instances."""

    def _make(text="-- test comment", line=1, end_line=1, column=0, comment_type="line"):
        return fg.CommentInfo(text=text, line=line, end_line=end_line, column=column, comment_type=comment_type)

    return _make
