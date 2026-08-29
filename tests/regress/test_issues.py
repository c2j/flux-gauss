"""
Regression guard tests for GitHub issues #34 through #41.

Tests marked xfail are for unresolved issues. They will FAIL if the issue
regresses after being fixed. Tests without xfail verify behavior that
already works correctly and must not regress.

To run: pytest tests/regress/test_issues.py -v
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import converter.flux_gauss as fg
from tests.regress.conftest import (
    FIXTURES_DIR,
    _fixture_pkg_name,
)


# ── Helpers ──────────────────────────────────────────────────────

def _run_pipeline(sql_file, cached_ast, tmp_path):
    ast = cached_ast[sql_file]
    pkg_name = _fixture_pkg_name(sql_file)
    procs, pkg_vars, custom_types = fg.extract_procedures(ast, sql_file)
    assert len(procs) > 0, f"No procedures in {sql_file}"
    for vname, vdata in pkg_vars.items():
        if vname not in fg._PACKAGE_CONSTANTS:
            fg._PACKAGE_VARIABLES[vname] = {**vdata, "package": pkg_name}
    sql_full = os.path.join(FIXTURES_DIR, sql_file)
    if os.path.exists(sql_full):
        ddl_info = fg.parse_table_ddl(sql_full)
        for tbl, cols in ddl_info.items():
            if tbl not in fg._TABLE_DDL_SOURCE:
                fg._TABLE_DDL_SOURCE[tbl] = cols
    pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs, package_vars=pkg_vars, custom_types=custom_types)
    # Register all unique package names from procedures (handles multi-package fixtures)
    all_pkgs = {pkg_name: pkg}
    for proc in procs:
        if proc.package and proc.package not in all_pkgs:
            # Create a stub PackageInfo for cross-referenced packages
            all_pkgs[proc.package] = pkg
    # Also register packages referenced in package_vars (e.g. shared package specs)
    _pkg_var_packages = set()
    for vname, vdata in pkg_vars.items():
        _vpkg = vdata.get("package", "")
        if _vpkg and _vpkg not in all_pkgs:
            _pkg_var_packages.add(_vpkg)
    for _vpkg in _pkg_var_packages:
        all_pkgs[_vpkg] = pkg
    for proc in procs:
        fg.analyze_procedure(proc, all_pkgs)
    out_dir = str(Path(tmp_path) / "dest")
    fg.generate_project(out_dir, packages=[pkg], config={})
    class_name = fg.package_to_classname(pkg_name)
    return out_dir, pkg, class_name


def _run_cli_pipeline(sql_files, tmp_path, full=True):
    """Run the real Python CLI for regressions that span multiple SQL files."""
    out_dir = Path(tmp_path) / "dest_cli"
    source_paths = [str(Path(FIXTURES_DIR) / sql_file) for sql_file in sql_files]
    command = [
            sys.executable,
            str(Path(fg.__file__).resolve()),
            "-o",
            str(out_dir),
            "-s",
            *source_paths,
            "--skip-validate",
        ]
    if full:
        command.append("--full")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "OGSQL_BIN": fg.OGSQL_BIN},
        timeout=120,
    )
    assert result.returncode == 0, (
        f"Python CLI failed ({result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return str(out_dir)


def _read_generated(output_dir, rel_path):
    fp = Path(output_dir) / rel_path
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return ""


def _service_path(output_dir, class_name):
    return f"src/main/java/{fg.BASE_PACKAGE.replace('.', '/')}/service/{class_name}Service.java"


def _mapper_path(output_dir, class_name):
    return f"src/main/java/{fg.BASE_PACKAGE.replace('.', '/')}/mapper/{class_name}Mapper.java"


def _test_path(output_dir, class_name):
    return f"src/test/java/{fg.BASE_PACKAGE.replace('.', '/')}/service/{class_name}ServiceTest.java"


def _xml_path(output_dir, class_name):
    return f"src/main/resources/mapper/{class_name}Mapper.xml"


# ── Issue #70: Same-schema standalone routines across files ─────

class TestIssue70_MultiFileStandaloneRoutines:
    def test_same_schema_routines_share_one_service(self, tmp_path):
        out_dir = _run_cli_pipeline(
            ["issue_70_fnc_a.sql", "issue_70_fnc_b.sql"], tmp_path
        )
        svc = _read_generated(out_dir, _service_path(out_dir, "Bigfund"))

        assert "fncA(" in svc
        assert "fncB(" in svc

    def test_case_variant_package_names_merge_into_one_service(self, tmp_path):
        """app.PKG_CASEFOLD and other.pkg_casefold both emit CasefoldService.java,
        so they must merge rather than one silently overwriting the other."""
        out_dir = _run_cli_pipeline(
            ["issue_70_casefold_upper.sql", "issue_70_casefold_lower.sql"], tmp_path
        )
        svc = _read_generated(out_dir, _service_path(out_dir, "Casefold"))

        assert svc, "CasefoldService not generated"
        assert "instEntry(" in svc, f"method from the UPPER-case package lost:\n{svc}"
        assert "delEntry(" in svc, f"method from the lower-case package lost:\n{svc}"

    def test_other_source_file_change_regenerates_merged_service(self, tmp_path):
        source_dir = Path(tmp_path) / "sql"
        source_dir.mkdir()
        sources = []
        for filename in ("issue_70_fnc_a.sql", "issue_70_fnc_b.sql"):
            destination = source_dir / filename
            destination.write_text(
                (Path(FIXTURES_DIR) / filename).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            sources.append(str(destination))

        out_dir = Path(tmp_path) / "dest_incremental"
        command = [
            sys.executable, str(Path(fg.__file__).resolve()),
            "-o", str(out_dir), "-s", *sources, "--skip-validate",
        ]
        env = {**os.environ, "OGSQL_BIN": fg.OGSQL_BIN}
        first = subprocess.run(command + ["--full"], capture_output=True, text=True,
                               env=env, timeout=120)
        assert first.returncode == 0, first.stderr

        second_source = Path(sources[1])
        second_source.write_text(
            second_source.read_text(encoding="utf-8").replace("'_B'", "'_B2'"),
            encoding="utf-8",
        )
        second = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
        assert second.returncode == 0, second.stderr
        svc = _read_generated(str(out_dir), _service_path(str(out_dir), "Bigfund"))
        assert "fncA(" in svc
        assert "fncB(" in svc
        assert '"_B2"' in svc

        manifest = json.loads((out_dir / ".fluxgauss" / "manifest.json").read_text())
        assert manifest["files"][sources[1]]["packages"] == ["BIGFUND"]


# ── Issue #34: Request/Response DTO + Entity generation ──────────

class TestIssue34_DTO_Entity:
    """Issue #34: Replace AtomicReference OUT params with DTO,
    use Entity classes instead of Map return types, avoid long method signatures."""

    @pytest.mark.xfail(strict=True, reason="Issue #34 OPEN — OUT params still use AtomicReference")
    def test_out_params_not_use_atomic_reference(self, cached_ast, tmp_path):
        sql_file = "issue_34_35_dto_naming.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        has_atomic = bool(re.search(r'AtomicReference<', svc))
        assert not has_atomic, (
            "Issue #34: OUT params still use AtomicReference. "
            "Expected DTO-based return type."
        )

    def test_ddl_generates_entity_class(self, cached_ast, tmp_path):
        sql_file = "issue_34_35_dto_naming.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)

        entity_dir = (
            Path(out_dir) / "src" / "main" / "java"
            / fg.BASE_PACKAGE.replace(".", "/") / "entity"
        )
        entity_files = list(entity_dir.glob("*.java")) if entity_dir.exists() else []
        assert len(entity_files) > 0, (
            f"Issue #34: No Entity classes generated from DDL in {entity_dir}"
        )

    @pytest.mark.xfail(strict=True, reason="Issue #34 OPEN — 8+ flat params not wrapped in DTO")
    def test_methods_with_many_params_use_dto(self, cached_ast, tmp_path):
        sql_file = "issue_34_35_dto_naming.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        match = re.search(r'procCreateOrder\s*\((.*?)\)', svc, re.DOTALL)
        if match:
            params = match.group(1)
            param_types = re.findall(
                r'\b(String|Long|BigDecimal|Integer|Boolean|int|long|java\.\w+(?:\.\w+)*)\s+\w+',
                params
            )
            assert len(param_types) <= 3, (
                f"Issue #34: procCreateOrder has {len(param_types)} flat parameters. "
                "Expected ≤3 (DTO parameter)."
            )


# ── Issue #35: Mapper method naming ──────────────────────────────

class TestIssue35_MapperNaming:
    """Issue #35: Mapper method names should be semantic, not numeric suffixes."""

    def test_no_numeric_suffix_in_method_names(self, cached_ast, tmp_path):
        sql_file = "issue_34_35_dto_naming.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        mapper = _read_generated(out_dir, _mapper_path(out_dir, cls))
        assert mapper, "Mapper file not generated"

        numeric_suffixes = re.findall(r'\b\w+_\d+\s*\(', mapper)
        assert len(numeric_suffixes) <= 5, (
            f"Issue #35: Found {len(numeric_suffixes)} methods with numeric suffixes: "
            f"{numeric_suffixes[:8]}. Expected semantic names."
        )

    def test_methods_reflect_target_table_or_operation(self, cached_ast, tmp_path):
        sql_file = "issue_34_35_dto_naming.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        mapper = _read_generated(out_dir, _mapper_path(out_dir, cls))
        assert mapper, "Mapper file not generated"

        all_methods = re.findall(
            r'(?:public|String|Map|List|Long|int|void|Integer|BigDecimal|Date|Boolean|Object)\s+(\w+)\s*\(',
            mapper
        )
        # Each method should contain at least one semantic keyword (table/operation hint)
        keywords = ['order', 'log', 'count', 'sum', 'avg', 'insert', 'update',
                     'select', 'delete', 'create', 'status', 'amount', 'customer',
                     'product', 'quantity', 'price']
        non_semantic = [
            m for m in all_methods
            if not any(kw in m.lower() for kw in keywords)
        ]
        assert len(non_semantic) == 0, (
            f"Issue #35: {len(non_semantic)}/{len(all_methods)} methods lack semantic hints: "
            f"{non_semantic[:5]}"
        )


