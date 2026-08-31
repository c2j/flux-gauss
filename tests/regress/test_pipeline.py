"""Layer 1: Pipeline integrity regression tests.

For each fixture SQL file, run the full conversion pipeline and verify
structural invariants — pipeline completion, procedure counts, DML presence,
output file generation. Does NOT compare output content (that's Layer 2).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import converter.flux_gauss as fg

# conftest.py helpers are accessed as fixtures or via the module
# (pytest conftest.py is auto-loaded, not directly importable)
from tests.regress.conftest import (
    EXPECTED_BASELINES,
    FIXTURES_DIR,
    _fixture_pkg_name,
    _fixture_sql_files,
)


class TestParseSqlFile:
    """Step 1: SQL file → ogsql binary → raw AST dict."""

    def test_fixtures_available(self):
        files = _fixture_sql_files()
        assert len(files) > 0, (
            f"No SQL fixtures found in {FIXTURES_DIR}. Copy files from demo-project/sql/ to tests/regress/fixtures/"
        )

    def test_returns_valid_ast_structure(self):
        fake_ast = {"statements": [], "errors": [], "comments": []}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(fake_ast)

        with patch("converter.flux_gauss.subprocess.run", return_value=mock_result):
            with patch("converter.flux_gauss._read_sql_file", return_value=("SELECT 1;", "utf-8")):
                result = fg.parse_sql_file("fake.sql")

        assert "statements" in result
        assert "errors" in result
        assert "comments" in result

    def test_handles_parse_errors_gracefully(self):
        fake_ast = {
            "statements": [],
            "errors": [{"UnexpectedToken": {"got": ";"}}],
            "comments": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(fake_ast)

        with patch("converter.flux_gauss.subprocess.run", return_value=mock_result):
            with patch("converter.flux_gauss._read_sql_file", return_value=("BAD SQL;", "utf-8")):
                result = fg.parse_sql_file("fake.sql")

        assert len(result.get("errors", [])) > 0

    def test_subprocess_called_with_correct_flags(self):
        fake_ast = {"statements": [], "errors": [], "comments": []}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(fake_ast)

        with patch("converter.flux_gauss.subprocess.run", return_value=mock_result) as mock_run:
            with patch("converter.flux_gauss._read_sql_file", return_value=("SELECT 1;", "utf-8")):
                fg.parse_sql_file("fake.sql")

        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "parse" in cmd_args
        assert "-j" in cmd_args


class TestExtractProcedures:
    """Step 2: raw AST → extract_procedures → ProcedureInfo list."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_extracts_expected_minimum_procedures(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, pkg_vars, custom_types = fg.extract_procedures(ast, sql_file)

        exp = EXPECTED_BASELINES.get(sql_file, {})
        min_procs = exp.get("min_procs", 0)
        assert len(procs) >= min_procs, f"{sql_file}: expected ≥{min_procs} procedures, got {len(procs)}"

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_each_procedure_has_required_fields(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)

        for proc in procs:
            assert proc.name, f"Empty name in {sql_file}"
            # package may be empty for standalone functions not in a named package
            assert proc.proc_name, f"Empty proc_name in {sql_file}/{proc.name}"
            assert proc.body is not None, f"Null body in {sql_file}/{proc.name}"
            assert proc.source_file, f"Empty source_file in {sql_file}/{proc.name}"

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_procedures_have_consistent_naming(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)

        for proc in procs:
            assert proc.package in proc.name, (
                f"{sql_file}/{proc.name}: name '{proc.name}' does not contain package '{proc.package}'"
            )

    def test_empty_ast_returns_empty(self):
        procs, pkg_vars, types = fg.extract_procedures({"statements": []}, "")
        assert procs == []
        assert pkg_vars == {}

    def test_non_procedure_statements_return_empty(self):
        ast = {"statements": [{"CreateTable": {"name": ["orders"]}}]}
        procs, _, _ = fg.extract_procedures(ast, "test.sql")
        assert procs == []


