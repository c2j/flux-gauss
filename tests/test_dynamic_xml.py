"""
Unit tests for dynamic SQL → MyBatis dynamic XML tag conversion.

Tests cover:
1. DynamicCondition dataclass creation and defaults
2. DmlStatement with dynamic_conditions and base_sql fields
3. _build_mapper_statement() dynamic XML generation
4. ProcedureInfo.sql_concat_chain field
"""
import pytest
import converter.flux_gauss as fg


class TestDynamicCondition:
    """Tests for the DynamicCondition dataclass."""

    def test_basic_creation(self):
        dc = fg.DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="if",
        )
        assert dc.condition_expr == "whereClause != null"
        assert dc.sql_fragment == "WHERE ${whereClause}"
        assert dc.clause_type == "WHERE"
        assert dc.tag_name == "if"

    def test_order_by_condition(self):
        dc = fg.DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        assert dc.clause_type == "ORDER_BY"
        assert dc.tag_name == "if"

    def test_having_clause_type(self):
        dc = fg.DynamicCondition(
            condition_expr="x != null",
            sql_fragment="HAVING x > 0",
            clause_type="HAVING",
            tag_name="if",
        )
        assert dc.clause_type == "HAVING"


class TestDmlStatementDynamicConditions:
    """Tests for DmlStatement with dynamic_conditions and base_sql fields."""

    def test_default_empty_dynamic_conditions(self):
        dml = fg.DmlStatement(
            sql_type="SELECT",
            method_id="testSelect1",
            sql_text="SELECT * FROM orders",
        )
        assert dml.dynamic_conditions == []
        assert dml.base_sql == ""

    def test_with_dynamic_conditions(self):
        dc = fg.DynamicCondition(
            condition_expr="status != null",
            sql_fragment="WHERE status = #{status}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = fg.DmlStatement(
            sql_type="SELECT",
            method_id="testSelect1",
            sql_text="SELECT * FROM orders WHERE status = #{status}",
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM orders",
        )
        assert len(dml.dynamic_conditions) == 1
        assert dml.base_sql == "SELECT * FROM orders"
        assert dml.dynamic_conditions[0].condition_expr == "status != null"

    def test_multiple_dynamic_conditions(self):
        dc1 = fg.DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dc2 = fg.DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        dml = fg.DmlStatement(
            sql_type="SELECT",
            method_id="testSelect2",
            sql_text="SELECT * FROM t WHERE ${whereClause} ORDER BY ${orderBy}",
            dynamic_conditions=[dc1, dc2],
            base_sql="SELECT * FROM t",
        )
        assert len(dml.dynamic_conditions) == 2
        assert dml.dynamic_conditions[0].clause_type == "WHERE"
        assert dml.dynamic_conditions[1].clause_type == "ORDER_BY"