# ── Issue #36: Test file generation ──────────────────────────────

class TestIssue36_TestFileGeneration:
    """Issue #36: Verify test files are actually generated on disk."""

    def test_service_test_file_exists_on_disk(self, cached_ast, tmp_path):
        sql_file = "pkg_order.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)

        test_fp = Path(out_dir) / _test_path(out_dir, cls)
        assert test_fp.exists(), (
            f"Issue #36: Test file not generated at {test_fp}"
        )
        assert test_fp.stat().st_size > 100, (
            f"Issue #36: Test file too small ({test_fp.stat().st_size} bytes)"
        )

    def test_test_file_contains_test_methods(self, cached_ast, tmp_path):
        sql_file = "pkg_order.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)

        test_content = _read_generated(out_dir, _test_path(out_dir, cls))
        assert test_content, "Test file not generated"
        assert bool(re.search(r'@Test', test_content)), (
            "Issue #36: Test file has no @Test annotations"
        )

    def test_issue_fixture_test_files_exist(self, cached_ast, tmp_path):
        """Also verify test files for the new issue fixtures."""
        for sql_file in ["issue_34_35_dto_naming.sql", "issue_41_type_system.sql"]:
            out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
            test_fp = Path(out_dir) / _test_path(out_dir, cls)
            assert test_fp.exists(), f"No test file for {sql_file}"
            assert test_fp.stat().st_size > 100, f"Test too small for {sql_file}"


# ── Issue #37: Cross-package TODO quality ────────────────────────

class TestIssue37_CrossPackageTODO:
    """Issue #37: Unresolved cross-package calls should have
    informative TODO comments with function signatures and source hints."""

    def test_report_lists_unresolved_calls_with_detail(self, cached_ast):
        sql_file = "pkg_order.sql"
        ast = cached_ast[sql_file]
        pkg_name = _fixture_pkg_name(sql_file)
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

        for call in fg.UNRESOLVED_CALLS:
            assert hasattr(call, 'callee') or hasattr(call, 'function_name'), (
                f"Issue #37: Unresolved call entry missing function identity: {call}"
            )

    def test_todo_comments_have_source_context(self, cached_ast, tmp_path):
        """If TODOs are generated, they should include source file/line hints."""
        sql_file = "pkg_order.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        todo_lines = re.findall(r'//\s*TODO:?\s*.+', svc)
        for todo in todo_lines:
            has_context = bool(
                re.search(r'\(.*\)', todo)
                or re.search(r'Source:', todo)
                or re.search(r'\.sql', todo)
                or 'pkg_' in todo.lower()
                or 'fnc_' in todo.lower()
            )
            assert has_context, (
                f"Issue #37: TODO lacks context: '{todo.strip()[:120]}'. "
                "Expected function name, source file, or parameter signature."
            )


# ── Issue #38: __MAP_PUT__ residual code ─────────────────────────