class TestAnalyzeProcedures:
    """Step 3: ProcedureInfo → analyze_procedure → java_logic_lines + DML."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_produces_java_logic_lines(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        assert len(procs) > 0, f"No procedures in {sql_file}"

        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}

        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        procs_with_logic = [p for p in procs if p.java_logic_lines]
        assert len(procs_with_logic) > 0, f"{sql_file}: no procedures produced java_logic_lines"

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_detects_dml_statements(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)

        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}

        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        exp = EXPECTED_BASELINES.get(sql_file, {})
        min_dml = exp.get("min_procs_with_dml", 0)
        procs_with_dml = [p for p in procs if p.dml_statements]
        assert len(procs_with_dml) >= min_dml, (
            f"{sql_file}: expected ≥{min_dml} procedures with DML, got {len(procs_with_dml)}"
        )

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_java_logic_lines_are_strings(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)

        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}

        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)
            for line in proc.java_logic_lines:
                assert isinstance(line, str), f"{sql_file}/{proc.proc_name}: non-string line: {line!r}"


class TestGenerateProject:
    """Step 4: generate_project → output files written."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_writes_all_expected_files(self, sql_file, cached_ast, tmp_path):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        assert len(procs) > 0, f"No procedures extracted from {sql_file}"

        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}

        # Must analyze before generating
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        out_dir = str(tmp_path / "dest")
        fg.generate_project(out_dir, packages=[pkg], config={})

        class_name = fg.package_to_classname(pkg_name)
        base = Path(out_dir)

        # Match the converter's own path logic via _pkg_base_dir
        pkg_base = fg._pkg_base_dir(pkg)
        service_path = base / pkg_base / "service" / f"{class_name}Service.java"
        mapper_path = base / pkg_base / "mapper" / f"{class_name}Mapper.java"
        xml_path = base / "src" / "main" / "resources" / "mapper" / f"{class_name}Mapper.xml"
        test_path = (
            base
            / "src"
            / "test"
            / "java"
            / fg._pkg_java_package(pkg).replace(".", "/")
            / "service"
            / f"{class_name}ServiceTest.java"
        )

        assert service_path.exists(), f"Missing: {service_path}"
        assert mapper_path.exists(), f"Missing: {mapper_path}"
        assert xml_path.exists(), f"Missing: {xml_path}"
        assert test_path.exists(), f"Missing: {test_path}"

        # Verify files are non-empty
        assert len(service_path.read_text()) > 100, f"Service file too small: {service_path}"
        assert len(mapper_path.read_text()) > 50, f"Mapper file too small: {mapper_path}"
        assert len(xml_path.read_text()) > 50, f"XML file too small: {xml_path}"
        assert len(test_path.read_text()) > 100, f"Test file too small: {test_path}"

    def test_pom_xml_written_once(self, cached_ast, tmp_path):
        sql_file = _fixture_sql_files()[0]
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        out_dir = str(tmp_path / "dest")
        fg.generate_project(out_dir, packages=[pkg], config={})

        pom_path = Path(out_dir) / "pom.xml"
        assert pom_path.exists()
        assert "fluxgauss" in pom_path.read_text().lower() or "spring" in pom_path.read_text().lower()

    def test_business_exception_written(self, cached_ast, tmp_path):
        sql_file = _fixture_sql_files()[0]
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        out_dir = str(tmp_path / "dest")
        fg.generate_project(out_dir, packages=[pkg], config={})

        be_path = Path(out_dir) / fg.BASE_DIR / "exception" / "BusinessException.java"
        assert be_path.exists()


class TestConversionReport:
    """Verify ConversionReport is built correctly."""

    @pytest.mark.parametrize("sql_file", _fixture_sql_files())
    def test_creates_report_with_correct_counts(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        pkg_name = _fixture_pkg_name(sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)

        report = fg.build_conversion_report(
            output_dir="./dest",
            packages=[pkg],
            all_skipped=[],
            parse_errors_map={},
            config_path="fluxgauss.yaml",
        )
        assert report.total_packages == 1
        assert report.total_procedures == len(procs)
        assert len(report.procedure_mappings) == len(procs)
        assert isinstance(report.generated_at, str)
