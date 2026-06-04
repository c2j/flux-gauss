"""
Tests for dataclass models in converter/flux_gauss.py.

These tests verify correct initialization, property computation,
and default values for the core data structures.
"""
import pytest
import converter.flux_gauss as fg


class TestParameter:
    """Test Parameter dataclass and its computed properties."""

    def test_basic_creation(self):
        p = fg.Parameter(name="p_id", java_type="Long", sql_type="bigint", mode="IN")
        assert p.name == "p_id"
        assert p.java_type == "Long"
        assert p.sql_type == "bigint"
        assert p.mode == "IN"

    def test_java_name_property(self):
        p = fg.Parameter(name="order_id", java_type="Long", sql_type="bigint")
        assert p.java_name == "orderId"

    def test_java_name_single_word(self):
        p = fg.Parameter(name="id", java_type="Long", sql_type="bigint")
        assert p.java_name == "id"

    def test_is_out_true_for_out(self):
        p = fg.Parameter(name="p_result", java_type="String", sql_type="varchar", mode="OUT")
        assert p.is_out is True

    def test_is_out_true_for_inout(self):
        p = fg.Parameter(name="p_result", java_type="String", sql_type="varchar", mode="INOUT")
        assert p.is_out is True

    def test_is_out_false_for_in(self):
        p = fg.Parameter(name="p_id", java_type="Long", sql_type="bigint", mode="IN")
        assert p.is_out is False

    def test_is_out_false_for_none(self):
        p = fg.Parameter(name="p_id", java_type="Long", sql_type="bigint")
        assert not p.is_out

    def test_is_refcursor_true(self):
        p = fg.Parameter(name="p_cur", java_type="List<Map<String, Object>>", sql_type="refcursor")
        assert p.is_refcursor is True

    def test_is_refcursor_true_ref_cursor(self):
        p = fg.Parameter(name="p_cur", java_type="List<Map<String, Object>>", sql_type="ref cursor")
        assert p.is_refcursor is True

    def test_is_refcursor_false(self):
        p = fg.Parameter(name="p_id", java_type="Long", sql_type="bigint")
        assert p.is_refcursor is False

    def test_default_mode_is_none(self):
        p = fg.Parameter(name="test", java_type="String", sql_type="varchar")
        assert p.mode is None


class TestCommentInfo:
    """Test CommentInfo dataclass."""

    def test_creation(self):
        c = fg.CommentInfo(text="-- hello", line=5, end_line=5, column=1, comment_type="line")
        assert c.text == "-- hello"
        assert c.line == 5
        assert c.comment_type == "line"

    def test_block_comment(self):
        c = fg.CommentInfo(text="/* block */", line=1, end_line=3, column=0, comment_type="block")
        assert c.end_line == 3


class TestDmlStatement:
    """Test DmlStatement dataclass defaults."""

    def test_minimal_creation(self):
        d = fg.DmlStatement(sql_type="SELECT", method_id="getOrders", sql_text="SELECT * FROM orders")
        assert d.sql_type == "SELECT"
        assert d.method_id == "getOrders"
        assert d.result_type is None
        assert d.parameter_types == {}
        assert d.optional_filters == []
        assert d.returns_list is False
        assert d.is_dynamic is False
        assert d.returning_cols == []
        assert d.returning_into_vars == []
        assert d.is_forall_batch is False

    def test_full_creation(self):
        d = fg.DmlStatement(
            sql_type="INSERT",
            method_id="insertOrder",
            sql_text="INSERT INTO orders VALUES(?)",
            result_type="Integer",
            parameter_types={"p_id": "Long"},
            returns_list=False,
            is_dynamic=True,
        )
        assert d.is_dynamic is True
        assert d.result_type == "Integer"


class TestProcedureInfo:
    """Test ProcedureInfo dataclass initialization and defaults."""

    def test_minimal_creation(self):
        proc = fg.ProcedureInfo(
            name="pkg_test.do_something",
            package="pkg_test",
            proc_name="do_something",
            is_function=False,
            return_type=None,
            parameters=[],
            body={"Block": {"body": {"statements": []}}},
            sql_text="BEGIN NULL; END;",
        )
        assert proc.name == "pkg_test.do_something"
        assert proc.is_function is False
        assert proc.dml_statements == []
        assert proc.service_calls == []
        assert proc.java_logic_lines == []
        assert proc.imports == set()
        assert proc.local_vars == {}
        assert proc.table_refs == set()
        assert proc.is_autonomous is False

    def test_function_creation(self):
        proc = fg.ProcedureInfo(
            name="pkg_test.get_name",
            package="pkg_test",
            proc_name="get_name",
            is_function=True,
            return_type="varchar",
            parameters=[],
            body={"Block": {"body": {"statements": []}}},
            sql_text="BEGIN RETURN 'test'; END;",
        )
        assert proc.is_function is True
        assert proc.return_type == "varchar"

    def test_default_collections_are_independent(self):
        """Each instance should have its own collection objects."""
        proc1 = fg.ProcedureInfo(
            name="p1", package="pkg", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
        )
        proc2 = fg.ProcedureInfo(
            name="p2", package="pkg", proc_name="p2",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
        )
        proc1.local_vars["x"] = "String"
        assert "x" not in proc2.local_vars


class TestPackageInfo:
    """Test PackageInfo dataclass."""

    def test_creation(self):
        pkg = fg.PackageInfo(package_name="pkg_order")
        assert pkg.package_name == "pkg_order"
        assert pkg.procedures == []
        assert pkg.table_refs == set()

    def test_with_procedures(self):
        proc = fg.ProcedureInfo(
            name="pkg_test.p1", package="pkg_test", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
        )
        pkg = fg.PackageInfo(package_name="pkg_test", procedures=[proc])
        assert len(pkg.procedures) == 1
        assert pkg.procedures[0].proc_name == "p1"


class TestSkippedItem:
    """Test SkippedItem dataclass."""

    def test_creation(self):
        item = fg.SkippedItem(
            sql_file="test.sql",
            statement_type="CreateTable",
            category="DDL",
            name="orders",
            detail="CREATE TABLE orders (...)",
        )
        assert item.sql_file == "test.sql"
        assert item.category == "DDL"


class TestProcedureMapping:
    """Test ProcedureMapping dataclass."""

    def test_creation(self):
        m = fg.ProcedureMapping(
            sql_file="pkg.sql",
            procedure_name="create_order",
            procedure_type="PROCEDURE",
            java_service="OrderService",
            java_method="createOrder",
            mapper_methods=["insertOrder"],
            generated_files=["OrderService.java"],
        )
        assert m.is_stub is False
        assert m.has_parse_error is False
        assert m.stub_reasons == []


class TestConversionReport:
    """Test ConversionReport dataclass."""

    def test_creation(self):
        r = fg.ConversionReport(
            generated_at="2026-05-26",
            config_path="fluxgauss.yaml",
            output_dir="./dest",
            sql_files=["a.sql"],
            procedure_mappings=[],
            skipped_items=[],
            parse_errors=[],
            parse_warnings=[],
            unresolved_calls=[],
        )
        assert r.total_packages == 0
        assert r.total_procedures == 0