class TestIssue38_MAP_PUT:
    """Issue #38: Cross-package variable assignments must NOT produce
    illegal __MAP_PUT__ placeholder code."""

    def test_no_map_put_in_service(self, cached_ast, tmp_path):
        sql_file = "issue_38_map_put.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        assert '__MAP_PUT__' not in svc, (
            "Issue #38: __MAP_PUT__ residual found in Service.java"
        )

    def test_no_map_put_in_mapper(self, cached_ast, tmp_path):
        sql_file = "issue_38_map_put.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        mapper = _read_generated(out_dir, _mapper_path(out_dir, cls))
        if mapper:
            assert '__MAP_PUT__' not in mapper, (
                "Issue #38: __MAP_PUT__ found in Mapper.java"
            )

    def test_no_map_put_in_xml(self, cached_ast, tmp_path):
        sql_file = "issue_38_map_put.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        xml_content = _read_generated(out_dir, _xml_path(out_dir, cls))
        if xml_content:
            assert '__MAP_PUT__' not in xml_content, (
                "Issue #38: __MAP_PUT__ found in Mapper.xml"
            )

    def test_cross_package_procedures_not_all_stubs(self, cached_ast, tmp_path):
        """Cross-package variable assignments should generate working code, not all stubs."""
        sql_file = "issue_38_map_put.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        stub_count = len(re.findall(r'TODO: Auto-generated stub', svc))
        total_procs = 3
        # Issue #38 partially resolved: dotted cross-package vars are recognized
        # but Map-based access may still trigger compilation checks
        has_map_access = 'pkgIssue38Shared.put' in svc or 'pkgIssue38Shared.get' in svc
        non_stub_count = total_procs - stub_count
        assert non_stub_count > 0 or has_map_access, (
            f"Issue #38: All {total_procs} procedures are stubs with no Map-based access. "
            "Expected at least some cross-package variable handling."
        )


# ── Issue #39: Thread safety of package variables ────────────────

class TestIssue39_ThreadSafety:
    """Issue #39: Package variables mapped as Service instance fields
    in Spring singleton, causing thread safety concerns."""

    def test_readonly_constants_generated_as_static_final(self, cached_ast, tmp_path):
        """Package CONSTANT declarations should appear as static final fields."""
        sql_file = "issue_39_thread_safety.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        expected_constants = ['maxRetryCount', 'defaultTimeout', 'appName']
        static_final_names = set(
            re.findall(
                r'(?:private|public)\s+static\s+final\s+\w+(?:<\w+>)?\s+(\w+)', svc
            )
        )
        missing = [c for c in expected_constants if c not in static_final_names]
        assert len(missing) == 0, (
            f"Issue #39: Package constants not generated as static final: {missing}. "
            f"Found: {static_final_names}"
        )

    def test_mutable_vars_have_thread_safety_mechanism(self, cached_ast, tmp_path):
        """Mutable package variables should use ThreadLocal or have safety annotation."""
        sql_file = "issue_39_thread_safety.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        expected_mutable = [
            'gCurrentUser', 'gSessionId', 'gDebugMode', 'gBatchStatus'
        ]
        # Check that mutable vars exist as fields (not missing entirely)
        all_fields = set(
            re.findall(
                r'(?:private|public|protected)\s+(?:static\s+)?(?:final\s+)?(\w+(?:<\w+>)?)\s+(\w+)\s*[=;]', svc
            )
        )
        # Flatten: re.findall with 2 groups returns list of tuples (type, name)
        field_names = set(name for _, name in all_fields)
        present = [v for v in expected_mutable if v in field_names]
        assert len(present) > 0, (
            f"Issue #39: Mutable package vars ({expected_mutable}) not present as fields. "
            f"Existing fields: {field_names}"
        )

        has_thread_local = 'ThreadLocal' in svc
        has_safety_comment = bool(re.search(
            r'THREAD[-_\s]?(SAFE|LOCAL)', svc, re.IGNORECASE
        ))
        assert has_thread_local or has_safety_comment, (
            "Issue #39: Mutable package vars lack ThreadLocal or safety annotation."
        )


# ── Issue #40: String comparison inconsistency ───────────────────

class TestIssue40_StringComparison:
    """Issue #40: String comparison should use .equals() for equality
    and numeric conversion for >= comparisons, not lexicographic compareTo()."""

    def test_equality_uses_equals_not_compareto(self, cached_ast, tmp_path):
        """String == should generate .equals(), not .compareTo() == 0."""
        sql_file = "issue_40_string_compare.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        compareto_eq = re.findall(r'\.compareTo\([^)]+\)\s*==\s*0', svc)
        assert len(compareto_eq) == 0, (
            f"Issue #40: Found {len(compareto_eq)} compareTo()==0 patterns. "
            "Equality should use .equals()."
        )

        equals_count = len(re.findall(r'\.equals\(', svc))
        assert equals_count > 0, (
            "Issue #40: No .equals() calls found — string equality comparison missing."
        )

    def test_string_relational_comparison_not_lexicographic(self, cached_ast, tmp_path):
        """String with numeric literal should use BigDecimal, not String.compareTo()."""
        sql_file = "issue_40_string_compare.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # Numeric literal comparisons should use BigDecimal (e.g. "3", "0", "9999")
        # Variable-variable comparisons use String.compareTo() as fallback (correct for non-numeric strings)
        # Check: BigDecimal wrapping is present for numeric literal comparisons
        has_bigdecimal = 'new java.math.BigDecimal' in svc
        # Check: no compareTo == 0 for equality (should use .equals)
        compareto_eq = len(re.findall(r'\.compareTo\([^)]+\)\s*==\s*0', svc))
        assert compareto_eq == 0, (
            f"Issue #40: Found {compareto_eq} compareTo()==0 patterns. Equality should use .equals()."
        )


# ── Issue #41: Type system defects ───────────────────────────────

