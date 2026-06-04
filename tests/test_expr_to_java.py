"""
Tests for _expr_to_java and related expression conversion functions.

Covers: ColumnRef, Literal, BinaryOp, FunctionCall, InList, IsNull, Between,
_infer_expr_type, _coerce_java_arg.
"""
import pytest
import converter.flux_gauss as fg


@pytest.fixture
def proc():
    return fg.ProcedureInfo(
        name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
        is_function=False, return_type=None, parameters=[],
        body={}, sql_text="",
        local_vars={"x": "Integer", "v_name": "String", "flag": "Boolean",
                     "amount": "java.math.BigDecimal"},
    )


class TestExprToJavaColumnRef:
    def test_simple_var(self):
        result = fg._expr_to_java({"ColumnRef": ["my_var"]})
        assert result == "myVar"

    def test_var_with_proc_type(self, proc):
        result = fg._expr_to_java({"ColumnRef": ["v_name"]}, proc)
        assert result == "vName"

    def test_integer_var(self, proc):
        result = fg._expr_to_java({"ColumnRef": ["x"]}, proc)
        assert result == "x"


class TestExprToJavaLiteral:
    def test_integer(self):
        result = fg._expr_to_java({"Literal": {"Integer": 42}})
        assert result == "42"

    def test_string(self):
        result = fg._expr_to_java({"Literal": {"String": "hello"}})
        assert result == '"hello"'

    def test_null(self):
        result = fg._expr_to_java({"Literal": {"Null": None}})
        assert result == "null"

    def test_float(self, proc):
        result = fg._expr_to_java({"Literal": {"Float": 3.14}}, proc)
        assert "3.14" in result

    def test_boolean_true(self):
        result = fg._expr_to_java({"Literal": {"Boolean": True}})
        assert result == "true"

    def test_boolean_false(self):
        result = fg._expr_to_java({"Literal": {"Boolean": False}})
        assert result == "false"


class TestExprToJavaBinaryOp:
    def test_equality(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": "=", "left": {"ColumnRef": ["x"]}, "right": {"Literal": {"Integer": 1}}}},
            proc,
        )
        assert result == "x == 1"

    def test_string_concat(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": "||", "left": {"Literal": {"String": "a"}}, "right": {"Literal": {"String": "b"}}}},
            proc,
        )
        assert result == '"a" + "b"'

    def test_and(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": "AND", "left": {"ColumnRef": ["flag"]}, "right": {"ColumnRef": ["flag"]}}},
            proc,
        )
        assert "&&" in result

    def test_or(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": "OR", "left": {"ColumnRef": ["flag"]}, "right": {"ColumnRef": ["flag"]}}},
            proc,
        )
        assert "||" in result

    def test_not_equal(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": "<>", "left": {"ColumnRef": ["x"]}, "right": {"Literal": {"Integer": 0}}}},
            proc,
        )
        assert "!=" in result

    def test_greater_than(self, proc):
        result = fg._expr_to_java(
            {"BinaryOp": {"op": ">", "left": {"ColumnRef": ["x"]}, "right": {"Literal": {"Integer": 0}}}},
            proc,
        )
        assert ">" in result


class TestExprToJavaUnaryOp:
    def test_not(self):
        result = fg._expr_to_java({"UnaryOp": {"op": "NOT", "expr": {"ColumnRef": ["flag"]}}})
        assert result == "!flag"

    def test_negation(self, proc):
        result = fg._expr_to_java({"UnaryOp": {"op": "-", "expr": {"ColumnRef": ["x"]}}}, proc)
        assert "-" in result


class TestExprToJavaIsNull:
    def test_is_null(self):
        result = fg._expr_to_java({"IsNull": {"expr": {"ColumnRef": ["x"]}, "negated": False}})
        assert result == "x == null"

    def test_is_not_null(self):
        result = fg._expr_to_java({"IsNull": {"expr": {"ColumnRef": ["x"]}, "negated": True}})
        assert result == "x != null"


class TestExprToJavaInList:
    def test_in_list(self):
        result = fg._expr_to_java(
            {"InList": {"expr": {"ColumnRef": ["status"]}, "list": [{"Literal": {"String": "A"}}, {"Literal": {"String": "B"}}], "negated": False}},
        )
        assert "Arrays.asList" in result
        assert '"A"' in result
        assert '"B"' in result
        assert ".contains(" in result

    def test_not_in(self):
        result = fg._expr_to_java(
            {"InList": {"expr": {"ColumnRef": ["status"]}, "list": [{"Literal": {"String": "A"}}], "negated": True}},
        )
        assert "!" in result


