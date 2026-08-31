"""Tests for _coerce_type() — unified type coercion engine."""

import converter.flux_gauss as fg


class TestNormalizeType:
    """Test _normalize_type()."""

    def test_primitive_to_boxed(self):
        assert fg._normalize_type("int") == "Integer"

    def test_long_primitive(self):
        assert fg._normalize_type("long") == "Long"

    def test_double_primitive(self):
        assert fg._normalize_type("double") == "Double"

    def test_float_primitive(self):
        assert fg._normalize_type("float") == "Float"

    def test_boolean_primitive(self):
        assert fg._normalize_type("boolean") == "Boolean"

    def test_boxed_unchanged(self):
        assert fg._normalize_type("Integer") == "Integer"

    def test_bigdecimal_unchanged(self):
        assert fg._normalize_type("java.math.BigDecimal") == "java.math.BigDecimal"

    def test_string_unchanged(self):
        assert fg._normalize_type("String") == "String"

    def test_empty_returns_empty(self):
        assert fg._normalize_type("") == ""

    def test_none_returns_empty(self):
        assert fg._normalize_type(None) == ""


class TestIsNumericType:
    """Test _is_numeric_type()."""

    def test_integer(self):
        assert fg._is_numeric_type("Integer") is True

    def test_long(self):
        assert fg._is_numeric_type("Long") is True

    def test_double(self):
        assert fg._is_numeric_type("Double") is True

    def test_float(self):
        assert fg._is_numeric_type("Float") is True

    def test_bigdecimal(self):
        assert fg._is_numeric_type("java.math.BigDecimal") is True

    def test_string(self):
        assert fg._is_numeric_type("String") is False

    def test_boolean(self):
        assert fg._is_numeric_type("Boolean") is False

    def test_int_primitive(self):
        assert fg._is_numeric_type("int") is True

    def test_long_primitive(self):
        assert fg._is_numeric_type("long") is True


class TestNeedsCoercion:
    """Test _needs_coercion()."""

    def test_same_type_false(self):
        assert fg._needs_coercion("Integer", "Integer") is False

    def test_primitive_boxed_same_false(self):
        assert fg._needs_coercion("int", "Integer") is False

    def test_integer_long_true(self):
        assert fg._needs_coercion("Integer", "Long") is True

    def test_object_source_false(self):
        assert fg._needs_coercion("Object", "Integer") is False

    def test_object_target_false(self):
        assert fg._needs_coercion("Integer", "Object") is False

    def test_empty_false(self):
        assert fg._needs_coercion("Integer", "") is False

    def test_none_false(self):
        assert fg._needs_coercion(None, "Integer") is False

    def test_map_source_false(self):
        assert fg._needs_coercion("Map<String, Object>", "Integer") is False

    def test_list_types_false(self):
        assert fg._needs_coercion("List<String>", "String") is False

    def test_java_sql_date_false(self):
        assert fg._needs_coercion("java.sql.Date", "Long") is False

    def test_string_bigdecimal_true(self):
        assert fg._needs_coercion("String", "java.math.BigDecimal") is True


class TestCoerceTypeNumeric:
    """Numeric type to numeric type coercions."""

    def test_integer_to_long(self):
        assert fg._coerce_type("countVar", "Integer", "Long") == "countVar.longValue()"

    def test_integer_to_double(self):
        assert fg._coerce_type("countVar", "Integer", "Double") == "countVar.doubleValue()"

    def test_long_to_integer(self):
        assert fg._coerce_type("idVar", "Long", "Integer") == "idVar.intValue()"

    def test_long_to_double(self):
        assert fg._coerce_type("idVar", "Long", "Double") == "idVar.doubleValue()"

    def test_double_to_integer(self):
        assert fg._coerce_type("rateVar", "Double", "Integer") == "rateVar.intValue()"

    def test_double_to_long(self):
        assert fg._coerce_type("rateVar", "Double", "Long") == "rateVar.longValue()"

    def test_integer_to_bigdecimal(self):
        assert (
            fg._coerce_type("countVar", "Integer", "java.math.BigDecimal") == "java.math.BigDecimal.valueOf(countVar)"
        )

    def test_long_to_bigdecimal(self):
        assert fg._coerce_type("idVar", "Long", "java.math.BigDecimal") == "java.math.BigDecimal.valueOf(idVar)"

    def test_double_to_bigdecimal(self):
        assert fg._coerce_type("rateVar", "Double", "java.math.BigDecimal") == "java.math.BigDecimal.valueOf(rateVar)"

    def test_bigdecimal_to_integer(self):
        assert fg._coerce_type("amountVar", "java.math.BigDecimal", "Integer") == "amountVar.intValue()"

    def test_bigdecimal_to_long(self):
        assert fg._coerce_type("amountVar", "java.math.BigDecimal", "Long") == "amountVar.longValue()"

    def test_bigdecimal_to_double(self):
        assert fg._coerce_type("amountVar", "java.math.BigDecimal", "Double") == "amountVar.doubleValue()"