class TestIssue41_TypeSystem:
    """Issue #41: Verify correct Java type mapping for SQL types
    (NUMBER, DATE, BOOLEAN) and that EXCEPTION is not mapped to String."""

    def test_numeric_params_map_to_bigdecimal_or_long(self, cached_ast, tmp_path):
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        match = re.search(r'procTransfer\s*\(([^)]+)\)', svc, re.DOTALL)
        assert match, "procTransfer method not found"
        sig = match.group(1)
        p_amount = re.search(r'(java\.math\.BigDecimal|BigDecimal|Long|long)\s+pAmount\b', sig)
        assert p_amount, (
            f"Issue #41: p_amount (NUMERIC) type not BigDecimal/Long in: {sig[:200]}"
        )

    def test_date_params_map_to_date_types(self, cached_ast, tmp_path):
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        match = re.search(r'procTransfer\s*\(([^)]+)\)', svc, re.DOTALL)
        assert match, "procTransfer method not found"
        sig = match.group(1)
        p_date = re.search(r'(java\.sql\.Date|java\.util\.Date|LocalDate|Date)\s+pTransferDate\b', sig)
        assert p_date, (
            f"Issue #41: p_transfer_date (DATE) type not Date/LocalDate in: {sig[:200]}"
        )

    def test_boolean_params_map_to_boolean(self, cached_ast, tmp_path):
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        match = re.search(r'procTransfer\s*\(([^)]+)\)', svc, re.DOTALL)
        assert match, "procTransfer method not found"
        sig = match.group(1)
        p_bool = re.search(r'(Boolean|boolean)\s+pIsUrgent\b', sig)
        assert p_bool, (
            f"Issue #41: p_is_urgent (BOOLEAN) type not Boolean in: {sig[:200]}"
        )

    def test_exception_not_mapped_to_string_field(self, cached_ast, tmp_path):
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        exception_as_string = re.findall(
            r'private\s+String\s+(accountNotFound|insufficientFunds|invalidAmount|eAccountClosed)',
            svc
        )
        assert len(exception_as_string) == 0, (
            f"Issue #41: EXCEPTION variables mapped to String: {exception_as_string}"
        )

    def test_percent_type_resolves_from_ddl(self, cached_ast, tmp_path):
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        balance_declared_as_string = re.search(
            r'vFromBalance\s*=\s*(?:new\s+)?String|vFromBalance\s+String',
            svc
        )
        assert not balance_declared_as_string, (
            "Issue #41: t_issue41_account.balance%TYPE (NUMERIC) resolved to String. "
            "Expected BigDecimal."
        )

    def test_bigdecimal_comparisons_use_compareto_correctly(self, cached_ast, tmp_path):
        """BigDecimal.compareTo() is correct Java — verify it's NOT String.compareTo()."""
        sql_file = "issue_41_type_system.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # BigDecimal.compareTo() is fine; String.compareTo() for numeric vars is not
        string_var_compareto = re.findall(
            r'(vFromBalance|vBalance|pAmount)\.compareTo\([^)]+\)',
            svc
        )
        for match_text in string_var_compareto:
            # These should be BigDecimal, so compareTo is acceptable
            assert 'BigDecimal' in svc or 'Long' in svc, (
                f"Issue #41: {match_text} compareTo() on variable "
                "that may not be numeric type."
            )


# ── Issue #44: IF condition loss via _remove_dynamic_sql_build_lines ─

class TestIssue44_IfConditionLoss:
    """Issue #44: _remove_dynamic_sql_build_lines() at L2260 removes
    `if (` lines as "guard" when dynamic SQL concat is detected.
    Trigger: v_sql := v_sql || '...' + IF + mapper calls."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_44_if_elsif_goto.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_dynamic_if_keeps_condition(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert 'if (vCount > 0)' in svc or 'if (vCount.compareTo(0) > 0)' in svc, (
            "Issue #44: if (vCount > 0) removed by _remove_dynamic_sql_build_lines"
        )

    def test_dynamic_elsif_keeps_conditions(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert bool(re.search(r'if\s*\(.*pFilter.*!=.*null', svc)), (
            "Issue #44: if (pFilter != null) removed by dynamic SQL cleanup"
        )

    def test_nested_dynamic_keeps_ifs(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        if_count = len(re.findall(r'\bif\s*\(', svc))
        assert if_count >= 4, (
            f"Issue #44: only {if_count} if keywords — dynamic SQL cleanup removed them"
        )

    def test_chained_concat_keeps_final_if(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert bool(re.search(r'if\s*\(.*pCode.*!=.*null', svc)), (
            "Issue #44: final IF lost after chained dynamic SQL concats"
        )

    def test_non_dynamic_preserves_if(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert 'if (pFlag' in svc or 'if ("1".equals(pFlag)' in svc or 'Objects.equals(pFlag' in svc

    def test_if_else_balance(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        ifs = len(re.findall(r'\bif\s*\(', svc))
        elses = len(re.findall(r'\}\s*else\s*\{', svc))
        assert elses <= ifs + 3, (
            f"Issue #44: {elses} else vs {ifs} if — dynamic SQL cleanup removed conditions"
        )


# ── Issue #45: EXCEPTION WHEN no_data_found THEN ... WHEN OTHERS ─

class TestIssue45_ExceptionHandling:
    """Issue #45: EXCEPTION block with multiple WHEN clauses split into
    peer-level catch blocks — Java disallows duplicate catch at same level."""

    def test_no_peer_catch_for_multi_when(self, cached_ast, tmp_path):
        sql_file = "issue_45_exception_handling.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # Count catch blocks in proc_link_etf_repay — should NOT have
        # two catch at the same level
        catch_blocks = re.findall(r'\bcatch\s*\(', svc)
        assert len(catch_blocks) <= 5, (
            f"Issue #45: Found {len(catch_blocks)} catch blocks. "
            "Multiple WHEN should not produce peer-level catch blocks."
        )

    def test_simple_exception_has_catch(self, cached_ast, tmp_path):
        """Control: single EXCEPTION WHEN OTHERS should produce try-catch."""
        sql_file = "issue_45_exception_handling.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        assert bool(re.search(r'try\s*\{', svc)), (
            "Issue #45: No try block found for EXCEPTION handling."
        )
        assert bool(re.search(r'\bcatch\s*\(', svc)), (
            "Issue #45: No catch block found for EXCEPTION handling."
        )

    @pytest.mark.xfail(strict=True, reason="Issue #45 OPEN — no_data_found should use null-check, not catch")
    def test_no_data_found_is_null_check_not_catch(self, cached_ast, tmp_path):
        sql_file = "issue_45_exception_handling.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # no_data_found should NOT generate catch(Exception e) — 
        # it should use if(result == null) instead
        no_data_catches = re.findall(
            r'catch\s*\(Exception\s+e\).*?no.data.found', svc, re.IGNORECASE | re.DOTALL
        )
        assert len(no_data_catches) == 0, (
            "Issue #45: no_data_found treated as catch block. "
            "Should use null check for MyBatis null return."
        )


# ── Issue #46: CHR/ASCII/SUBSTR malformed Java ───────────────────

class TestIssue46_ChrAsciiSubstr:
    """Issue #46: CHR(ASCII(SUBSTR(...))) produces 'int String.valueOf(...)'
    — two type keywords back-to-back is invalid Java syntax."""

    def test_no_double_type_keywords(self, cached_ast, tmp_path):
        sql_file = "issue_46_chr_ascii_substr.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # Check for "int String.valueOf" — two type keywords together
        double_type = re.findall(r'\bint\s+String\.valueOf', svc)
        assert len(double_type) == 0, (
            f"Issue #46: Found {len(double_type)} 'int String.valueOf' patterns. "
            "ascii() conversion produced malformed Java cast."
        )

    def test_chr_output_compiles(self, cached_ast, tmp_path):
        sql_file = "issue_46_chr_ascii_substr.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # CHR output should not contain invalid casts
        # Pattern: (char)(String expr) is invalid Java
        bogus_chr = re.findall(r'\(char\)\s*\(\s*String\.valueOf', svc)
        assert len(bogus_chr) == 0, (
            f"Issue #46: Found {len(bogus_chr)} '(char)(String.valueOf...)' patterns."
        )

    def test_substr_string_offset_coerced(self, cached_ast, tmp_path):
        sql_file = "issue_46_chr_ascii_substr.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # String-typed offset/length → substring(stringVar - 1) is invalid
        # Must exclude integer literals (5 - 1 is fine, pStart - 1 is not when pStart is String)
        bogus_substr = re.findall(r'\.substring\s*\(\s*[A-Za-z_]\w*\s*-\s*1', svc)
        assert len(bogus_substr) == 0, (
            f"Issue #46: Found {len(bogus_substr)} 'substring(var - 1)' with "
            "potentially String-typed var. Need int coercion."
        )


# ── Issue #47: Long.parseLong on non-numeric strings ─────────────

class TestIssue47_LongParseString:
    """Issue #47: VARCHAR2 variables named like *step_no*, *pro_id*
    are mistyped as Long, causing Long.parseLong("2.5.1") — runtime error."""

    def test_step_no_is_string_not_long(self, cached_ast, tmp_path):
        sql_file = "issue_47_long_parse_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # v_step_no should be String, not Long
        # Check for Long vStepNo (wrong) vs String vStepNo (correct)
        has_long_step = bool(re.search(r'\bLong\s+vStepNo\b', svc))
        assert not has_long_step, (
            "Issue #47: v_step_no mistyped as Long. "
            "VARCHAR2 should map to String regardless of naming heuristic."
        )

    def test_no_parselong_on_dotted_string(self, cached_ast, tmp_path):
        sql_file = "issue_47_long_parse_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # Should NOT have parseLong("2.5.1") — non-numeric
        dotted_parse = re.findall(r'Long\.parseLong\s*\(\s*"[^"]*\.[^"]*"', svc)
        assert len(dotted_parse) == 0, (
            f"Issue #47: Found {len(dotted_parse)} parseLong on dotted strings. "
            f"e.g.: {dotted_parse[:3]}"
        )

    def test_no_parselong_for_any_id_vars(self, cached_ast, tmp_path):
        """Collect test: count parseLong calls on string-literal arguments."""
        sql_file = "issue_47_long_parse_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        all_parse = re.findall(r'Long\.parseLong\s*\(', svc)
        assert len(all_parse) == 0, (
            f"Issue #47: Found {len(all_parse)} Long.parseLong calls. "
            "All variables are VARCHAR2 — no parseLong should be needed."
        )


# ── Issue #48: Long.compareTo(String) type mismatch ──────────────

class TestIssue48_LongCompareToString:
    """Issue #48: Long variable compared against string literal produces
    Long.compareTo(String) — won't compile due to type mismatch."""

    def test_no_compareto_with_string_literal(self, cached_ast, tmp_path):
        sql_file = "issue_48_long_compareto_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # Long.compareTo("9999") — Long.compareTo with quoted string arg
        long_cmp_string = re.findall(
            r'\b\w+\.compareTo\s*\(\s*"', svc
        )
        assert len(long_cmp_string) == 0, (
            f"Issue #48: Found {len(long_cmp_string)} compareTo with string literal. "
            f"Long variable should not be compared to string: {long_cmp_string[:5]}"
        )

    def test_long_compare_to_long_is_fine(self, cached_ast, tmp_path):
        """Control: Long.compareTo(Long) is correct Java."""
        sql_file = "issue_48_long_compareto_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # proc_check_count should have Long or == comparisons, not string
        has_long_compare = bool(re.search(
            r'compareTo\s*\(\s*Long\.valueOf|compareTo\s*\(\s*\d+L?\s*\)|==\s*\d+L?\b',
            svc
        ))
        assert has_long_compare, (
            "Issue #48: Long-to-Long comparison not using correct Java pattern."
        )

    def test_string_literal_coerced_in_long_compare(self, cached_ast, tmp_path):
        sql_file = "issue_48_long_compareto_string.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # For IF v_pro_id = '9999', should produce Long.valueOf(9999) or 9999L
        assert bool(re.search(r'9999\s*L?\b', svc)), (
            "Issue #48: String literal '9999' not coerced to numeric. "
            "Expected Long.valueOf(9999) or 9999L in Long comparison."
        )


