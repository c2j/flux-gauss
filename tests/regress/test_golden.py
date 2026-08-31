"""Layer 2: Golden file regression tests.

Re-runs the full conversion pipeline for each fixture SQL and compares
generated output byte-for-byte against committed golden files.

Golden files are generated with --regress-save, manually reviewed, then
committed. Subsequent runs compare against them. Intentional changes
are accepted with --regress-update.

report.json is saved for reference but NOT compared (it contains timestamps).
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

import converter.flux_gauss as fg
from tests.regress.conftest import (
    GOLDEN_DIR,
    _fixture_pkg_name,
    _fixture_sql_files,
)

ENGINE = "py"
FOUR_FILE_TYPES = ["Service.java", "Mapper.java", "Mapper.xml", "ServiceTest.java"]


def _normalize_output(content: str) -> str:
    stripped = "\n".join(line.rstrip() for line in content.splitlines())
    collapsed = re.sub(r"\n{3,}", "\n\n", stripped)
    return collapsed


def _output_checksum(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _generate_for_package(sql_file: str, cached_ast_by_pkg: dict, tmp_path: str) -> dict:
    """Run full conversion pipeline for one fixture, return {file_type: content}."""
    pkg_name = _fixture_pkg_name(sql_file)
    ast = cached_ast_by_pkg[pkg_name]
    procs, _, _ = fg.extract_procedures(ast, sql_file)
    assert len(procs) > 0, f"No procedures in {sql_file}"
    pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
    all_pkgs = {pkg_name: pkg}

    for proc in procs:
        fg.analyze_procedure(proc, all_pkgs)

    out_dir = str(Path(tmp_path) / "dest")
    fg.generate_project(out_dir, packages=[pkg], config={})

    class_name = fg.package_to_classname(pkg_name)
    base = Path(out_dir)

    files = {}
    pkg_base = fg._pkg_base_dir(pkg)
    java_pkg = fg._pkg_java_package(pkg)

    for file_type in FOUR_FILE_TYPES:
        if file_type == "Mapper.xml":
            filepath = base / "src" / "main" / "resources" / "mapper" / f"{class_name}{file_type}"
        elif file_type == "ServiceTest.java":
            filepath = (
                base / "src" / "test" / "java" / java_pkg.replace(".", "/") / "service" / f"{class_name}{file_type}"
            )
        elif file_type.startswith("Mapper"):
            filepath = base / pkg_base / "mapper" / f"{class_name}{file_type}"
        else:
            filepath = base / pkg_base / "service" / f"{class_name}{file_type}"

        if filepath.exists():
            files[file_type] = filepath.read_text(encoding="utf-8")

    # Also save report.json for reference (NOT compared)
    report_path = base / "report.json"
    if not report_path.exists():
        report = fg.build_conversion_report(
            output_dir=out_dir,
            packages=[pkg],
            all_skipped=[],
            parse_errors_map={},
            config_path="regress",
        )
        import dataclasses

        report_path.write_text(
            json.dumps(dataclasses.asdict(report), indent=2, default=str),
            encoding="utf-8",
        )

    return files


def _write_golden(pkg_name: str, engine: str, file_type: str, content: str):
    pkg_dir = Path(GOLDEN_DIR) / engine / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_output(content)
    (pkg_dir / f"{file_type}.golden").write_text(normalized, encoding="utf-8")


def _read_golden(pkg_name: str, engine: str, file_type: str) -> str:
    path = Path(GOLDEN_DIR) / engine / pkg_name / f"{file_type}.golden"
    if not path.exists():
        raise FileNotFoundError(f"Golden file not found: {path}")
    return path.read_text(encoding="utf-8")


def _collect_golden_packages(engine: str) -> list:
    engine_dir = Path(GOLDEN_DIR) / engine
    if not engine_dir.is_dir():
        return []
    return sorted(d.name for d in engine_dir.iterdir() if d.is_dir() and not d.name.startswith("."))


def _all_golden_files_present(pkg_name: str, engine: str) -> bool:
    pkg_dir = Path(GOLDEN_DIR) / engine / pkg_name
    return all((pkg_dir / f"{ft}.golden").exists() for ft in FOUR_FILE_TYPES)


class TestGoldenSave:
    """Generate golden files from current output."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_save_golden_files(self, sql_file, cached_ast_by_pkg, tmp_path, regress_save):
        if not regress_save:
            pytest.skip("Use --regress-save to generate golden files")

        pkg_name = _fixture_pkg_name(sql_file)
        output = _generate_for_package(sql_file, cached_ast_by_pkg, str(tmp_path))

        for file_type in FOUR_FILE_TYPES:
            assert file_type in output, f"Missing {file_type} in generated output for {sql_file}"
            _write_golden(pkg_name, ENGINE, file_type, output[file_type])

        # Save build manifest
        manifest = {
            "engine": "python",
            "fixture": sql_file,
            "package_name": pkg_name,
            "files": {ft: _output_checksum(output[ft]) for ft in FOUR_FILE_TYPES if ft in output},
        }
        manifest_path = Path(GOLDEN_DIR) / ENGINE / pkg_name / "build_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_update_golden_files(self, sql_file, cached_ast_by_pkg, tmp_path, regress_update):
        if not regress_update:
            pytest.skip("Use --regress-update to overwrite golden files")

        pkg_name = _fixture_pkg_name(sql_file)
        output = _generate_for_package(sql_file, cached_ast_by_pkg, str(tmp_path))

        for file_type in FOUR_FILE_TYPES:
            assert file_type in output, f"Missing {file_type} in generated output for {sql_file}"
            _write_golden(pkg_name, ENGINE, file_type, output[file_type])


