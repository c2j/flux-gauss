"""
Tests for expression conversion utility functions in converter/flux_gauss.py.
"""
import pytest
import converter.flux_gauss as fg


class TestLiteralToJava:
    def test_string_literal(self):
        result = fg._literal_to_java({"String": "hello"})
        assert result == '"hello"'

    def test_integer_literal(self):
        result = fg._literal_to_java({"Integer": 42})
        assert result == "42"

    def test_float_literal(self):
        result = fg._literal_to_java({"Float": 3.14})
        assert result == "3.14d"

    def test_boolean_true(self):
        result = fg._literal_to_java({"Boolean": True})
        assert result == "true"

    def test_boolean_false(self):
        result = fg._literal_to_java({"Boolean": False})
        assert result == "false"

    def test_null_dict(self):
        result = fg._literal_to_java({"Null": {}})
        assert result == "null"

    def test_null_string(self):
        result = fg._literal_to_java("Null")
        assert result == "null"

    def test_string_passthrough(self):
        result = fg._literal_to_java("some_string")
        assert result == "some_string"


class TestJavaOp:
    """Test _java_op() — SQL operator → Java operator mapping."""

    def test_and(self):
        assert fg._java_op("AND") == "&&"

    def test_or(self):
        assert fg._java_op("OR") == "||"

    def test_eq(self):
        assert fg._java_op("=") == "=="

    def test_neq(self):
        assert fg._java_op("<>") == "!="

    def test_neq_alt(self):
        assert fg._java_op("!=") == "!="

    def test_lt(self):
        assert fg._java_op("<") == "<"

    def test_gt(self):
        assert fg._java_op(">") == ">"

    def test_lte(self):
        assert fg._java_op("<=") == "<="

    def test_gte(self):
        assert fg._java_op(">=") == ">="

    def test_is_null(self):
        assert fg._java_op("IS NULL") == "== null"

    def test_is_not_null(self):
        assert fg._java_op("IS NOT NULL") == "!= null"

    def test_unknown_passthrough(self):
        assert fg._java_op("IS") == "IS"
        assert fg._java_op("SOME_OP") == "SOME_OP"

    def test_concat(self):
        assert fg._java_op("||") == "+"


class TestIsNumericLiteral:
    def test_literal_integer(self):
        assert fg._is_numeric_literal({"Literal": {"Integer": "42"}}) is True

    def test_literal_float(self):
        assert fg._is_numeric_literal({"Literal": {"Float": "3.14"}}) is True

    def test_string_literal(self):
        assert fg._is_numeric_literal({"Literal": {"String": "42"}}) is False

    def test_non_literal(self):
        assert fg._is_numeric_literal({"ColumnRef": {}}) is False

    def test_number_key(self):
        assert fg._is_numeric_literal({"Number": {"value": "42"}}) is False

    def test_non_dict(self):
        assert fg._is_numeric_literal("42") is False


class TestIsNumericLiteralExpr:
    """Test _is_numeric_literal_expr() — detects numeric Java expressions."""

    def test_integer(self):
        assert fg._is_numeric_literal_expr("42") is True

    def test_negative(self):
        assert fg._is_numeric_literal_expr("-1") is True

    def test_long_literal(self):
        assert fg._is_numeric_literal_expr("100L") is True

    def test_float(self):
        assert fg._is_numeric_literal_expr("3.14") is True

    def test_not_numeric(self):
        assert fg._is_numeric_literal_expr("hello") is False

    def test_variable(self):
        assert fg._is_numeric_literal_expr("orderId") is False


class TestCoerceForInt:
    """Test _coerce_for_int() — coerce expression to int."""

    def test_long_to_int(self):
        result = fg._coerce_for_int("42L")
        assert result == "42"

    def test_plain_int_unchanged(self):
        result = fg._coerce_for_int("42")
        assert result == "42"

    def test_negative_long(self):
        result = fg._coerce_for_int("-5L")
        assert result == "-5"


class TestEscapeJavaString:
    """Test _escape_java_string() — escapes special chars for Java strings."""

    def test_backslash(self):
        assert "\\\\" in fg._escape_java_string("a\\b")

    def test_double_quote(self):
        assert '\\"' in fg._escape_java_string('a"b')

    def test_newline(self):
        assert "\\n" in fg._escape_java_string("a\nb")

    def test_tab(self):
        assert "\\t" in fg._escape_java_string("a\tb")

    def test_no_escape_needed(self):
        assert fg._escape_java_string("hello") == "hello"


class TestIndent:
    """Test _indent() — indents text by level."""

    def test_level_1(self):
        result = fg._indent("hello", 1)
        assert result.startswith("    ")

    def test_level_0(self):
        result = fg._indent("hello", 0)
        assert result == "hello"

    def test_multiline(self):
        result = fg._indent("line1\nline2", 1)
        lines = result.split("\n")
        assert all(l.startswith("    ") for l in lines)


class TestFlattenComment:
    def test_strips_nested_block_comment_markers(self):
        result = fg._flatten_comment("/* outer /* inner */ end */")
        assert "/*" not in result
        assert "*/" not in result

    def test_preserves_line_comment(self):
        result = fg._flatten_comment("-- hello world")
        assert result == "-- hello world"


class TestCleanSql:
    def test_collapses_whitespace(self):
        result = fg._clean_sql("SELECT   *   FROM   t")
        assert "   " not in result

    def test_strips_leading_trailing_whitespace(self):
        result = fg._clean_sql("  SELECT 1  ")
        assert result == result.strip()

    def test_normalizes_parentheses(self):
        result = fg._clean_sql("SELECT * FROM ( SELECT 1 )")
        assert "( " not in result or " )" not in result