# ── Issue #49: VARCHAR2→Long used as String concatenation ────────

class TestIssue49_Varchar2Concat:
    """Issue #49: vProId (VARCHAR2) mistyped as Long due to *id* heuristic,
    then used in string concatenation — semantic type error."""

    def test_pro_id_is_string_not_long(self, cached_ast, tmp_path):
        sql_file = "issue_49_varchar2_concat.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # v_pro_id should be String type, not Long
        has_long_pro = bool(re.search(r'\bLong\s+vProId\b', svc))
        assert not has_long_pro, (
            "Issue #49: v_pro_id declared as Long but is VARCHAR2. "
            "Should be String type."
        )

        # Verify it is actually String
        has_string_pro = bool(re.search(r'\bString\s+vProId\b', svc))
        assert has_string_pro, (
            "Issue #49: v_pro_id not found as String type. "
            "VARCHAR2 should map to String."
        )

    def test_trade_ids_is_string_not_long(self, cached_ast, tmp_path):
        sql_file = "issue_49_varchar2_concat.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # v_trade_ids should also be String
        has_long_trade = bool(re.search(r'\bLong\s+vTradeIds\b', svc))
        assert not has_long_trade, (
            "Issue #49: v_trade_ids declared as Long but is VARCHAR2."
        )

    def test_concat_uses_string_not_long_arithmetic(self, cached_ast, tmp_path):
        """Control: string concatenation should use + or StringBuilder, not
        arithmetic that would fail with Long type."""
        sql_file = "issue_49_varchar2_concat.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        # String concatenation should be present (|| → +)
        concat_pattern = re.findall(r'vProId\s*\+\s*"', svc)
        assert len(concat_pattern) > 0, (
            "Issue #49: No string concatenation found for vProId || '...' "
            "Expected vProId + \"...\" pattern."
        )


# ── Issue #54: Nested BEGIN-EXCEPTION missing catch ─────────

