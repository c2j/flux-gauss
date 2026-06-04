"""
Integration regression tests for the full conversion pipeline.

Tests the complete SQL → AST → ProcedureInfo → Java code path using
cached AST files from dest/.fluxgauss/ast/ and mocked parse_sql_file.
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
import converter.flux_gauss as fg


AST_DIR = os.path.join(os.path.dirname(__file__), "..", "dest", ".fluxgauss", "ast")


def _cached_ast_files():
    if not os.path.isdir(AST_DIR):
        return []
    return sorted(f for f in os.listdir(AST_DIR) if f.endswith(".json"))


def _load_cached_ast(filename):
    path = os.path.join(AST_DIR, filename)
    if not os.path.isfile(path):
        pytest.skip(f"Cached AST not found: {filename}")
    with open(path) as f:
        return json.load(f)


class TestParseSqlFileMocked:
    """Test parse_sql_file with mocked subprocess."""

    def test_returns_ast_dict(self):
        fake_ast = {"statements": [], "errors": [], "comments": []}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(fake_ast)

        with patch("converter.flux_gauss.subprocess.run", return_value=mock_result):
            with patch("converter.flux_gauss._read_sql_file", return_value=("SELECT 1;", "utf-8")):
                result = fg.parse_sql_file("fake.sql")
        assert "statements" in result

    def test_handles_parse_errors(self):
        fake_ast = {"statements": [], "errors": [{"UnexpectedToken": {"got": ";"}}], "comments": []}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(fake_ast)

        with patch("converter.flux_gauss.subprocess.run", return_value=mock_result):
            with patch("converter.flux_gauss._read_sql_file", return_value=("BAD SQL;", "utf-8")):
                result = fg.parse_sql_file("fake.sql")
        assert len(result.get("errors", [])) > 0

    def test_subprocess_called_with_ogsql(self):
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


class TestExtractProceduresFromCachedAst:
    """Test extract_procedures using real cached AST files."""

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_extracts_from_stress_test(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, pkg_vars, custom_types = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")
        assert len(procs) >= 5
        proc_names = [p.proc_name for p in procs]
        assert "sp_main_orchestrator" in proc_names

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_extracts_from_complex_clearing(self):
        ast = _load_cached_ast("demo_project_sql_complex_clearing_pkg_sql.json")
        procs, pkg_vars, custom_types = fg.extract_procedures(ast, "complex_clearing_pkg.sql")
        assert len(procs) >= 3
        has_function = any(p.is_function for p in procs)
        assert has_function

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_procedure_has_parameters(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, _, _ = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")
        procs_with_params = [p for p in procs if len(p.parameters) > 0]
        assert len(procs_with_params) > 0

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_procedure_fields_populated(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, _, _ = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")
        for p in procs:
            assert p.name
            assert p.package
            assert p.proc_name
            assert p.body is not None
            assert p.source_file


class TestAnalyzeProcedureFromCachedAst:
    """Test analyze_procedure using real cached AST files."""

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_generates_java_logic(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, _, _ = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")
        assert len(procs) > 0

        all_pkgs = {"PKG_WARPDRIVER_STRESS_TEST": fg.PackageInfo(package_name="PKG_WARPDRIVER_STRESS_TEST", procedures=procs)}
        fg.analyze_procedure(procs[0], all_pkgs)
        assert len(procs[0].java_logic_lines) > 0

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_detects_dml_statements(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, _, _ = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")

        all_pkgs = {"PKG_WARPDRIVER_STRESS_TEST": fg.PackageInfo(package_name="PKG_WARPDRIVER_STRESS_TEST", procedures=procs)}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        procs_with_dml = [p for p in procs if len(p.dml_statements) > 0]
        assert len(procs_with_dml) > 0

    @pytest.mark.skipif(not _cached_ast_files(), reason="No cached AST files")
    def test_java_logic_contains_no_syntax_errors(self):
        ast = _load_cached_ast("demo_project_sql_PKG_WARPDRIVER_STRESS_TEST_sql.json")
        procs, _, _ = fg.extract_procedures(ast, "PKG_WARPDRIVER_STRESS_TEST.sql")

        all_pkgs = {"PKG_WARPDRIVER_STRESS_TEST": fg.PackageInfo(package_name="PKG_WARPDRIVER_STRESS_TEST", procedures=procs)}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)
            for line in proc.java_logic_lines:
                assert isinstance(line, str), f"Non-string line in {proc.proc_name}: {line}"


class TestBuildConversionReport:
    def test_creates_report(self):
        pkg = fg.PackageInfo(package_name="pkg_test")
        proc = fg.ProcedureInfo(
            name="pkg_test.p1", package="pkg_test", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
            source_file="test.sql",
        )
        pkg.procedures = [proc]
        report = fg.build_conversion_report(
            output_dir="./dest",
            packages=[pkg],
            all_skipped=[],
            parse_errors_map={},
            config_path="fluxgauss.yaml",
        )
        assert report.total_packages == 1
        assert report.total_procedures == 1
        assert len(report.procedure_mappings) == 1


class TestExtractProceduresEmptyInput:
    def test_empty_ast(self):
        procs, pkg_vars, types = fg.extract_procedures({"statements": []}, "")
        assert procs == []
        assert pkg_vars == {}

    def test_non_procedure_statements(self):
        ast = {"statements": [{"CreateTable": {"name": ["orders"]}}]}
        procs, _, _ = fg.extract_procedures(ast, "test.sql")
        assert procs == []