class TestCoerceTypeString:
    """String to/from numeric type coercions."""

    def test_integer_to_string(self):
        assert fg._coerce_type("countVar", "Integer", "String") == "String.valueOf(countVar)"

    def test_long_to_string(self):
        assert fg._coerce_type("idVar", "Long", "String") == "String.valueOf(idVar)"

    def test_bigdecimal_to_string(self):
        assert fg._coerce_type("amountVar", "java.math.BigDecimal", "String") == "amountVar.toString()"

    def test_double_to_string(self):
        assert fg._coerce_type("rateVar", "Double", "String") == "String.valueOf(rateVar)"

    def test_string_to_integer(self):
        assert fg._coerce_type("sVar", "String", "Integer") == "Integer.parseInt(sVar)"

    def test_string_to_long(self):
        assert fg._coerce_type("sVar", "String", "Long") == "Long.parseLong(sVar)"

    def test_string_to_bigdecimal(self):
        assert fg._coerce_type("sVar", "String", "java.math.BigDecimal") == "new java.math.BigDecimal(sVar)"

    def test_string_to_double(self):
        assert fg._coerce_type("sVar", "String", "Double") == "Double.parseDouble(sVar)"


class TestCoerceTypeBoolean:
    """Boolean to/from numeric type coercions."""

    def test_integer_to_boolean(self):
        assert fg._coerce_type("flagVar", "Integer", "Boolean") == "(flagVar != 0)"

    def test_boolean_to_integer(self):
        assert fg._coerce_type("flagVar", "Boolean", "Integer") == "(flagVar ? 1 : 0)"

    def test_long_to_boolean(self):
        assert fg._coerce_type("flagVar", "Long", "Boolean") == "(flagVar != 0L)"

    def test_boolean_to_long(self):
        assert fg._coerce_type("flagVar", "Boolean", "Long") == "(flagVar ? 1L : 0L)"


class TestCoerceTypeIdentity:
    """Same type or no-coercion scenarios return expr unchanged."""

    def test_same_type_returns_expr(self):
        assert fg._coerce_type("x", "Integer", "Integer") == "x"

    def test_object_source_returns_expr(self):
        assert fg._coerce_type("x", "Object", "Integer") == "x"

    def test_object_target_returns_expr(self):
        assert fg._coerce_type("x", "Integer", "Object") == "x"

    def test_none_types_returns_expr(self):
        assert fg._coerce_type("x", "Integer", "") == "x"

    def test_map_source_returns_expr(self):
        assert fg._coerce_type("x", "Map<String, Object>", "Integer") == "x"

    def test_primitive_to_boxed(self):
        assert fg._coerce_type("x", "int", "Integer") == "x"

    def test_boxed_to_primitive(self):
        assert fg._coerce_type("x", "Integer", "int") == "x"