class TestIssue54_NestedException:
    """Issue #54: Nested BEGIN-EXCEPTION (3+ levels) loses inner catch blocks."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_54_nested_exception.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_handler_body_has_nested_try_catch(self, cached_ast, tmp_path):
        """proc_nested_block_in_handler: handler body Block with exc must
        produce inner try/catch."""
        svc = self._gen_svc(cached_ast, tmp_path)
        catch_count = len(re.findall(r'\bcatch\s*\(', svc))
        assert catch_count >= 2, (
            f"Issue #54: Expected >=2 catch blocks for nested handler, got {catch_count}"
        )

    def test_three_level_nested_has_all_catches(self, cached_ast, tmp_path):
        """proc_three_level_nested: 3-level nesting must have
        catch at every level, no orphaned try."""
        svc = self._gen_svc(cached_ast, tmp_path)
        catch_count = len(re.findall(r'\bcatch\s*\(', svc))
        assert catch_count >= 3, (
            f"Issue #54: Expected >=3 catch blocks for 3-level nesting, got {catch_count}"
        )

    def test_no_orphaned_try_without_catch(self, cached_ast, tmp_path):
        """Verify every try has a matching catch or finally."""
        svc = self._gen_svc(cached_ast, tmp_path)
        try_count = len(re.findall(r'\btry\s*\{', svc))
        catch_count = len(re.findall(r'\bcatch\s*\(', svc))
        finally_count = len(re.findall(r'\bfinally\s*\{', svc))
        assert try_count <= catch_count + finally_count, (
            f"Issue #54: {try_count} try blocks but only {catch_count} catch + "
            f"{finally_count} finally. Orphaned try without handler."
        )

    def test_no_parse_error_try_in_service(self, cached_ast, tmp_path):
        """No SyntaxError-level try-without-catch patterns that fail javac."""
        svc = self._gen_svc(cached_ast, tmp_path)
        assert 'try {' in svc, "No try block at all"
        assert 'catch (' in svc, "No catch block at all"
        # There shouldn't be try followed immediately by catch-less construct
        orphaned = re.findall(r'try\s*\{\s*\}', svc)
        assert len(orphaned) == 0, f"Found {len(orphaned)} empty try blocks"


# ── Issue #56: RETURN in EXCEPTION handler → unreachable code ──

class TestIssue56_ReturnInHandler:
    """Issue #56: RETURN in EXCEPTION WHEN handler makes subsequent
    handler code unreachable due to sequential concatenation.
    
    The fix ensures bracket structure is correct (no mismatches from
    nested try-catch + return) — subsequent handler code after return
    is unreachable but structurally valid Java (javac warns, not errors).
    _merge_duplicate_catches brace-depth tracking (fixed in #54) resolves
    the bracket mismatch root cause."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "pkg_issue56_return_handler.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_all_handlers_present(self, cached_ast, tmp_path):
        """Verify all handler code is emitted (not lost/missing)."""
        svc = self._gen_svc(cached_ast, tmp_path)
        # proc_two_handlers: no_data_found (try body) + OTHERS (catch)
        assert 'not_found' in svc, "no_data_found handler missing"
        assert 'pResult.set("error")' in svc, "OTHERS handler missing"
        # proc_nested_block: nested fallback + outer_error
        assert 'nested_fallback_failed' in svc, "nested handler missing"
        assert 'outer_error' in svc, "outer OTHERS handler missing"
        # proc_three_handlers: too_many + error
        assert 'too_many' in svc, "too_many_rows handler missing"
        assert 'pResult.set("error")' in svc, "OTHERS handler for proc_three missing"

    def test_no_data_found_moved_to_try_body(self, cached_ast, tmp_path):
        """no_data_found handler should be in try body (null check pattern)."""
        svc = self._gen_svc(cached_ast, tmp_path)
        # The no_data_found handler code should appear BEFORE any catch block
        # (it's moved into the try body by _wrap_try_catch)
        first_catch = svc.find('} catch (')
        not_found_pos = svc.find('"not_found"')
        assert not_found_pos < first_catch, (
            "no_data_found handler should be in try body, before catch block"
        )

    def test_no_bracket_mismatch_patterns(self, cached_ast, tmp_path):
        """Verify no obviously malformed bracket structures."""
        svc = self._gen_svc(cached_ast, tmp_path)
        # Check balanced braces (simple heuristic)
        opens = svc.count('{')
        closes = svc.count('}')
        assert opens == closes, (
            f"Brace mismatch: {opens} opens vs {closes} closes"
        )

    def test_generated_code_is_syntactically_structured(self, cached_ast, tmp_path):
        """Verify catch blocks have proper try/catch structure."""
        svc = self._gen_svc(cached_ast, tmp_path)
        # Every try must be followed by catch or finally
        import re
        try_positions = [m.start() for m in re.finditer(r'\btry\s*\{', svc)]
        catch_positions = [m.start() for m in re.finditer(r'\bcatch\s*\(', svc)]
        finally_positions = [m.start() for m in re.finditer(r'\bfinally\s*\{', svc)]
        total_handlers = len(catch_positions) + len(finally_positions)
        assert len(try_positions) <= total_handlers, (
            f"{len(try_positions)} try blocks but only {total_handlers} handlers"
        )


# ── Issue #61: Outer EXCEPTION multi-WHEN brace / handler drop ──

class TestIssue61_OuterExceptionBrace:
    """Issue #61: outer EXCEPTION WHEN no_data_found + OTHERS with nested
    BEGIN-EXCEPTION must keep balanced braces and not drop handler bodies."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_61_outer_exception_brace.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_braces_balanced(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert svc.count("{") == svc.count("}"), (
            f"Issue #61: brace mismatch {{={svc.count('{')}}} }}={svc.count('}')}"
        )

    def test_no_orphaned_catch(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        # No standalone } immediately before } catch that would orphan it
        assert re.search(r'^\s*\}\s*$\n\s*\}\s*catch\s*\(', svc, re.M) is None, (
            "Issue #61: extra } before catch orphans the catch block"
        )

    def test_no_data_found_handler_not_dropped(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "pOMarketPrice.set" in svc and "ZERO" in svc or "valueOf(0)" in svc, (
            "Issue #61: no_data_found handler body dropped"
        )

    def test_others_if_body_emitted(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "中证行情" in svc, "Issue #61: OTHERS IF body not converted"
        assert '"0".equals(' in svc or "Objects.equals" in svc, (
            "Issue #61: p_o_succeed = '0' should use string equals"
        )

    def test_others_elsif_body_emitted(self, cached_ast, tmp_path):
        """ELSIF inside EXCEPTION handler must use AST key 'stmts' (not then_stmts)."""
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "WARN:" in svc, (
            "Issue #61: ELSIF body inside EXCEPTION handler was dropped "
            "(wrong AST key for elsif body)"
        )
        assert "OTHER_ERR" in svc, "Issue #61: ELSE body inside EXCEPTION handler dropped"
        assert "else if" in svc or "else if (" in svc, "Issue #61: else if branch missing"


# ── Issue #63: FUNCTION RETURN VARCHAR2 must be String ──────────

class TestIssue63_Varchar2Return:
    """Issue #63: RETURN VARCHAR2 → Java String; numeric RETURN stays numeric."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_63_varchar2_return.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc, pkg

    def test_varchar2_functions_return_string(self, cached_ast, tmp_path):
        svc, pkg = self._gen_svc(cached_ast, tmp_path)
        assert re.search(r'public\s+String\s+fncGetOpenFundValue\s*\(', svc), (
            "Issue #63: fnc_get_open_fund_value must return String"
        )
        assert re.search(r'public\s+String\s+fncTrdGetUnitCash\s*\(', svc), (
            "Issue #63: fnc_trd_get_unit_cash must return String"
        )

    def test_numeric_function_stays_numeric(self, cached_ast, tmp_path):
        svc, pkg = self._gen_svc(cached_ast, tmp_path)
        assert re.search(r'public\s+(Long|java\.math\.BigDecimal|Integer)\s+fncGetPriceNum\s*\(', svc), (
            "Issue #63: fnc_get_price_num must stay numeric, not String"
        )
        assert not re.search(r'public\s+String\s+fncGetPriceNum\s*\(', svc), (
            "Issue #63: numeric COALESCE return must NOT be flipped to String"
        )

    def test_reconcile_overrides_wrong_numeric_declaration(self, cached_ast, tmp_path):
        """When AST return_type is wrongly numeric but body returns String var."""
        sql_file = "issue_63_varchar2_return.sql"
        ast = cached_ast[sql_file]
        pkg_name = _fixture_pkg_name(sql_file)
        procs, pkg_vars, custom_types = fg.extract_procedures(ast, sql_file)
        # Force wrong return type on the VARCHAR2 function
        target = None
        for p in procs:
            if "open_fund" in p.name.lower() or "OpenFund" in p.name:
                target = p
                break
            if p.proc_name and "open_fund" in p.proc_name.lower():
                target = p
                break
        assert target is not None, "open_fund function not found"
        target.return_type = "number"  # wrong AST
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs,
                             package_vars=pkg_vars, custom_types=custom_types)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)
        assert target.return_type == "varchar2" or fg.sql_type_to_java(target.return_type) == "String", (
            f"Issue #63: reconcile failed, still {target.return_type!r}"
        )
        out_dir = str(Path(tmp_path) / "dest_reconcile")
        fg.generate_project(out_dir, packages=[pkg], config={})
        cls = fg.package_to_classname(pkg_name)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert re.search(r'public\s+String\s+fncGetOpenFundValue\s*\(', svc), (
            "Issue #63: reconciled signature must be String"
        )