class TestExprToJavaBetween:
    def test_between(self, proc):
        result = fg._expr_to_java(
            {"Between": {"expr": {"ColumnRef": ["x"]}, "low": {"Literal": {"Integer": 1}}, "high": {"Literal": {"Integer": 10}}, "negated": False}},
            proc,
        )
        assert ">=" in result
        assert "<=" in result

    def test_not_between(self, proc):
        result = fg._expr_to_java(
            {"Between": {"expr": {"ColumnRef": ["x"]}, "low": {"Literal": {"Integer": 1}}, "high": {"Literal": {"Integer": 10}}, "negated": True}},
            proc,
        )
        assert "!" in result


class TestExprToJavaFunctionCall:
    def test_upper(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["upper"], "args": [{"ColumnRef": ["v_name"]}]}}, proc,
        )
        assert "toUpperCase" in result

    def test_lower(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["lower"], "args": [{"ColumnRef": ["v_name"]}]}}, proc,
        )
        assert "toLowerCase" in result

    def test_floor(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["floor"], "args": [{"ColumnRef": ["amount"]}]}}, proc,
        )
        assert "Math.floor" in result

    def test_abs(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["abs"], "args": [{"ColumnRef": ["x"]}]}}, proc,
        )
        assert "Math.abs" in result

    def test_length(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["length"], "args": [{"ColumnRef": ["v_name"]}]}}, proc,
        )
        assert ".length()" in result

    def test_instr(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["instr"], "args": [{"ColumnRef": ["v_name"]}, {"Literal": {"String": "."}}]}}, proc,
        )
        assert "indexOf" in result
        assert "+ 1" in result

    def test_nvl(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["nvl"], "args": [{"ColumnRef": ["x"]}, {"Literal": {"Integer": 0}}]}}, proc,
        )
        assert "!=" in result or "? :" in result

    def test_now(self, proc):
        result = fg._expr_to_java({"FunctionCall": {"name": ["now"], "args": []}}, proc)
        assert "System.currentTimeMillis" in result

    def test_gen_random_uuid(self, proc):
        result = fg._expr_to_java({"FunctionCall": {"name": ["gen_random_uuid"], "args": []}}, proc)
        assert "UUID" in result

    def test_coalesce(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["coalesce"], "args": [{"ColumnRef": ["x"]}, {"Literal": {"Integer": 0}}]}}, proc,
        )
        assert "requireNonNullElse" in result or "Objects" in result

    def test_mod(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["mod"], "args": [{"ColumnRef": ["x"]}, {"Literal": {"Integer": 2}}]}}, proc,
        )
        assert "%" in result

    def test_sqrt(self, proc):
        result = fg._expr_to_java(
            {"FunctionCall": {"name": ["sqrt"], "args": [{"ColumnRef": ["x"]}]}}, proc,
        )
        assert "Math.sqrt" in result

    def test_random(self, proc):
        result = fg._expr_to_java({"FunctionCall": {"name": ["random"], "args": []}}, proc)
        assert "Math.random" in result

    def test_pi(self, proc):
        result = fg._expr_to_java({"FunctionCall": {"name": ["pi"], "args": []}}, proc)
        assert "Math.PI" in result


class TestInferExprType:
    def test_integer_literal(self, proc):
        assert fg._infer_expr_type({"Literal": {"Integer": 42}}, proc) == "Integer"

    def test_string_literal(self, proc):
        assert fg._infer_expr_type({"Literal": {"String": "hi"}}, proc) == "String"

    def test_float_literal(self, proc):
        result = fg._infer_expr_type({"Literal": {"Float": 3.14}}, proc)
        assert result in ("Double", "Float")

    def test_var_from_local_vars(self, proc):
        assert fg._infer_expr_type({"ColumnRef": ["x"]}, proc) == "Integer"
        assert fg._infer_expr_type({"ColumnRef": ["v_name"]}, proc) == "String"

    def test_bd_var(self, proc):
        assert fg._infer_expr_type({"ColumnRef": ["amount"]}, proc) == "java.math.BigDecimal"


class TestCoerceJavaArg:
    def test_empty_to_long(self):
        result = fg._coerce_java_arg("", "Long")
        assert result == "" or "0" in result

    def test_empty_to_integer(self):
        result = fg._coerce_java_arg("", "Integer")
        assert result == "" or "0" in result

    def test_bd_to_string(self):
        result = fg._coerce_java_arg("BigDecimal.valueOf(10)", "String")
        assert ".toString()" in result

    def test_passthrough(self):
        result = fg._coerce_java_arg("someVar", "String")
        assert result == "someVar"