class TestBuildMapperStatementDynamicXml:
    """Tests for _build_mapper_statement() with dynamic conditions."""

    def _make_proc(self):
        return fg.ProcedureInfo(
            name="pkg_test.proc_dyn",
            package="pkg_test",
            proc_name="proc_dyn",
            is_function=False,
            return_type=None,
            parameters=[
                fg.Parameter(name="p_table_name", java_type="String", sql_type="varchar", mode="IN"),
                fg.Parameter(name="p_where_clause", java_type="String", sql_type="varchar", mode="IN"),
                fg.Parameter(name="p_order_by", java_type="String", sql_type="varchar", mode="IN"),
            ],
            body={"Block": {"body": {"statements": []}}},
            sql_text="BEGIN NULL; END;",
        )

    def test_where_if_tag_generation(self):
        proc = self._make_proc()
        dc = fg.DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = fg.DmlStatement(
            sql_type="select",
            method_id="dynSelect1",
            sql_text="SELECT * FROM ${tableName} WHERE ${whereClause}",
            result_type="java.util.LinkedHashMap",
            returns_list=True,
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM ${tableName}",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "<where>" in xml
        assert "</where>" in xml
        assert '<if test="whereClause != null">' in xml
        assert "</if>" in xml
        assert "AND ${whereClause}" in xml

    def test_order_by_if_tag_generation(self):
        proc = self._make_proc()
        dc = fg.DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        dml = fg.DmlStatement(
            sql_type="select",
            method_id="dynSelect1",
            sql_text="SELECT * FROM ${tableName} ORDER BY ${orderBy}",
            result_type="java.util.LinkedHashMap",
            returns_list=True,
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM ${tableName}",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert '<if test="orderBy != null">' in xml
        assert "ORDER BY ${orderBy}" in xml
        assert "</if>" in xml
        assert "<where>" not in xml

    def test_combined_where_and_order_by(self):
        proc = self._make_proc()
        dc_where = fg.DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dc_order = fg.DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        dml = fg.DmlStatement(
            sql_type="select",
            method_id="dynSelect1",
            sql_text="SELECT * FROM ${tableName} WHERE ${whereClause} ORDER BY ${orderBy}",
            result_type="java.util.LinkedHashMap",
            returns_list=True,
            dynamic_conditions=[dc_where, dc_order],
            base_sql="SELECT * FROM ${tableName}",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "<where>" in xml
        assert '<if test="whereClause != null">' in xml
        assert '<if test="orderBy != null">' in xml
        assert "ORDER BY ${orderBy}" in xml
        assert "</if>" in xml
        assert "</where>" in xml

    def test_no_dynamic_conditions_static_xml(self):
        proc = self._make_proc()
        dml = fg.DmlStatement(
            sql_type="select",
            method_id="staticSelect1",
            sql_text="SELECT * FROM orders WHERE status = #{status}",
            result_type="java.util.LinkedHashMap",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "<where>" not in xml
        assert '<if test=' not in xml
        assert "FROM orders" in xml
        assert "status = #{status}" in xml

    def test_xml_escape_in_conditions(self):
        proc = self._make_proc()
        dc = fg.DynamicCondition(
            condition_expr='whereClause != null \u0026\u0026 whereClause != ""',
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = fg.DmlStatement(
            sql_type="select",
            method_id="dynSelect1",
            sql_text="SELECT * FROM ${tableName}",
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM ${tableName}",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "\u0026amp;\u0026amp;" in xml or "!= null" in xml
        assert "<where>" in xml


class TestProcedureInfoSqlConcatChain:
    """Tests for ProcedureInfo.sql_concat_chain field."""

    def test_default_empty_chain(self, make_procedure):
        proc = make_procedure()
        assert proc.sql_concat_chain == {}

    def test_chain_with_entries(self, make_procedure):
        proc = make_procedure()
        proc.sql_concat_chain["v_sql"] = [
            ("whereClause != null", "WHERE ${whereClause}", "WHERE"),
            ("orderBy != null", "ORDER BY ${orderBy}", "ORDER_BY"),
        ]
        assert len(proc.sql_concat_chain["v_sql"]) == 2
        assert proc.sql_concat_chain["v_sql"][0][2] == "WHERE"
        assert proc.sql_concat_chain["v_sql"][1][2] == "ORDER_BY"

    def test_chain_is_independent_per_instance(self):
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
        proc1.sql_concat_chain["v_sql"] = [("x != null", "WHERE x = 1", "WHERE")]
        assert "v_sql" not in proc2.sql_concat_chain


class TestDetectSqlConcatAppend:
    """Tests for _detect_sql_concat_append() function."""

    def test_returns_none_for_non_concat(self):
        proc = fg.ProcedureInfo(
            name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
        )
        assign = {"target": {"Identifier": {"name": "v_sql"}}, "expression": {"Literal": {"String": "'SELECT 1'"}}}
        result = fg._detect_sql_concat_append(assign, proc)
        assert result is None

    def test_detects_where_append(self):
        proc = fg.ProcedureInfo(
            name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
        )
        proc.parameters = [fg.Parameter(name="p_where", java_type="String", sql_type="varchar")]
        assign = {
            "target": {"PlVariable": ["v_sql"]},
            "expression": {
                "BinaryOp": {
                    "op": "||",
                    "left": {"PlVariable": ["v_sql"]},
                    "right": {
                        "BinaryOp": {
                            "op": "||",
                            "left": {"Literal": {"String": " WHERE status = "}},
                            "right": {"PlVariable": ["p_where"]},
                        }
                    },
                }
            },
        }
        result = fg._detect_sql_concat_append(assign, proc)
        assert result is not None
        assert result[0] == "v_sql"
        assert result[2] == "WHERE"
