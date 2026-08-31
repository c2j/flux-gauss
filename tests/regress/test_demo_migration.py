"""Layer 0: End-to-end demo migration guard.

Runs the REAL demo-project yaml configs through the full pipeline:
  convert → mvn compile → mvn test → semantic health checks

This is the authoritative guard for: "demo migration produces a working,
compilable, testable Java project with no silent failures."

Slow (~4 min both engines). Select with: pytest -m demo_migration
Deselect with: pytest -m "not demo_migration"
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ── Config ───────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_CONFIGS = [
    # (engine, yaml_path relative to repo root, dest_dir relative to repo root)
    ("py", "demo-project/fluxgauss_py.yaml", "dest_py"),
    ("ru", "demo-project/fluxgauss_ru.yaml", "dest_ru"),
]
REQUIRES_OGSQL = {"py"}

# Patterns that indicate a stub caused by a converter EXCEPTION (always a bug).
# These must never appear in generated output. Contrast with legitimate stubs
# ("complex PL/pgSQL pattern requires manual implementation") which are by-design.
EXCEPTION_STUB_PATTERNS = [
    "转换分析阶段异常",  # analysis-phase exception
    "AttributeError",
    "TypeError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
    "RecursionError",
    "OverflowError",
]

# Thresholds
STUB_RATIO_HARD_FAIL = 0.25  # >25% of Services with any stub → fail
STUB_RATIO_WARN = 0.10  # >10% → warn (printed, not failed)
DML_FLOOR = {
    "py": 50,  # Python after _tracker fix produces ~630; floor catches catastrophic regression
    "ru": 100,  # Rust produces ~617
}

# Timeout for each subprocess (seconds)
CONVERT_TIMEOUT = 300
MVN_TIMEOUT = 600


# ── Binary detection ─────────────────────────────────────────────


def _find_ogsql() -> str | None:
    """Resolve ogsql binary path, or None if unavailable."""
    p = os.environ.get("OGSQL_BIN")
    if p and os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    # Try PATH
    found = shutil.which("ogsql")
    return found


def _find_rust_binary() -> str | None:
    """Resolve fluxgauss release binary, or None."""
    p = REPO_ROOT / "target" / "release" / "fluxgauss"
    if p.is_file() and os.access(p, os.X_OK):
        return str(p)
    return None


# ── Pipeline runner ──────────────────────────────────────────────


def _run_cmd(cmd: list[str], cwd: str, timeout: int, env: dict | None = None) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", str(e)


def _parse_claimed_packages(stdout: str) -> int | None:
    """Extract 'Packages: N' from converter stdout."""
    m = re.search(r"Packages:\s+(\d+)", stdout)
    return int(m.group(1)) if m else None


def _parse_compile_errors(output: str) -> list[str]:
    """Extract [ERROR] lines referencing .java files from mvn output."""
    errors = []
    for line in output.splitlines():
        if line.startswith("[ERROR]") and ".java" in line:
            # Shorten to file:line:message
            cleaned = re.sub(r"^.*?/src/", "src/", line.strip())
            errors.append(cleaned[:200])
    return errors


def _parse_test_summary(output: str) -> dict:
    """Parse mvn test output for final summary."""
    # Look for "Tests run: X, Failures: Y, Errors: Z, Skipped: W"
    # Take the LAST match (aggregate line)
    matches = re.findall(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)(?:,\s*Skipped:\s*(\d+))?",
        output,
    )
    if matches:
        t, f, e, s = matches[-1]
        return {"tests": int(t), "failures": int(f), "errors": int(e), "skipped": int(s or 0)}
    return {"tests": 0, "failures": -1, "errors": -1, "skipped": 0}


def _count_service_files(dest_dir: Path) -> int:
    """Count *Service.java files under src/main/java."""
    pattern = str(dest_dir / "src" / "main" / "java" / "**" / "*Service.java")
    # Use pathlib glob recursively
    return sum(1 for _ in dest_dir.rglob("*Service.java") if "src/main/java" in str(_))


def _count_exception_stubs(dest_dir: Path) -> tuple[int, int]:
    """Count Service files containing exception-stub patterns.
    Returns (exception_stub_count, total_stub_count).
    """
    svc_files = list(dest_dir.rglob("*Service.java"))
    exception_count = 0
    total_stub_count = 0
    for f in svc_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Auto-generated stub" in content:
            total_stub_count += 1
        if any(pat in content for pat in EXCEPTION_STUB_PATTERNS):
            exception_count += 1
    return exception_count, total_stub_count


def _count_mapper_methods(dest_dir: Path) -> int:
    """Count mapper methods via XML tags in Mapper.xml files.
    This is more reliable than parsing Java signatures.
    """
    xml_dir = dest_dir / "src" / "main" / "resources" / "mapper"
    if not xml_dir.is_dir():
        return 0
    count = 0
    for xml_file in xml_dir.glob("*Mapper.xml"):
        try:
            content = xml_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Count DML/SELECT tags
        count += len(re.findall(r"<(?:select|insert|update|delete)\b", content))
    return count


def _run_engine_pipeline(engine: str, yaml_path: str, dest_dir: str) -> dict:
    """Run full pipeline for one engine. Returns result dict."""
    result: dict = {"engine": engine, "yaml": yaml_path, "dest_dir": dest_dir}
    dest = REPO_ROOT / dest_dir

    # --- L0.0: Config sources exist (checked separately in test) ---

    # --- Clean dest ---
    if dest.exists():
        shutil.rmtree(dest)

    # --- L0.1: Run converter ---
    if engine == "py":
        ogsql = _find_ogsql()
        if not ogsql:
            result["skipped"] = "ogsql not available (set OGSQL_BIN or install on PATH)"
            return result
        cmd = [sys.executable, "converter/flux_gauss.py", "-c", yaml_path]
        env = {"OGSQL_BIN": ogsql}
    else:  # ru
        binary = _find_rust_binary()
        if not binary:
            result["skipped"] = "fluxgauss binary not built (run: cargo build --release --bin fluxgauss)"
            return result
        cmd = [binary, "--config", yaml_path]
        env = {}

    exit_code, stdout, stderr = _run_cmd(cmd, str(REPO_ROOT), CONVERT_TIMEOUT, env)
    result["convert_exit"] = exit_code
    result["convert_stdout"] = stdout[-3000:]  # keep tail for diagnostics
    result["convert_stderr"] = stderr[-2000:]
    result["claimed_packages"] = _parse_claimed_packages(stdout)
    result["actual_services"] = _count_service_files(dest) if dest.exists() else 0

    if exit_code != 0:
        return result  # downstream is meaningless

    # --- L0.2: mvn compile ---
    compile_exit, compile_out, _ = _run_cmd(
        ["mvn", "compile", "-batch-mode", "-q"],
        str(dest),
        MVN_TIMEOUT,
    )
    result["compile_exit"] = compile_exit
    result["compile_errors"] = _parse_compile_errors(compile_out + (result.get("convert_stderr", "")))

    # --- L0.3: mvn test ---
    test_exit, test_out, _ = _run_cmd(
        ["mvn", "test", "-batch-mode"],
        str(dest),
        MVN_TIMEOUT,
    )
    result["test_exit"] = test_exit
    result["test_summary"] = _parse_test_summary(test_out)

    # --- L0.4: Semantic health — stub scanning ---
    exc_stubs, total_stubs = _count_exception_stubs(dest)
    result["exception_stub_count"] = exc_stubs
    result["total_stub_count"] = total_stubs
    result["service_count"] = result["actual_services"]
    result["stub_ratio"] = (total_stubs / result["service_count"]) if result["service_count"] > 0 else 1.0

    # --- L0.5: DML extraction sanity ---
    result["mapper_method_count"] = _count_mapper_methods(dest)

    return result


# ── Session fixture ──────────────────────────────────────────────


@pytest.fixture(scope="module")
def demo_results():
    """Run full demo migration for all configs once. Returns {engine: ResultBundle}."""
    results = {}
    for engine, yaml_path, dest_dir in DEMO_CONFIGS:
        results[engine] = _run_engine_pipeline(engine, yaml_path, dest_dir)
    return results


# ── L0.0: Config integrity ──────────────────────────────────────


@pytest.mark.demo_migration
class TestL00ConfigIntegrity:
    """Every yaml source entry must exist on disk."""

    @pytest.mark.parametrize("engine,yaml_path", [(e, y) for e, y, _ in DEMO_CONFIGS])
    def test_sources_exist(self, engine, yaml_path):
        import yaml  # pyyaml

        yaml_full = REPO_ROOT / yaml_path
        with open(yaml_full, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        sources = cfg.get("sources", [])
        assert len(sources) > 0, f"{yaml_path}: no sources listed"
        missing = [s for s in sources if not (REPO_ROOT / s).exists()]
        assert not missing, (
            f"{yaml_path} references {len(missing)} missing SQL files: {missing}. "
            f"Either add the files to demo-project/sql/ or remove them from yaml."
        )


# ── L0.1: Conversion runs, no silent skip ───────────────────────


@pytest.mark.demo_migration
class TestL01Conversion:
    """Converter must exit 0 and produce expected package count with no silent skips."""

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_convert_exit_zero(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        assert r["convert_exit"] == 0, (
            f"{engine}: converter exited {r['convert_exit']}\nstderr: {r.get('convert_stderr', '')[:500]}"
        )

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_no_silent_skip(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        claimed = r["claimed_packages"]
        actual = r["actual_services"]
        assert claimed is not None, f"{engine}: could not parse 'Packages: N' from stdout"
        # Some SQL files contain only DDL/types (no procedures → no Service.java).
        # Require ≥60% of claimed packages to produce Service files — catches
        # massive silent skips while tolerating DDL-only packages.
        assert actual >= claimed * 0.6, (
            f"{engine}: converter claims {claimed} packages but only {actual} Service.java files exist "
            f"({actual / claimed:.0%}). Large discrepancy suggests silent failures."
        )
        assert actual > 0, f"{engine}: no Service.java files generated at all"


# ── L0.2: Compile ───────────────────────────────────────────────


@pytest.mark.demo_migration
class TestL02Compile:
    """mvn compile must pass with zero errors."""

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_compile_passes(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        errors = r.get("compile_errors", [])
        assert r["compile_exit"] == 0 and len(errors) == 0, (
            f"{engine}: {len(errors)} compile errors in {len(set(e.split(':')[0] for e in errors))} files.\n"
            f"First 10 errors:\n" + "\n".join(errors[:10])
        )


# ── L0.3: Unit tests ────────────────────────────────────────────


@pytest.mark.demo_migration
class TestL03UnitTest:
    """mvn test must pass with zero failures and zero errors."""

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_unit_tests_pass(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        ts = r["test_summary"]
        assert ts["failures"] == 0 and ts["errors"] == 0, (
            f"{engine}: mvn test has {ts['failures']} failures, {ts['errors']} errors "
            f"(out of {ts['tests']} tests).\n"
            f"Run: cd {r['dest_dir']} && mvn test  for details"
        )


# ── L0.4: Semantic health — stub scanning ───────────────────────


@pytest.mark.demo_migration
class TestL04SemanticHealth:
    """Generated code must not contain exception-caused stubs (converter bugs).
    Total stub ratio must stay below threshold."""

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_no_exception_stubs(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        count = r.get("exception_stub_count", 0)
        assert count == 0, (
            f"{engine}: {count} Service files contain exception-caused stubs "
            f"(patterns: {EXCEPTION_STUB_PATTERNS}). "
            f"These are converter bugs — analysis crashed during procedure processing."
        )

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_stub_ratio_below_threshold(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        ratio = r.get("stub_ratio", 1.0)
        if ratio > STUB_RATIO_WARN:
            print(
                f"  WARN: {engine} stub ratio is {ratio:.1%} "
                f"({r['total_stub_count']}/{r['service_count']} Services have stubs)"
            )
        assert ratio <= STUB_RATIO_HARD_FAIL, (
            f"{engine}: stub ratio {ratio:.1%} exceeds {STUB_RATIO_HARD_FAIL:.0%} threshold "
            f"({r['total_stub_count']}/{r['service_count']} Services). "
            f"Engine may have widespread analysis failures."
        )


# ── L0.5: DML extraction sanity ─────────────────────────────────


@pytest.mark.demo_migration
class TestL05DmlExtraction:
    """Mapper must contain a reasonable number of DML methods.
    A floor catches catastrophic DML extraction failures (like _tracker bug)."""

    @pytest.mark.parametrize("engine", ["py", "ru"])
    def test_mapper_has_methods(self, engine, demo_results):
        r = demo_results[engine]
        if "skipped" in r:
            pytest.skip(r["skipped"])
        count = r.get("mapper_method_count", 0)
        floor = DML_FLOOR[engine]
        assert count >= floor, (
            f"{engine}: only {count} mapper methods generated (floor: {floor}). DML extraction may be silently broken."
        )