class TestAssignmentTypeCoercion:
    """Test type coercion during variable assignment in _process_assignment."""

    def _make_proc(self, local_vars=None, params=None):
        """Create a minimal ProcedureInfo for testing."""
        proc = fg.ProcedureInfo(
            name="test_pkg.test_proc",
            package="test_pkg",
            proc_name="test_proc",
            is_function=False,
            return_type=None,
            parameters=params or [],
            body={},
            sql_text="",
        )
        if local_vars:
            proc.local_vars = local_vars
        return proc

    def test_assign_integer_to_long_var(self):
        """v_long := v_integer -> v_long = vInteger.longValue()"""
        proc = self._make_proc(local_vars={"v_integer": "Integer", "v_long": "Long"})
        assign_data = {
            "target": {"PlVariable": ["v_long"]},
            "expression": {"PlVariable": ["v_integer"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any(".longValue()" in line and "vLong" in line for line in proc.java_logic_lines), (
            f"Expected .longValue() coercion, got: {proc.java_logic_lines}"
        )

    def test_assign_integer_to_string_var(self):
        """v_str := v_integer -> v_str = String.valueOf(vInteger)"""
        proc = self._make_proc(local_vars={"v_integer": "Integer", "v_str": "String"})
        assign_data = {
            "target": {"PlVariable": ["v_str"]},
            "expression": {"PlVariable": ["v_integer"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("String.valueOf(" in line and "vInteger" in line for line in proc.java_logic_lines), (
            f"Expected String.valueOf() coercion, got: {proc.java_logic_lines}"
        )

    def test_assign_string_to_integer_var(self):
        """v_int := v_str -> v_int = Integer.parseInt(vStr)"""
        proc = self._make_proc(local_vars={"v_str": "String", "v_int": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_int"]},
            "expression": {"PlVariable": ["v_str"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("Integer.parseInt(" in line for line in proc.java_logic_lines), (
            f"Expected Integer.parseInt() coercion, got: {proc.java_logic_lines}"
        )

    def test_assign_long_to_integer_var(self):
        """v_int := v_long -> v_int = vLong.intValue()"""
        proc = self._make_proc(local_vars={"v_long": "Long", "v_int": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_int"]},
            "expression": {"PlVariable": ["v_long"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any(".intValue()" in line and "vLong" in line for line in proc.java_logic_lines), (
            f"Expected .intValue() coercion, got: {proc.java_logic_lines}"
        )

    def test_assign_same_type_no_coercion(self):
        """v_a := v_b (both Integer) -> no coercion"""
        proc = self._make_proc(local_vars={"v_a": "Integer", "v_b": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_a"]},
            "expression": {"PlVariable": ["v_b"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("vA = vB;" in line for line in proc.java_logic_lines), (
            f"Expected no coercion for same type, got: {proc.java_logic_lines}"
        )


class TestComparisonTypeCoercion:
    """Test type coercion during comparison operations in _expr_to_java."""

    def _make_proc(self, local_vars=None):
        proc = fg.ProcedureInfo(
            name="test_pkg.test_proc",
            package="test_pkg",
            proc_name="test_proc",
            is_function=False,
            return_type=None,
            parameters=[],
            body={},
            sql_text="",
        )
        if local_vars:
            proc.local_vars = local_vars
        return proc

    def test_compare_integer_vs_long(self):
        """v_int = v_long -> coerce to Long, use compareTo"""
        proc = self._make_proc(local_vars={"v_int": "Integer", "v_long": "Long"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_int"]}, "op": "=", "right": {"PlVariable": ["v_long"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "longValue()" in result or "compareTo" in result, f"Expected type coercion in comparison, got: {result}"

    def test_compare_long_vs_bigdecimal(self):
        """v_long = v_bd -> coerce to BigDecimal compareTo"""
        proc = self._make_proc(local_vars={"v_long": "Long", "v_bd": "java.math.BigDecimal"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_long"]}, "op": "=", "right": {"PlVariable": ["v_bd"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "compareTo" in result, f"Expected compareTo for BigDecimal comparison, got: {result}"

    def test_compare_same_type_no_extra_coercion(self):
        """v_a = v_b (both Integer) -> simple =="""
        proc = self._make_proc(local_vars={"v_a": "Integer", "v_b": "Integer"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_a"]}, "op": "=", "right": {"PlVariable": ["v_b"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "==" in result, f"Expected simple == comparison, got: {result}"

    def test_compare_integer_vs_double(self):
        """v_int > v_dbl -> coerce to Double comparison"""
        proc = self._make_proc(local_vars={"v_int": "Integer", "v_dbl": "Double"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_int"]}, "op": ">", "right": {"PlVariable": ["v_dbl"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "doubleValue()" in result or "Double.compare" in result, (
            f"Expected type coercion for Integer vs Double, got: {result}"
        )


class TestVarDeclDefaultCoercion:
    """Test type coercion for local variable declaration default values."""

    def test_default_integer_to_long_var(self):
        """v_long BIGINT := 42 -> default coerced to Long.valueOf(42)"""
        var_type = fg.sql_type_to_java("bigint")
        default_expr = {"Literal": {"Integer": 42}}
        default_java = fg._expr_to_java(default_expr, None)
        default_inferred = fg._infer_expr_type(default_expr, None)
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        assert default_java == "Long.valueOf(42)", f"Expected Long.valueOf(42), got: {default_java}"

    def test_default_string_to_integer_var(self):
        """v_int INTEGER := '100' -> default coerced to Integer.parseInt("100")"""
        var_type = fg.sql_type_to_java("integer")
        default_expr = {"Literal": {"String": "100"}}
        default_java = fg._expr_to_java(default_expr, None)
        default_inferred = fg._infer_expr_type(default_expr, None)
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        assert default_java == 'Integer.parseInt("100")', f"Expected Integer.parseInt coercion, got: {default_java}"

    def test_default_same_type_no_coercion(self):
        """v_str VARCHAR := 'hello' -> no coercion needed"""
        var_type = fg.sql_type_to_java("varchar")
        default_expr = {"Literal": {"String": "hello"}}
        default_java = fg._expr_to_java(default_expr, None)
        default_inferred = fg._infer_expr_type(default_expr, None)
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        assert default_java == '"hello"', f"Expected no coercion for same type, got: {default_java}"

    def test_default_integer_to_bigdecimal_var(self):
        """v_amount NUMERIC := 100 -> default coerced to BigDecimal.valueOf(100)"""
        var_type = fg.sql_type_to_java("numeric")
        default_expr = {"Literal": {"Integer": 100}}
        default_java = fg._expr_to_java(default_expr, None)
        default_inferred = fg._infer_expr_type(default_expr, None)
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        assert "BigDecimal.valueOf(100)" in default_java, f"Expected BigDecimal.valueOf coercion, got: {default_java}"
