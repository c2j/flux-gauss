"""
Tests for type conversion functions in converter/flux_gauss.py.

These are the most critical pure functions — every SQL-to-Java conversion
depends on correct type mapping.
"""
import pytest
import converter.flux_gauss as fg


class TestSqlTypeToJava:
    """Test sql_type_to_java() — the core SQL → Java type mapping."""

    # ── String types ──
    def test_varchar(self):
        assert fg.sql_type_to_java("varchar") == "String"

    def test_varchar2(self):
        assert fg.sql_type_to_java("varchar2") == "String"

    def test_text(self):
        assert fg.sql_type_to_java("text") == "String"

    def test_char(self):
        assert fg.sql_type_to_java("char") == "String"

    # ── Integer types ──
    def test_bigint(self):
        assert fg.sql_type_to_java("bigint") == "Long"

    def test_integer(self):
        assert fg.sql_type_to_java("integer") == "Integer"

    def test_int(self):
        assert fg.sql_type_to_java("int") == "Integer"

    def test_int4(self):
        assert fg.sql_type_to_java("int4") == "Integer"

    def test_int8(self):
        assert fg.sql_type_to_java("int8") == "Long"

    def test_smallint(self):
        assert fg.sql_type_to_java("smallint") == "Integer"

    def test_serial(self):
        assert fg.sql_type_to_java("serial") == "Integer"

    def test_bigserial(self):
        assert fg.sql_type_to_java("bigserial") == "Long"

    # ── Decimal types ──
    def test_numeric(self):
        assert fg.sql_type_to_java("numeric") == "java.math.BigDecimal"

    def test_decimal(self):
        assert fg.sql_type_to_java("decimal") == "java.math.BigDecimal"

    # ── Float types ──
    def test_real(self):
        assert fg.sql_type_to_java("real") == "Float"

    def test_float8(self):
        assert fg.sql_type_to_java("float8") == "Double"

    def test_double_precision(self):
        assert fg.sql_type_to_java("double precision") == "Double"

    # ── Boolean ──
    def test_boolean(self):
        assert fg.sql_type_to_java("boolean") == "Boolean"

    def test_bool(self):
        assert fg.sql_type_to_java("bool") == "Boolean"

    # ── Date/Time types ──
    def test_date(self):
        assert fg.sql_type_to_java("date") == "java.sql.Date"

    def test_timestamp(self):
        assert fg.sql_type_to_java("timestamp") == "java.sql.Timestamp"

    def test_timestamptz(self):
        assert fg.sql_type_to_java("timestamp with time zone") == "java.sql.Timestamp"

    def test_time(self):
        assert fg.sql_type_to_java("time") == "java.sql.Time"

    # ── Binary ──
    def test_bytea(self):
        assert fg.sql_type_to_java("bytea") == "byte[]"

    def test_blob(self):
        assert fg.sql_type_to_java("blob") == "byte[]"

    # ── JSON types ──
    def test_json(self):
        assert fg.sql_type_to_java("json") == "String"

    def test_jsonb(self):
        assert fg.sql_type_to_java("jsonb") == "String"

    # ── Edge cases ──
    def test_none_returns_object(self):
        assert fg.sql_type_to_java(None) == "Object"

    def test_empty_string_returns_object(self):
        assert fg.sql_type_to_java("") == "Object"

    def test_unknown_type_returns_map(self):
        assert fg.sql_type_to_java("unknown_foo_type") == "Map<String, Object>"

    def test_case_insensitive(self):
        assert fg.sql_type_to_java("VARCHAR") == "String"
        assert fg.sql_type_to_java("BigInt") == "Long"

    def test_type_with_precision_stripped(self):
        assert fg.sql_type_to_java("numeric(10,2)") == "java.math.BigDecimal"
        assert fg.sql_type_to_java("varchar(255)") == "String"

    # ── Array types ──
    def test_text_array(self):
        assert fg.sql_type_to_java("text[]") == "java.util.List<String>"

    def test_float8_array(self):
        assert fg.sql_type_to_java("float8[]") == "java.util.List<Double>"

    # ── Dict types (PercentType, RefCursor, etc.) ──
    def test_percent_type_dict(self):
        result = fg.sql_type_to_java({"PercentType": {"table": "orders", "column": "order_id"}})
        # order_id → varchar (id heuristic removed in #47/#49, safe default)
        assert result == "String"

    def test_percent_row_type_dict(self):
        result = fg.sql_type_to_java({"PercentRowType": {"table": "orders"}})
        assert result == "Map<String, Object>"

    def test_record_dict(self):
        result = fg.sql_type_to_java({"Record": {}})
        assert result == "Map<String, Object>"

    def test_refcursor_dict(self):
        result = fg.sql_type_to_java({"RefCursor": {}})
        assert result == "List<Map<String, Object>>"

    # ── %TYPE string syntax ──
    def test_percent_type_string(self):
        result = fg.sql_type_to_java("orders.order_id%type")
        assert result == "String"  # #47/#49: heuristic removed, safe default

    def test_percent_type_string_with_override(self):
        fg.TYPE_OVERRIDES[("orders", "status")] = "varchar"
        result = fg.sql_type_to_java("orders.status%type")
        assert result == "String"

    # ── TABLE return type ──
    def test_table_type(self):
        result = fg.sql_type_to_java("table")
        assert result == "java.util.List<java.util.Map<String, Object>>"

    # ── Function modifiers stripped ──
    def test_deterministic_stripped(self):
        result = fg.sql_type_to_java("integer deterministic")
        assert result == "Integer"

    def test_immutable_stripped(self):
        result = fg.sql_type_to_java("varchar immutable")
        assert result == "String"

    # ── LANGUAGE clause stripped (issue #79) ──
    def test_language_clause_stripped(self):
        assert fg.sql_type_to_java("numeric language plpgsql") == "java.math.BigDecimal"
        assert fg.sql_type_to_java("integer language plpgsql") == "Integer"
        assert fg.sql_type_to_java("boolean language plpgsql") == "Boolean"
        assert fg.sql_type_to_java("date language sql immutable strict") == "java.sql.Date"

    def test_language_clause_case_insensitive(self):
        assert fg.sql_type_to_java("NUMERIC LANGUAGE PLPGSQL") == "java.math.BigDecimal"

    # ── timestamptz / timestampz aliases (issue #83/#84) ──
    def test_timestamptz_short(self):
        assert fg.sql_type_to_java("timestamptz") == "java.sql.Timestamp"

    def test_timestampz(self):
        assert fg.sql_type_to_java("timestampz") == "java.sql.Timestamp"


