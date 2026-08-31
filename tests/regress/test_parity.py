"""Cross-engine parity guard.

Per-engine golden files cannot detect Python/Rust divergence — they lock in
whatever each engine already does. Issue #71 is the motivating example: Rust
reported 0 cross-package calls where Python reported 171, and every per-engine
golden stayed green throughout.

This module runs BOTH engines over the same fixture and compares engine-independent
facts derived from the generated tree (never raw text, which legitimately differs
in formatting).

Run: pytest tests/regress/test_parity.py -m parity
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import converter.flux_gauss as fg
from tests.regress.conftest import FIXTURES_DIR

pytestmark = pytest.mark.parity

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_BIN = REPO_ROOT / "target" / "release" / "fluxgauss"
BASE_PACKAGE = "com.example.demo"

# Single-package fixtures exercising the areas #70/#71/#72 touched. Files holding
# MORE THAN ONE package go in MULTI_PACKAGE_FIXTURES instead: Python collapses every
# package in one file into a single PackageInfo (pkg_name = procedures[0].package)
# while Rust emits one per package. That is a real pre-existing divergence, tracked
# by test_multi_package_single_file_divergence rather than silently skipped.
PARITY_FIXTURES = [
    "issue_72_string_to_number.sql",
    "issue_72b_math_string_args.sql",
    "issue_63_varchar2_return.sql",
    "issue_60_instr_case_when.sql",
]

MULTI_PACKAGE_FIXTURES = [
    "gauss_complete_examples.sql",
    # two packages (PKG_LOG + PKG_BIZ) in one file, so Python emits LogService only
    # while Rust also emits BizService — which is why the Python golden for this
    # fixture cannot show the resolved cross-package call that #71 fixed.
    "issue_71_cross_pkg_schema.sql",
]

# One package PER FILE, so both engines emit both Services. This is the only shape
# that can actually exercise cross-package call parity: two packages in a single
# file hit the Python collapse above, which masks the #71 defect.
CROSS_PACKAGE_MULTI_FILE = [
    "issue_71_parity_callee.sql",
    "issue_71_parity_caller.sql",
]


def _service_dir(out_dir: Path) -> Path:
    return out_dir / "src" / "main" / "java" / Path(*BASE_PACKAGE.split(".")) / "service"


def _require_tools():
    if not RUST_BIN.exists():
        pytest.skip(f"Rust binary not built: {RUST_BIN}. Run: cargo build --release -p fluxgauss")
    if not fg.OGSQL_BIN or not os.path.exists(fg.OGSQL_BIN):
        pytest.skip("ogsql binary not available; set OGSQL_BIN")


def _run_python(sql_files, out_dir: Path):
    cmd = [
        sys.executable,
        str(Path(fg.__file__).resolve()),
        "-o",
        str(out_dir),
        "-s",
        *[str(p) for p in sql_files],
        "--full",
        "--skip-validate",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "OGSQL_BIN": fg.OGSQL_BIN},
    )


def _run_rust(sql_files, out_dir: Path, tmp_path: Path):
    # The Rust CLI's -o/-s mode has a known defect (see issue #70), so drive it
    # through a generated config file instead.
    config = tmp_path / "parity.yaml"
    sources = "\n".join(f"  - {p}" for p in sql_files)
    config.write_text(
        f"output_dir: {out_dir}\nbase_package: {BASE_PACKAGE}\nsources:\n{sources}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [str(RUST_BIN), "-c", str(config), "--full"],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "OGSQL_BIN": fg.OGSQL_BIN},
        cwd=str(REPO_ROOT),
    )


def _facts(out_dir: Path) -> dict:
    """Engine-independent facts about a generated project."""
    svc_dir = _service_dir(out_dir)
    services = sorted(p.name[: -len("Service.java")] for p in svc_dir.glob("*Service.java")) if svc_dir.is_dir() else []
    unresolved = 0
    resolved = 0
    for path in svc_dir.glob("*Service.java") if svc_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        unresolved += len(re.findall(r"^\s*//\s*CALL\s", text, re.MULTILINE))
        own = path.name[: -len(".java")]
        for match in re.finditer(r"\b(\w+Service)\.\w+\s*\(", text):
            if match.group(1) != own:
                resolved += 1
    return {"services": services, "unresolved": unresolved, "resolved": resolved}


def _both_engines(sql_files, tmp_path):
    _require_tools()
    py_out = Path(tmp_path) / "py"
    ru_out = Path(tmp_path) / "ru"
    py = _run_python(sql_files, py_out)
    assert py.returncode == 0, f"python engine failed:\n{py.stdout}\n{py.stderr}"
    ru = _run_rust(sql_files, ru_out, Path(tmp_path))
    assert ru.returncode == 0, f"rust engine failed:\n{ru.stdout}\n{ru.stderr}"
    return _facts(py_out), _facts(ru_out)


@pytest.mark.parametrize("fixture", PARITY_FIXTURES)
def test_service_class_sets_match(fixture, tmp_path):
    """Both engines must derive the same set of generated Service classes.

    This is the invariant #70 (package merging) and #71 (schema-qualified package
    registration) both operate on.
    """
    sql = Path(FIXTURES_DIR) / fixture
    if not sql.is_file():
        pytest.skip(f"fixture missing: {fixture}")
    py, ru = _both_engines([sql], tmp_path)
    assert py["services"] == ru["services"], (
        f"Service class sets diverge for {fixture}\n  python: {py['services']}\n  rust:   {ru['services']}"
    )


@pytest.mark.parametrize("fixture", PARITY_FIXTURES)
def test_cross_call_resolution_matches(fixture, tmp_path):
    """Resolved vs unresolved cross-service call counts must match.

    Guards the #71 class of defect, where one engine emitted `// CALL ...`
    comments while the other injected and invoked the collaborating service.
    """
    sql = Path(FIXTURES_DIR) / fixture
    if not sql.is_file():
        pytest.skip(f"fixture missing: {fixture}")
    py, ru = _both_engines([sql], tmp_path)
    assert (py["unresolved"], py["resolved"]) == (ru["unresolved"], ru["resolved"]), (
        f"Cross-call resolution diverges for {fixture}\n"
        f"  python: unresolved={py['unresolved']} resolved={py['resolved']}\n"
        f"  rust:   unresolved={ru['unresolved']} resolved={ru['resolved']}"
    )


def test_cross_package_call_resolution_parity_multi_file(tmp_path):
    """The case that would have caught #71: one package per file, one calling the other.

    Rust reported 0 cross-calls / 205 unresolved on the real corpus while Python
    reported 171, and no per-engine golden noticed. Both engines must now resolve
    the call — zero `// CALL` comments and at least one real service invocation.
    """
    sources = [Path(FIXTURES_DIR) / f for f in CROSS_PACKAGE_MULTI_FILE]
    for sql in sources:
        if not sql.is_file():
            pytest.skip(f"fixture missing: {sql.name}")
    py, ru = _both_engines(sources, tmp_path)

    assert py["services"] == ru["services"], (
        f"Service class sets diverge\n  python: {py['services']}\n  rust:   {ru['services']}"
    )
    assert (py["unresolved"], py["resolved"]) == (ru["unresolved"], ru["resolved"]), (
        f"Cross-call resolution diverges\n"
        f"  python: unresolved={py['unresolved']} resolved={py['resolved']}\n"
        f"  rust:   unresolved={ru['unresolved']} resolved={ru['resolved']}"
    )
    assert ru["unresolved"] == 0, f"rust left unresolved cross-calls: {ru}"
    assert ru["resolved"] >= 1, f"rust resolved no cross-service call: {ru}"


@pytest.mark.parametrize("fixture", MULTI_PACKAGE_FIXTURES)
@pytest.mark.xfail(
    strict=True,
    reason="Known divergence: Python collapses all packages in one file into a "
    "single PackageInfo (pkg_name = procedures[0].package), so a multi-package "
    "file yields one Service; Rust yields one per package. Not addressed by #70.",
)
def test_multi_package_single_file_divergence(fixture, tmp_path):
    """Documents the multi-package-per-file divergence so it flips when fixed."""
    sql = Path(FIXTURES_DIR) / fixture
    if not sql.is_file():
        pytest.skip(f"fixture missing: {fixture}")
    py, ru = _both_engines([sql], tmp_path)
    assert py["services"] == ru["services"], f"python: {py['services']}\nrust: {ru['services']}"