class TestGoldenCompare:
    """Compare generated output against committed golden files."""

    @pytest.mark.parametrize("pkg_name", _collect_golden_packages(ENGINE))
    @pytest.mark.parametrize("file_type", FOUR_FILE_TYPES)
    def test_matches_golden(self, pkg_name, file_type, cached_ast_by_pkg, tmp_path):
        if not _all_golden_files_present(pkg_name, ENGINE):
            pytest.skip(f"Golden files incomplete for {pkg_name}")

        # Find the fixture SQL for this package
        sql_file = None
        for f in _fixture_sql_files():
            if _fixture_pkg_name(f) == pkg_name:
                sql_file = f
                break
        assert sql_file is not None, f"No fixture found for package {pkg_name}"

        output = _generate_for_package(sql_file, cached_ast_by_pkg, str(tmp_path))
        assert file_type in output, f"Missing {file_type} in generated output"

        actual = _normalize_output(output[file_type])
        expected = _read_golden(pkg_name, ENGINE, file_type)

        assert actual == expected, (
            f"{pkg_name}/{file_type} differs from golden.\n"
            f"Run with --regress-update to accept changes, "
            f"or --regress-save to regenerate baseline."
        )


class TestGoldenStructure:
    """Verify golden directory has correct structure after save."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_all_file_types_present_after_save(self, sql_file, cached_ast_by_pkg, tmp_path, regress_save):
        if not regress_save:
            pytest.skip("Use --regress-save to verify")

        pkg_name = _fixture_pkg_name(sql_file)
        output = _generate_for_package(sql_file, cached_ast_by_pkg, str(tmp_path))

        for file_type in FOUR_FILE_TYPES:
            _write_golden(pkg_name, ENGINE, file_type, output[file_type])

        assert _all_golden_files_present(pkg_name, ENGINE), f"Not all golden files written for {pkg_name}"

        manifest_path = Path(GOLDEN_DIR) / ENGINE / pkg_name / "build_manifest.json"
        assert manifest_path.exists(), f"Missing manifest for {pkg_name}"


class TestGoldenNormalization:
    """Verify output normalization is stable."""

    def test_strips_trailing_whitespace(self):
        result = _normalize_output("line1   \nline2\t\n  line3")
        assert "   " not in result.split("\n")[0]

    def test_collapses_multiple_blank_lines(self):
        result = _normalize_output("a\n\n\n\nb")
        assert "\n\n\n\n" not in result
        assert result.count("\n\n") == 1

    def test_preserves_single_blank_line(self):
        result = _normalize_output("a\n\nb")
        assert "\n\n" in result

    def test_normalization_is_idempotent(self):
        content = "line1  \n\n\nline2\t\n\nline3"
        first = _normalize_output(content)
        second = _normalize_output(first)
        assert first == second


class TestGoldenIntegrity:
    """Meta-tests that golden directory is healthy — prevents silent-pass risks."""

    def test_every_fixture_has_golden_package(self):
        missing = [f for f in _fixture_sql_files() if _fixture_pkg_name(f) not in _collect_golden_packages(ENGINE)]
        assert not missing, f"Fixtures without golden files: {missing}. Run: pytest tests/regress/ --regress-save"

    def test_every_golden_package_has_all_file_types(self):
        for pkg_name in _collect_golden_packages(ENGINE):
            assert _all_golden_files_present(pkg_name, ENGINE), (
                f"Golden package {pkg_name} is incomplete. Run: pytest tests/regress/ --regress-save"
            )

    def test_golden_directory_exists(self):
        from pathlib import Path

        assert Path(GOLDEN_DIR).joinpath(ENGINE).is_dir(), (
            f"Golden directory missing: {GOLDEN_DIR}/{ENGINE}. Run: pytest tests/regress/ --regress-save"
        )