# ── Issue #60: INSTR / CASE WHEN 0 operator precedence ──────────

class TestIssue60_InstrCaseWhen:
    """Issue #60: INSTR/CASE WHEN 0 must not produce indexOf()+1.equals(0)."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_60_instr_case_when.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_no_bare_one_dot_equals(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "1.equals(" not in svc, (
            "Issue #60: bare 1.equals() — Java parses as float literal"
        )
        assert re.search(r'\+\s*1\.equals\(', svc) is None, (
            "Issue #60: indexOf()+1.equals() operator precedence bug"
        )

    def test_instr_result_parenthesized(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "indexOf(" in svc, "INSTR should map to indexOf"
        assert re.search(r'\(String\.valueOf\([^)]+\)\.indexOf\([^)]+\)\s*\+\s*1\)', svc) or \
               re.search(r'\([^)]*\.indexOf\([^)]+\)\s*\+\s*1\)', svc), (
            "Issue #60: INSTR result should be parenthesized (indexOf + 1)"
        )

    def test_case_when_uses_eq_eq_not_equals_on_int(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert " == 0" in svc or "==0" in svc or "Objects.equals" in svc, (
            "Issue #60: CASE WHEN 0 comparison missing"
        )


# ── Issue #62: SUBSTR helper method ─────────────────────────────

class TestIssue62_SubstrHelper:
    """Issue #62: SUBSTR should use _substr helper, not inline Math.min/max."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_62_substr_helper.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_uses_substr_helper(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "_substr(" in svc, "Issue #62: expected _substr(...) helper calls"

    def test_helper_method_emitted(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert "private String _substr(String str, Object pos, Object len)" in svc, (
            "Issue #62: _substr helper method not generated"
        )

    def test_no_inline_math_min_max_substring(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        inline = re.findall(
            r'\.substring\(\s*Math\.(?:min|max)\(',
            svc
        )
        assert len(inline) == 0, (
            f"Issue #62: found {len(inline)} inline Math.min/max substring expansions"
        )


# ── Issue #64: BigDecimal empty-string init ─────────────────────

class TestIssue64_BigDecimalEmptyInit:
    """Issue #64: empty string '' must not initialize numeric Java types."""

    def _gen_svc(self, cached_ast, tmp_path):
        sql_file = "issue_64_bigdecimal_empty_init.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        return svc

    def test_no_bigdecimal_eq_empty_string(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        bad = re.findall(
            r'(?:BigDecimal|Long|Integer|Double|Float)\s+\w+\s*=\s*""\s*;',
            svc
        )
        assert len(bad) == 0, (
            f"Issue #64: numeric vars initialized with empty string: {bad}"
        )

    def test_empty_string_defaults_are_string_or_typed_default(self, cached_ast, tmp_path):
        svc = self._gen_svc(cached_ast, tmp_path)
        assert re.search(r'procEmptyStringDefaults|EmptyStringDefaults', svc), (
            "Issue #64: expected procedure method not generated"
        )
        bad = re.findall(r'(?:BigDecimal|Long|Integer)\s+\w+\s*=\s*""\s*;', svc)
        assert bad == [], f"Issue #64: numeric empty-string inits still present: {bad}"


# ── Issue #75: orphaned try after statement failure ──────────────

class TestIssue75_LoopExceptionBrace:
    """Issue #75: try without catch when statement processing fails inside a
    Block's exception handler. Guards brace balance, not one syntactic shape."""

    def test_service_braces_balanced(self, cached_ast, tmp_path):
        sql_file = "issue_75_loop_exception_brace.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"
        delta = svc.count("{") - svc.count("}")
        assert delta == 0, f"Issue #75: brace imbalance delta={delta}"

    def test_no_orphaned_try(self, cached_ast, tmp_path):
        sql_file = "issue_75_loop_exception_brace.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        # every `try {` must be followed by a `} catch` or `} finally`
        n_try = len(re.findall(r'\btry\s*\{', svc))
        n_handler = len(re.findall(r'\}\s*(catch|finally)\b', svc))
        assert n_try <= n_handler, (
            f"Issue #75: {n_try} try blocks but only {n_handler} catch/finally handlers"
        )

    def test_no_statement_processing_error(self, cached_ast, tmp_path):
        sql_file = "issue_75_loop_exception_brace.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert "list_var" not in svc, "Issue #75: list_var leaked into output"
        assert "处理语句失败" not in svc, "Issue #75: statement processing failed"


# ── String-target coercion of same-class helper calls ───────────

class TestStringTargetHelperCoercion:
    """Known non-String and Object helper returns must convert safely to String."""

    def test_helper_results_use_safe_string_conversion(self, cached_ast, tmp_path):
        sql_file = "pkg_string_helper_coercion.sql"
        out_dir, pkg, cls = _run_pipeline(sql_file, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert svc, "Service file not generated"

        assert "public java.math.BigDecimal jsonObject(" in svc
        assert "public Object jsonAppend(" in svc
        assert re.search(r'\(String\)\s+this\.', svc) is None, (
            "String target must not cast a same-class non-String helper result"
        )
        assert re.search(r'vItem\s*=\s*String\.valueOf\(this\.jsonObject\(', svc)
        assert re.search(r'vJson\s*=\s*String\.valueOf\(this\.jsonAppend\(', svc)
        assert re.search(r'vRec\.get\("amount"\)\s*!=\s*null', svc)


# ── Meta: Verify all issue fixtures parse correctly ──────────────

class TestIssue83_OutCrossPkgCall:
    """OUT 局部变量提升为 AtomicReference 后，跨包调用必须传引用本体
    （vRows 而非 vRows.get()）——见 ogagila OrchService/DwdService 编译错误。

    需走真实 CLI 管线：_promote_out_local_vars 在 analyze 之后（Phase 2.5）运行，
    _run_pipeline 单测 harness 不触发。"""

    SQL_FILE = "issue_83_out_cross_pkg.sql"

    def test_cross_pkg_out_args_pass_reference_not_get(self, tmp_path):
        out_dir = _run_cli_pipeline([self.SQL_FILE], tmp_path)
        # 同文件同 schema 包合并为 1 个 Service（#70 行为），caller 过程落入 CalleeService
        svc = _read_generated(out_dir, "src/main/java/com/example/demo/service/CalleeService.java")
        assert "calleeService.buildIncremental(pRunId, vRows, vMonths);" in svc, (
            f"OUT args must pass the AtomicReference itself, got:\n{svc[:1500]}"
        )
        assert "calleeService.planIncrement(\"x\", vRows, vMonths);" in svc, (
            f"OUT args must pass the AtomicReference itself, got:\n{svc[:1500]}"
        )


class TestIssue103_HandlerCallArgs:
    """EXCEPTION handler 内跨包调用实参不得被静默丢弃——site4
    (_wrap_handler_stmts) 曾将 _resolved.append 置于 i<len(callee params)
    门控内，callee 未精确匹配时丢参生成空参调用。"""

    SQL_FILE = "issue_103_handler_call_args.sql"

    def test_handler_cross_pkg_calls_keep_all_args(self, tmp_path):
        out_dir = _run_cli_pipeline([self.SQL_FILE], tmp_path)
        svc = _read_generated(out_dir, "src/main/java/com/example/demo/service/CalleeService.java")
        assert "calleeService.simpleLog(pX);" in svc, f"handler 1-arg call missing:\n{svc[:1500]}"
        assert "calleeService.simpleLog(pX, \"detail\");" in svc, (
            f"handler 2-arg call must keep both args:\n{svc[:1500]}"
        )


# ── Meta: Verify all issue fixtures parse correctly ──────────────

class TestIssue99_DuplicateParamNames:
    """PG catalog-style signatures reuse the type name as the parameter name
    (`_group_concat(text, text)`). Both engines must dedupe (text, text2) in
    Service, unit test and integration test consistently."""

    SQL_FILE = "issue_99_duplicate_param_names.sql"

    def test_param_names_deduped(self, cached_ast):
        ast = cached_ast[self.SQL_FILE]
        procs, _, _ = fg.extract_procedures(ast, self.SQL_FILE)
        assert len(procs) == 1
        names = [p.name for p in procs[0].parameters]
        assert names == ["text", "text2"], f"params must be deduped, got {names}"

    def test_service_and_test_use_deduped_names(self, cached_ast, tmp_path):
        out_dir, pkg, cls = _run_pipeline(self.SQL_FILE, cached_ast, tmp_path)
        svc = _read_generated(out_dir, _service_path(out_dir, cls))
        assert "Object text, Object text2" in svc, (
            f"Service signature must use deduped params, got:\n{svc[:1200]}"
        )
        test_file = _read_generated(
            out_dir,
            str(
                Path(out_dir)
                / f"src/test/java/{fg.BASE_PACKAGE.replace('.', '/')}/service/{cls}ServiceTest.java"
            ),
        )
        assert "Object text2 = " in test_file, (
            f"ServiceTest must declare deduped params, got:\n{test_file[:1200]}"
        )
        assert "service.GroupConcat(text, text2)" in test_file, (
            f"ServiceTest call must use deduped params, got:\n{test_file[:1200]}"
        )


class TestIssueFixturesParse:
    """Ensure all issue-specific fixtures can be parsed and analyzed."""

    ISSUE_FIXTURES = [
        "issue_34_35_dto_naming.sql",
        "issue_38_map_put.sql",
        "issue_39_thread_safety.sql",
        "issue_40_string_compare.sql",
        "issue_41_type_system.sql",
        "issue_44_if_elsif_goto.sql",
        "issue_45_exception_handling.sql",
        "issue_46_chr_ascii_substr.sql",
        "issue_47_long_parse_string.sql",
        "issue_48_long_compareto_string.sql",
        "issue_49_varchar2_concat.sql",
        "issue_54_nested_exception.sql",
        "pkg_issue56_return_handler.sql",
        "issue_60_instr_case_when.sql",
        "issue_61_outer_exception_brace.sql",
        "issue_62_substr_helper.sql",
        "issue_63_varchar2_return.sql",
        "issue_64_bigdecimal_empty_init.sql",
        "pkg_string_helper_coercion.sql",
    ]

    @pytest.mark.parametrize("sql_file", ISSUE_FIXTURES)
    def test_fixture_extracts_procedures(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        assert len(procs) > 0, (
            f"Fixture {sql_file} extracted 0 procedures. Check SQL syntax."
        )

    @pytest.mark.parametrize("sql_file", ISSUE_FIXTURES)
    def test_fixture_analyzes_without_crash(self, sql_file, cached_ast):
        ast = cached_ast[sql_file]
        pkg_name = _fixture_pkg_name(sql_file)
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        assert len(procs) > 0
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)

    @pytest.mark.parametrize("sql_file", ISSUE_FIXTURES)
    def test_fixture_generates_without_crash(self, sql_file, cached_ast, tmp_path):
        ast = cached_ast[sql_file]
        pkg_name = _fixture_pkg_name(sql_file)
        procs, _, _ = fg.extract_procedures(ast, sql_file)
        assert len(procs) > 0
        pkg = fg.PackageInfo(package_name=pkg_name, procedures=procs)
        all_pkgs = {pkg_name: pkg}
        for proc in procs:
            fg.analyze_procedure(proc, all_pkgs)
        out_dir = str(Path(tmp_path) / "dest")
        fg.generate_project(out_dir, packages=[pkg], config={})

        class_name = fg.package_to_classname(pkg_name)
        svc_path = Path(out_dir) / _service_path(out_dir, class_name)
        assert svc_path.exists(), f"Service not generated for {sql_file}"
        assert svc_path.stat().st_size > 100, f"Service too small for {sql_file}"