class TestSqlTypeToJdbc:
    """Test sql_type_to_jdbc() — SQL → MyBatis JdbcType mapping."""

    def test_varchar(self):
        assert fg.sql_type_to_jdbc("varchar") == "VARCHAR"

    def test_bigint(self):
        assert fg.sql_type_to_jdbc("bigint") == "BIGINT"

    def test_integer(self):
        assert fg.sql_type_to_jdbc("integer") == "INTEGER"

    def test_numeric(self):
        assert fg.sql_type_to_jdbc("numeric") == "NUMERIC"

    def test_timestamp(self):
        assert fg.sql_type_to_jdbc("timestamp") == "TIMESTAMP"

    def test_date(self):
        assert fg.sql_type_to_jdbc("date") == "DATE"

    def test_timestamptz(self):
        assert fg.sql_type_to_jdbc("timestamptz") == "TIMESTAMP"

    def test_timestampz(self):
        assert fg.sql_type_to_jdbc("timestampz") == "TIMESTAMP"

    def test_none_returns_none(self):
        assert fg.sql_type_to_jdbc(None) is None

    def test_unknown_returns_none(self):
        assert fg.sql_type_to_jdbc("weird_type") is None

    def test_case_insensitive(self):
        assert fg.sql_type_to_jdbc("VARCHAR") == "VARCHAR"

    def test_percent_type_dict(self):
        result = fg.sql_type_to_jdbc({"PercentType": {"table": "t", "column": "name"}})
        assert result == "VARCHAR"  # "name" heuristic → varchar

    def test_refcursor_returns_none(self):
        result = fg.sql_type_to_jdbc({"RefCursor": {}})
        assert result is None


class TestJavaTypeToJdbc:
    """Test java_type_to_jdbc() — reverse mapping."""

    def test_string(self):
        assert fg.java_type_to_jdbc("String") == "VARCHAR"

    def test_long(self):
        assert fg.java_type_to_jdbc("Long") == "BIGINT"

    def test_integer(self):
        assert fg.java_type_to_jdbc("Integer") == "INTEGER"

    def test_boolean(self):
        assert fg.java_type_to_jdbc("Boolean") == "BOOLEAN"

    def test_bigdecimal(self):
        assert fg.java_type_to_jdbc("java.math.BigDecimal") == "NUMERIC"

    def test_timestamp(self):
        assert fg.java_type_to_jdbc("java.sql.Timestamp") == "TIMESTAMP"

    def test_none_returns_none(self):
        assert fg.java_type_to_jdbc(None) is None

    def test_empty_returns_none(self):
        assert fg.java_type_to_jdbc("") is None

    def test_list_returns_none(self):
        assert fg.java_type_to_jdbc("List<String>") is None

    def test_map_returns_none(self):
        assert fg.java_type_to_jdbc("Map<String, Object>") is None


class TestIsSimpleJavaType:
    """Test is_simple_java_type()."""

    def test_string(self):
        assert fg.is_simple_java_type("String") is True

    def test_long(self):
        assert fg.is_simple_java_type("Long") is True

    def test_bigdecimal(self):
        assert fg.is_simple_java_type("java.math.BigDecimal") is False

    def test_list(self):
        assert fg.is_simple_java_type("List<String>") is False

    def test_map(self):
        assert fg.is_simple_java_type("Map<String, Object>") is False


class TestInferTypeFromColumnName:
    """Test _infer_type_from_column_name() — column name → SQL type heuristics."""

    def test_name_suffix(self):
        assert fg._infer_type_from_column_name("user_name") == "varchar"

    def test_id_suffix(self):
        assert fg._infer_type_from_column_name("order_id") == "varchar"

    def test_amount_suffix(self):
        assert fg._infer_type_from_column_name("total_amount") == "numeric"

    def test_date_suffix(self):
        assert fg._infer_type_from_column_name("create_date") == "timestamp"

    def test_flag_suffix(self):
        assert fg._infer_type_from_column_name("is_active_flag") == "varchar"

    def test_status_suffix(self):
        assert fg._infer_type_from_column_name("order_status") == "varchar"

    def test_unknown_returns_varchar(self):
        assert fg._infer_type_from_column_name("xyz") == "varchar"

    def test_num_in_name_returns_varchar(self):
        assert fg._infer_type_from_column_name("row_num") == "varchar"

    def test_no_suffix_returns_varchar(self):
        assert fg._infer_type_from_column_name("value") == "varchar"


class TestResolveImport:
    """Test _resolve_import() — Java type → import statement."""

    def test_bigdecimal(self):
        result = fg._resolve_import("java.math.BigDecimal")
        assert result == "import java.math.BigDecimal;"

    def test_timestamp(self):
        result = fg._resolve_import("java.sql.Timestamp")
        assert result == "import java.sql.Timestamp;"

    def test_string_no_import(self):
        assert fg._resolve_import("String") is None

    def test_long_no_import(self):
        assert fg._resolve_import("Long") is None
