"""Layer 0b: fastaas/exam/ogagila dataset migration guard.

Runs the fastaas/exam/ogagila yaml configs (sources re-pointed to the
lib/fastaas + lib/ogagila submodules) through the full pipeline:
  convert → mvn compile → mvn test → semantic health checks

This is the reproducible counterpart of test_demo_migration for the three
external datasets that previously lived only at machine-local absolute paths.
ogagila SQL files contain psql \\set meta-directives that ogsql cannot parse,
so the harness strips them at runtime (mirrors docs/reports/...).

Slow. Select with: pytest -m fastaas_migration
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.regress.test_demo_migration import (
    _find_ogsql,
    _find_rust_binary,
    _run_cmd,
    _parse_test_summary,
    EXCEPTION_STUB_PATTERNS,
    STUB_RATIO_HARD_FAIL,
    MVN_TIMEOUT,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# (dataset, engine, yaml relative to repo root, dest relative to repo root)
DATASET_CONFIGS = [
    ("exam", "py", "demo-project/fluxgauss_exam.yaml", "dest_exam"),
    ("exam", "ru", "demo-project/fluxgauss_exam-ru.yaml", "dest_exam_ru"),
    ("fastaas", "py", "demo-project/fluxgauss_fastaas_py.yaml", "dest_fastaas_py"),
    ("fastaas", "ru", "demo-project/fluxgauss_fastaas_ru.yaml", "dest_fastaas_ru"),
    ("ogagila", "py", "demo-project/fluxgauss_ogagila_py.yaml", "dest_ogagila_py"),
    ("ogagila", "ru", "demo-project/fluxgauss_ogagila_ru.yaml", "dest_ogagila_ru"),
]

REQUIRES_OGSQL = {"py"}

# DML floor per dataset (mapper method count sanity). Measured from a green run.
DML_FLOOR = {
    "exam": {"py": 200, "ru": 200},
    "fastaas": {"py": 100, "ru": 100},
    "ogagila": {"py": 100, "ru": 100},
}

# ogagila sources live in lib/ogagila/sqls which still contains psql \set
# meta-directives (the old /tmp/ogagila_src was a sed-stripped copy). Strip them
# into a temp dir and rewrite the config's sources prefix before conversion.
# The yaml uses the repo-relative prefix; match both the relative and absolute
# forms so the replace is robust.
OGAGILA_SRC_PREFIX_REL = str(Path("lib") / "ogagila" / "sqls")
OGAGILA_SRC_PREFIX = str(REPO_ROOT / "lib" / "ogagila" / "sqls")


def _prepare_ogagila(tmp: Path) -> Path:
    clean = tmp / "ogagila_clean"
    if clean.exists():
        shutil.rmtree(clean)
    shutil.copytree(OGAGILA_SRC_PREFIX, clean)
    for sql in clean.rglob("*.sql"):
        text = sql.read_bytes()
        # Drop lines starting with `\set ` (psql meta-command). Keep everything else.
        stripped = b"\n".join(
            ln for ln in text.split(b"\n") if not ln.lstrip().startswith(b"\\set ")
        )
        sql.write_bytes(stripped)
    return clean


def _run_dataset_pipeline(dataset: str, engine: str, yaml_path: str, dest_dir: str) -> dict:
    """Convert + mvn compile + mvn test for one dataset/engine. Returns result dict."""
    result = {"dataset": dataset, "engine": engine, "yaml": yaml_path, "dest_dir": dest_dir}
    cfg_path = REPO_ROOT / yaml_path

    # ogagila: strip \set into a temp dir, rewrite config sources prefix.
    tmp_cfg = cfg_path
    tmp = Path(tempfile.mkdtemp(prefix=f"fg_{dataset}_{engine}_"))
    try:
        if dataset == "ogagila":
            clean = _prepare_ogagila(tmp)
            text = cfg_path.read_text(encoding="utf-8").replace(OGAGILA_SRC_PREFIX, str(clean)).replace(
                OGAGILA_SRC_PREFIX_REL, str(clean)
            )
            tmp_cfg = tmp / "config.yaml"
            tmp_cfg.write_text(text, encoding="utf-8")

        out_dir = tmp / "out"
        return _convert_and_check(dataset, engine, tmp_cfg, tmp, out_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _convert_and_check(dataset: str, engine: str, tmp_cfg: Path, tmp: Path, out_dir: Path) -> dict:
    result = {"dataset": dataset, "engine": engine}
    if engine == "py":
        ogsql = _find_ogsql()
        if not ogsql:
            result["skipped"] = "ogsql binary not found (OGSQL_BIN or PATH)"
            return result
        env = dict(**{k: v for k, v in __import__("os").environ.items()}, OGSQL_BIN=ogsql)
        code, out, err = _run_cmd(
            [sys.executable, "converter/flux_gauss.py", "-c", str(tmp_cfg), "-o", str(out_dir)],
            cwd=str(REPO_ROOT),
            timeout=MVN_TIMEOUT,
            env=env,
        )
    else:
        binary = _find_rust_binary()
        if not binary:
            result["skipped"] = "fluxgauss release binary not built (cargo build --release --bin fluxgauss)"
            return result
        cfg_rewritten = tmp / "cfg_ru.yaml"
        # Replace any output_dir form (`./dest_exam-ru`, `dest_exam_ru`, …) with the
        # harness out dir. A plain string replace fails when dest_dir != the yaml's
        # literal output_dir (e.g. "dest_exam_ru" vs "./dest_exam-ru").
        cfg_text = tmp_cfg.read_text(encoding="utf-8")
        cfg_text = re.sub(r"^output_dir:\s*\S+.*$", f"output_dir: {out_dir}", cfg_text, flags=re.M)
        cfg_rewritten.write_text(cfg_text, encoding="utf-8")
        code, out, err = _run_cmd([binary, "--config", str(cfg_rewritten)], cwd=str(REPO_ROOT), timeout=MVN_TIMEOUT)
    result["convert_exit"] = code
    if code != 0:
        result["convert_error"] = err[-2000:]
        return result

    # mvn compile
    code, out, err = _run_cmd(["mvn", "-q", "compile", "-batch-mode"], cwd=str(out_dir), timeout=MVN_TIMEOUT)
    result["compile_exit"] = code
    errors = re.findall(r"\.java:\[\d+,\d+\]", out + err)
    result["compile_errors"] = sorted(set(errors))

    # mvn test
    code, out, err = _run_cmd(["mvn", "test", "-batch-mode"], cwd=str(out_dir), timeout=MVN_TIMEOUT)
    result["test_exit"] = code
    result["test_summary"] = _parse_test_summary(out + err)

    # semantic: exception stubs must not appear
    stub_count = 0
    for pattern in EXCEPTION_STUB_PATTERNS:
        stub_count += sum(f.read_text(errors="ignore").count(pattern) for f in out_dir.rglob("*.java"))
    result["exception_stub_count"] = stub_count

    # DML sanity: count mapper methods
    mapper_files = list((out_dir / "src" / "main" / "java").rglob("*Mapper.java"))
    mapper_methods = 0
    for mf in mapper_files:
        mapper_methods += len(re.findall(r"^\s+(?:[A-Za-z_][\w.]*)\s+[a-zA-Z_]\w*\s*\(", mf.read_text(errors="ignore"), re.M))
    result["mapper_method_count"] = mapper_methods
    return result


@pytest.fixture(scope="session")
def dataset_results():
    results = {}
    for dataset, engine, yaml_path, dest_dir in DATASET_CONFIGS:
        key = f"{dataset}/{engine}"
        results[key] = _run_dataset_pipeline(dataset, engine, yaml_path, dest_dir)
    return results


@pytest.mark.fastaas_migration
@pytest.mark.parametrize("dataset,engine", [(d, e) for d, e, _, _ in DATASET_CONFIGS])
def test_dataset_convert_ok(dataset, engine, dataset_results):
    r = dataset_results[f"{dataset}/{engine}"]
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r.get("convert_exit") == 0, f"{dataset}/{engine} convert failed: {r.get('convert_error', '')[:2000]}"


@pytest.mark.fastaas_migration
@pytest.mark.parametrize("dataset,engine", [(d, e) for d, e, _, _ in DATASET_CONFIGS])
def test_dataset_compile_ok(dataset, engine, dataset_results):
    r = dataset_results[f"{dataset}/{engine}"]
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r.get("compile_exit") == 0 and not r.get("compile_errors"), (
        f"{dataset}/{engine} mvn compile has {len(r.get('compile_errors', []))} unique errors: "
        f"{r.get('compile_errors', [])[:5]}"
    )


@pytest.mark.fastaas_migration
@pytest.mark.parametrize("dataset,engine", [(d, e) for d, e, _, _ in DATASET_CONFIGS])
def test_dataset_unit_tests_pass(dataset, engine, dataset_results):
    r = dataset_results[f"{dataset}/{engine}"]
    if "skipped" in r:
        pytest.skip(r["skipped"])
    ts = r.get("test_summary", {})
    assert ts.get("failures", 1) == 0 and ts.get("errors", 1) == 0, (
        f"{dataset}/{engine} mvn test: {ts.get('failures')} failures, {ts.get('errors')} errors"
    )


@pytest.mark.fastaas_migration
@pytest.mark.parametrize("dataset,engine", [(d, e) for d, e, _, _ in DATASET_CONFIGS])
def test_dataset_no_exception_stubs(dataset, engine, dataset_results):
    r = dataset_results[f"{dataset}/{engine}"]
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r.get("exception_stub_count", 1) == 0, f"{dataset}/{engine} has exception-stub artifacts"
