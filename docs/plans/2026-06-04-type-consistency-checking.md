# 类型一致性检查与强转 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 PL/pgSQL → Java 转换过程中，对变量声明初始值、变量赋值、变量间赋值、等值/不等值比较四种场景，系统性检测类型不一致并自动插入 Java 类型强转代码。

**Architecture:** 三层设计 — (1) 类型兼容性矩阵 + 统一强转函数 `_coerce_type(expr, source_type, target_type)`，(2) 在 `_process_assignment` 和 `analyze_procedure` 的变量解析阶段调用统一强转，(3) 在 `_expr_to_java` 的 BinaryOp 比较分支中增加类型对齐逻辑。所有改动限定在 `converter/flux_gauss.py` 和 `tests/test_type_coercion.py`。

**Tech Stack:** Python 3.9+ / pytest

---

## 现有类型处理现状

当前已有**分散**的类型转换逻辑：

| 位置 | 覆盖场景 | 问题 |
|---|---|---|
| `_coerce_java_arg()` L8354 | 函数调用参数传递 | 仅覆盖空串→0、数值→BigDecimal、Map.get()→类型 |
| `_safe_map_cast()` L4353 | Map.get() 结果转换 | 仅覆盖 Map 场景 |
| `_emit_assignment()` L4384 | OUT 参数赋值 | 仅检查 OUT 参数的 String/Long/Integer/BigDecimal |
| `_process_assignment()` L4643-4682 | 普通变量赋值 | 仅覆盖 BigDecimal/String/Map.get() 三条路径 |
| `_expr_to_java` BinaryOp L8630-8715 | 比较运算 | 仅覆盖 BigDecimal/String/Long 三种比较 |

**缺失场景：**
- `Integer ← Long` / `Long ← Integer`
- `Integer ← String` / `Long ← String`（非 OUT 参数）
- `Integer ← Double` / `Long ← Double`
- `Boolean ← Integer` / `Integer ← Boolean`
- `BigDecimal ← Integer/Long/Double`（非字面量、非 Map.get() 场景）
- 比较运算中 `Integer vs String`、`Integer vs BigDecimal`、`Boolean vs Integer`
- 变量声明初始值与声明类型不匹配

---

## Task 1: 类型兼容性矩阵与统一强转函数

**Files:**
- Create: `tests/test_type_coercion.py`
- Modify: `converter/flux_gauss.py` (约 L8409 后，`_coerce_java_arg` 之后)

**Step 1: 编写 `_coerce_type()` 的失败测试**

```python
# tests/test_type_coercion.py
"""Tests for _coerce_type() — unified type coercion engine."""
import converter.flux_gauss as fg


class TestCoerceTypeNumeric:
    """数值类型之间的强转。"""

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
        assert fg._coerce_type("countVar", "Integer", "java.math.BigDecimal") == "java.math.BigDecimal.valueOf(countVar)"

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
    """String 与数值类型之间的强转。"""

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
    """Boolean 与数值类型之间的强转。"""

    def test_integer_to_boolean(self):
        assert fg._coerce_type("flagVar", "Integer", "Boolean") == "(flagVar != 0)"

    def test_boolean_to_integer(self):
        assert fg._coerce_type("flagVar", "Boolean", "Integer") == "(flagVar ? 1 : 0)"

    def test_long_to_boolean(self):
        assert fg._coerce_type("flagVar", "Long", "Boolean") == "(flagVar != 0L)"


class TestCoerceTypeIdentity:
    """相同类型或无需强转时返回原表达式。"""

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
```

**Step 2: 运行测试确认失败**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py -v
```

预期：全部 FAIL（`AttributeError: module has no attribute '_coerce_type'`）

**Step 3: 实现 `_coerce_type()` 和 `_needs_coercion()`**

在 `converter/flux_gauss.py` 的 `_coerce_java_arg()` 函数之后（约 L8410）插入：

```python
# ── Unified Type Coercion ─────────────────────────────────────

# 类型规范化：将 primitive/boxed 统一为 canonical 形式用于比较
_CANONICAL_TYPE = {
    "int": "Integer", "long": "Long", "double": "Double",
    "float": "Float", "boolean": "Boolean", "short": "Short",
    "byte": "Byte", "char": "Character",
}


def _normalize_type(java_type: str) -> str:
    """Normalize a Java type to its canonical form for comparison.
    
    Examples: "int" → "Integer", "long" → "Long", "java.math.BigDecimal" → "BigDecimal"
    """
    if not java_type:
        return ""
    return _CANONICAL_TYPE.get(java_type, java_type)


def _is_numeric_type(java_type: str) -> bool:
    """Check if a Java type is a numeric type (Integer, Long, Double, Float, BigDecimal, etc.)."""
    t = _normalize_type(java_type)
    return t in ("Integer", "Long", "Double", "Float", "java.math.BigDecimal", "Short", "Byte")


def _needs_coercion(source_type: str, target_type: str) -> bool:
    """Check if a type conversion is needed from source to target.
    
    Returns True if source and target are different types that require explicit conversion.
    Returns False for: same types, Object involvement, Map<String, Object> involvement,
    primitive↔boxed pairs, unknown types.
    """
    if not source_type or not target_type:
        return False
    
    src = _normalize_type(source_type)
    tgt = _normalize_type(target_type)
    
    # Same type (after normalization) — no coercion needed
    if src == tgt:
        return False
    
    # Object / Map<String, Object> — no coercion possible/needed
    if src in ("Object", "Map<String, Object>", "") or tgt in ("Object", "Map<String, Object>", ""):
        return False
    
    # List types — no generic coercion
    if src.startswith("List<") or tgt.startswith("List<"):
        return False
    
    # java.sql.Date / Timestamp — no numeric coercion
    if src.startswith("java.sql.") or tgt.startswith("java.sql."):
        return False
    
    return True


def _coerce_type(expr: str, source_type: str, target_type: str) -> str:
    """Coerce a Java expression from source_type to target_type.
    
    Returns the original expr if no coercion is needed or possible.
    Uses _needs_coercion() to determine if coercion is applicable.
    
    Conversion rules:
    - Numeric → Numeric: use .xxxValue() or BigDecimal.valueOf()
    - Numeric → String: String.valueOf() (BigDecimal uses .toString())
    - String → Numeric: Xxx.parseXxx() (BigDecimal uses new BigDecimal())
    - Integer/Long → Boolean: (expr != 0) or (expr != 0L)
    - Boolean → Integer: (expr ? 1 : 0)
    """
    if not _needs_coercion(source_type, target_type):
        return expr
    
    src = _normalize_type(source_type)
    tgt = _normalize_type(target_type)
    
    # Numeric to numeric conversions
    if _is_numeric_type(src) and _is_numeric_type(tgt):
        if tgt == "Integer":
            return f"{expr}.intValue()"
        if tgt == "Long":
            return f"{expr}.longValue()"
        if tgt == "Double":
            return f"{expr}.doubleValue()"
        if tgt == "Float":
            return f"{expr}.floatValue()"
        if tgt == "java.math.BigDecimal":
            return f"java.math.BigDecimal.valueOf({expr})"
        # BigDecimal source → numeric target
        if src == "java.math.BigDecimal":
            if tgt == "Integer":
                return f"{expr}.intValue()"
            if tgt == "Long":
                return f"{expr}.longValue()"
            if tgt == "Double":
                return f"{expr}.doubleValue()"
            if tgt == "Float":
                return f"{expr}.floatValue()"
    
    # Numeric to String
    if _is_numeric_type(src) and tgt == "String":
        if src == "java.math.BigDecimal":
            return f"{expr}.toString()"
        return f"String.valueOf({expr})"
    
    # String to numeric
    if src == "String" and _is_numeric_type(tgt):
        if tgt == "Integer":
            return f"Integer.parseInt({expr})"
        if tgt == "Long":
            return f"Long.parseLong({expr})"
        if tgt == "Double":
            return f"Double.parseDouble({expr})"
        if tgt == "Float":
            return f"Float.parseFloat({expr})"
        if tgt == "java.math.BigDecimal":
            return f"new java.math.BigDecimal({expr})"
    
    # Integer/Long to Boolean
    if src in ("Integer", "Long") and tgt == "Boolean":
        suffix = "L" if src == "Long" else ""
        return f"({expr} != 0{suffix})"
    
    # Boolean to Integer
    if src == "Boolean" and tgt == "Integer":
        return f"({expr} ? 1 : 0)"
    
    # Boolean to Long
    if src == "Boolean" and tgt == "Long":
        return f"({expr} ? 1L : 0L)"
    
    # Fallback: no known coercion
    return expr
```

**Step 4: 运行测试确认通过**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py -v
```

预期：全部 PASS

**Step 5: Commit**

```bash
git add tests/test_type_coercion.py converter/flux_gauss.py
git commit -m "feat: add unified type coercion functions _coerce_type/_needs_coercion"
```

---

## Task 2: 赋值场景 — 增强 `_process_assignment`

**Files:**
- Modify: `tests/test_type_coercion.py` (新增测试类)
- Modify: `converter/flux_gauss.py` `_process_assignment()` (约 L4643-4692)

**核心改动逻辑：**

在 `_process_assignment()` 中，现有的 ad-hoc 类型检查（L4646-4682）之后，增加一个 **通用类型强转 fallback 路径**。现有逻辑优先（更精确），新逻辑作为兜底（覆盖遗漏的组合）。

**Step 1: 编写赋值场景的集成测试**

```python
# 在 tests/test_type_coercion.py 末尾追加

import pytest


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
        """v_long := v_integer → v_long = vInteger.longValue()"""
        proc = self._make_proc(local_vars={"v_integer": "Integer", "v_long": "Long"})
        assign_data = {
            "target": {"PlVariable": ["v_long"]},
            "expression": {"PlVariable": ["v_integer"]},
        }
        fg._process_assignment(assign_data, proc, {})
        # 应有 .longValue() 转换
        assert any(".longValue()" in line and "vLong" in line for line in proc.java_logic_lines), \
            f"Expected .longValue() coercion, got: {proc.java_logic_lines}"

    def test_assign_integer_to_string_var(self):
        """v_str := v_integer → v_str = String.valueOf(vInteger)"""
        proc = self._make_proc(local_vars={"v_integer": "Integer", "v_str": "String"})
        assign_data = {
            "target": {"PlVariable": ["v_str"]},
            "expression": {"PlVariable": ["v_integer"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("String.valueOf(" in line and "vInteger" in line for line in proc.java_logic_lines), \
            f"Expected String.valueOf() coercion, got: {proc.java_logic_lines}"

    def test_assign_string_to_integer_var(self):
        """v_int := v_str → v_int = Integer.parseInt(vStr)"""
        proc = self._make_proc(local_vars={"v_str": "String", "v_int": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_int"]},
            "expression": {"PlVariable": ["v_str"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("Integer.parseInt(" in line for line in proc.java_logic_lines), \
            f"Expected Integer.parseInt() coercion, got: {proc.java_logic_lines}"

    def test_assign_long_to_integer_var(self):
        """v_int := v_long → v_int = vLong.intValue()"""
        proc = self._make_proc(local_vars={"v_long": "Long", "v_int": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_int"]},
            "expression": {"PlVariable": ["v_long"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any(".intValue()" in line and "vLong" in line for line in proc.java_logic_lines), \
            f"Expected .intValue() coercion, got: {proc.java_logic_lines}"

    def test_assign_same_type_no_coercion(self):
        """v_a := v_b (both Integer) → no coercion"""
        proc = self._make_proc(local_vars={"v_a": "Integer", "v_b": "Integer"})
        assign_data = {
            "target": {"PlVariable": ["v_a"]},
            "expression": {"PlVariable": ["v_b"]},
        }
        fg._process_assignment(assign_data, proc, {})
        assert any("vA = vB;" in line for line in proc.java_logic_lines), \
            f"Expected no coercion for same type, got: {proc.java_logic_lines}"
```

**Step 2: 运行测试确认失败**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py::TestAssignmentTypeCoercion -v
```

预期：FAIL — 当前赋值路径不处理 Integer→Long 等组合

**Step 3: 修改 `_process_assignment()`**

在 `converter/flux_gauss.py` 的 `_process_assignment()` 函数中，现有类型处理逻辑（约 L4643-4682）之后、`_emit_assignment(proc, target, java_expr)` 调用之前（约 L4692），插入通用类型强转 fallback：

```python
    # ── General type coercion fallback ──
    # Existing ad-hoc checks above handle specific cases (BigDecimal, String, Map.get()).
    # This fallback covers remaining type mismatches using the unified coercion engine.
    if target_var_type and expr_type and _needs_coercion(expr_type, target_var_type):
        # Skip if already handled by existing logic above (check for type conversion patterns in expr)
        _already_coerced = any(pattern in java_expr for pattern in (
            "BigDecimal.valueOf(", "String.valueOf(", ".intValue()", ".longValue()",
            ".doubleValue()", "Integer.parseInt(", "Long.parseLong(", "Double.parseDouble(",
            ".toString()", "new java.math.BigDecimal(", "_safe_map_cast(",
            "(String) ", f"({target_var_type}) ",
        ))
        if not _already_coerced:
            java_expr = _coerce_type(java_expr, expr_type, target_var_type)
```

**插入位置：** 在 L4689（`if target_out and target_out.java_type == "String" and _is_numeric_literal(expression)`）之前。

**Step 4: 运行测试确认通过**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py -v
```

预期：全部 PASS

**Step 5: Commit**

```bash
git add tests/test_type_coercion.py converter/flux_gauss.py
git commit -m "feat: add general type coercion fallback in _process_assignment"
```

---

## Task 3: 比较场景 — 增强 `_expr_to_java` BinaryOp 分支

**Files:**
- Modify: `tests/test_type_coercion.py` (新增测试类)
- Modify: `converter/flux_gauss.py` `_expr_to_java()` (约 L8630-8715)

**核心改动逻辑：**

在 `_expr_to_java()` 的 BinaryOp 比较分支中，现有的 BigDecimal/String/Long 特殊处理之后，增加通用类型对齐。当比较两侧类型不一致时，将两侧提升到"公共类型"后再比较。

**类型提升优先级**（从低到高）：`Integer < Long < Double < BigDecimal`

**Step 1: 编写比较场景的测试**

```python
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
        """v_int = v_long → coerce left to Long, compare with .equals()"""
        proc = self._make_proc(local_vars={"v_int": "Integer", "v_long": "Long"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_int"]}, "op": "=", "right": {"PlVariable": ["v_long"]}}}
        result = fg._expr_to_java(expr, proc)
        # Should have .longValue() on the Integer side and compareTo or == comparison
        assert "longValue()" in result or "compareTo" in result, \
            f"Expected type coercion in comparison, got: {result}"

    def test_compare_integer_vs_string(self):
        """v_int = v_str → should coerce appropriately"""
        proc = self._make_proc(local_vars={"v_int": "Integer", "v_str": "String"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_int"]}, "op": "=", "right": {"PlVariable": ["v_str"]}}}
        result = fg._expr_to_java(expr, proc)
        # Should have parseInt or valueOf coercion
        assert "parseInt" in result or "valueOf" in result or "String.valueOf" in result, \
            f"Expected type coercion in comparison, got: {result}"

    def test_compare_long_vs_bigdecimal(self):
        """v_long = v_bd → should coerce to BigDecimal compareTo"""
        proc = self._make_proc(local_vars={"v_long": "Long", "v_bd": "java.math.BigDecimal"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_long"]}, "op": "=", "right": {"PlVariable": ["v_bd"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "compareTo" in result, f"Expected compareTo for BigDecimal comparison, got: {result}"
        assert "BigDecimal.valueOf" in result, f"Expected Long→BigDecimal coercion, got: {result}"

    def test_compare_same_type_no_coercion(self):
        """v_a = v_b (both Integer) → simple =="""
        proc = self._make_proc(local_vars={"v_a": "Integer", "v_b": "Integer"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_a"]}, "op": "=", "right": {"PlVariable": ["v_b"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "==" in result, f"Expected simple == comparison, got: {result}"
        assert "compareTo" not in result, f"Should not have compareTo for same type, got: {result}"

    def test_compare_integer_vs_double(self):
        """v_int > v_dbl → coerce to Double comparison"""
        proc = self._make_proc(local_vars={"v_int": "Integer", "v_dbl": "Double"})
        expr = {"BinaryOp": {"left": {"PlVariable": ["v_int"]}, "op": ">", "right": {"PlVariable": ["v_dbl"]}}}
        result = fg._expr_to_java(expr, proc)
        assert "doubleValue()" in result or "compareTo" in result, \
            f"Expected type coercion for Integer vs Double comparison, got: {result}"
```

**Step 2: 运行测试确认失败**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py::TestComparisonTypeCoercion -v
```

预期：部分 FAIL（现有代码不处理 Integer vs Long 等混合类型比较）

**Step 3: 修改 `_expr_to_java()` BinaryOp 分支**

在 `converter/flux_gauss.py` 的 `_expr_to_java()` 函数中，BinaryOp 比较运算的现有 Long 比较处理（约 L8703-8715）之后、BigDecimal 算术运算（约 L8717）之前，插入通用类型对齐：

```python
                # ── General type alignment for mixed-type comparisons ──
                # Determine common promotion type for numeric comparisons
                if op in (">", "<", ">=", "<=", "=", "<>"):
                    _NUMERIC_PRIORITY = {"Integer": 0, "Long": 1, "Float": 2, "Double": 3, "java.math.BigDecimal": 4}
                    _l_pri = _NUMERIC_PRIORITY.get(left_type, -1)
                    _r_pri = _NUMERIC_PRIORITY.get(right_type, -1)
                    
                    if _l_pri >= 0 and _r_pri >= 0 and _l_pri != _r_pri:
                        # Both numeric but different precision — promote to higher
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        
                        # Determine the common type
                        if _l_pri > _r_pri:
                            _common_type = left_type
                            right = _coerce_type(right, right_type, left_type)
                        else:
                            _common_type = right_type
                            left = _coerce_type(left, left_type, right_type)
                        
                        # Use compareTo for non-primitive comparison
                        if _common_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        elif _common_type == "Long":
                            if _is_numeric_literal(val.get("right")):
                                right = f"Long.valueOf({right})"
                            elif _is_numeric_literal(val.get("left")):
                                left = f"Long.valueOf({left})"
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        elif _common_type == "Double":
                            return f"Double.compare({left}, {right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"
                    
                    # String vs Numeric (non-BigDecimal, non-Map.get) comparison
                    if (_l_pri >= 0 and left_type != "Object") and right_type == "String" and ".get(" not in right:
                        # Coerce String to numeric for comparison
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        right = _coerce_type(right, "String", left_type)
                        if left_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"
                    if (_r_pri >= 0 and right_type != "Object") and left_type == "String" and ".get(" not in left:
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        left = _coerce_type(left, "String", right_type)
                        if right_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"
```

**插入位置：** 在 L8715（Long 比较的 return 之后）和 L8717（BigDecimal 算术运算 `if is_bd and op in ("+", "-", "*", "/")` 之前）之间。

**Step 4: 运行测试确认通过**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py::TestComparisonTypeCoercion -v
```

预期：全部 PASS

**Step 5: Commit**

```bash
git add tests/test_type_coercion.py converter/flux_gauss.py
git commit -m "feat: add general type alignment for mixed-type comparisons in BinaryOp"
```

---

## Task 4: 变量声明初始值 — 类型检查与修正

**Files:**
- Modify: `tests/test_type_coercion.py` (新增测试类)
- Modify: `converter/flux_gauss.py` `analyze_procedure()` 中变量解析部分 (约 L2293-2307)

**核心改动逻辑：**

在 `analyze_procedure()` 解析局部变量声明时（L2293-2307），检查 `default_java`（初始值的 Java 表达式）的推断类型与 `java_type`（变量声明类型）是否一致，不一致时通过 `_coerce_type()` 修正。

**Step 1: 编写变量声明初始值类型检查的测试**

```python
class TestVarDeclDefaultCoercion:
    """Test type coercion for variable declaration default values."""

    def _make_proc_for_decl(self):
        """Create a ProcedureInfo with empty local_vars."""
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
        return proc

    def test_default_integer_to_long_var(self):
        """v_long BIGINT := 42 → default should be Long.valueOf(42)"""
        proc = self._make_proc_for_decl()
        # Simulate what analyze_procedure does with variable declarations
        decl_data = {"name": "v_long", "data_type": "bigint", "default": {"Literal": {"Integer": 42}}}
        var_name = decl_data["name"]
        java_type = fg.sql_type_to_java(decl_data["data_type"])  # "Long"
        default_ast = decl_data["default"]
        default_java = fg._expr_to_java(default_ast, proc)  # "42"
        
        # Infer default type and coerce if needed
        default_inferred = fg._infer_expr_type(default_ast, proc)  # "Integer"
        if fg._needs_coercion(default_inferred, java_type):
            default_java = fg._coerce_type(default_java, default_inferred, java_type)
        
        assert "Long.valueOf(42)" == default_java or "42L" == default_java, \
            f"Expected Long coercion, got: {default_java}"

    def test_default_string_to_integer_var(self):
        """v_int INTEGER := '100' → default should be Integer.parseInt("100")"""
        proc = self._make_proc_for_decl()
        decl_data = {"name": "v_int", "data_type": "integer", "default": {"Literal": {"String": "100"}}}
        var_name = decl_data["name"]
        java_type = fg.sql_type_to_java(decl_data["data_type"])  # "Integer"
        default_ast = decl_data["default"]
        default_java = fg._expr_to_java(default_ast, proc)  # '"100"'
        
        default_inferred = fg._infer_expr_type(default_ast, proc)  # "String"
        if fg._needs_coercion(default_inferred, java_type):
            default_java = fg._coerce_type(default_java, default_inferred, java_type)
        
        assert 'Integer.parseInt("100")' == default_java, \
            f"Expected Integer.parseInt coercion, got: {default_java}"

    def test_default_same_type_no_coercion(self):
        """v_str VARCHAR := 'hello' → no coercion needed"""
        proc = self._make_proc_for_decl()
        decl_data = {"name": "v_str", "data_type": "varchar", "default": {"Literal": {"String": "hello"}}}
        java_type = fg.sql_type_to_java(decl_data["data_type"])  # "String"
        default_ast = decl_data["default"]
        default_java = fg._expr_to_java(default_ast, proc)  # '"hello"'
        
        default_inferred = fg._infer_expr_type(default_ast, proc)  # "String"
        if fg._needs_coercion(default_inferred, java_type):
            default_java = fg._coerce_type(default_java, default_inferred, java_type)
        
        assert '"hello"' == default_java, \
            f"Expected no coercion for same type, got: {default_java}"
```

**Step 2: 运行测试确认通过/失败**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py::TestVarDeclDefaultCoercion -v
```

预期：`test_default_same_type_no_coercion` PASS，其他可能 PASS 或 FAIL 取决于 `_coerce_type` 的实现

**Step 3: 修改 `analyze_procedure()` 中的变量声明解析**

在 `converter/flux_gauss.py` 的 `analyze_procedure()` 函数中，变量声明解析部分（约 L2294-2307），在 `proc.local_var_defaults[var_name] = default_java` 之前插入类型检查：

找到以下代码（约 L2294-2305）：
```python
                default_ast = decl_data.get("default")
                if default_ast is not None:
                    try:
                        default_java = _expr_to_java(default_ast, proc)
                        if java_type.startswith("java.util.List<"):
                            # ... existing List handling ...
                        proc.local_var_defaults[var_name] = default_java
                    except Exception:
                        pass
```

修改为：
```python
                default_ast = decl_data.get("default")
                if default_ast is not None:
                    try:
                        default_java = _expr_to_java(default_ast, proc)
                        if java_type.startswith("java.util.List<"):
                            # ... existing List handling (keep as-is) ...
                            pass  # (保持现有逻辑不变)
                        # Type-check: coerce default value if it doesn't match declared type
                        default_inferred = _infer_expr_type(default_ast, proc)
                        if _needs_coercion(default_inferred, java_type):
                            default_java = _coerce_type(default_java, default_inferred, java_type)
                        proc.local_var_defaults[var_name] = default_java
                    except Exception:
                        pass
```

**关键：** 需要将 List 类型的现有处理逻辑保留不变，只在 List 处理之后、赋值 `proc.local_var_defaults` 之前插入类型检查。需要精确读取 L2294-2307 的完整代码来编辑。

**Step 4: 运行测试确认通过**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py -v
```

**Step 5: Commit**

```bash
git add tests/test_type_coercion.py converter/flux_gauss.py
git commit -m "feat: add type checking for variable declaration default values"
```

---

## Task 5: 包变量和常量声明初始值 — 类型检查

**Files:**
- Modify: `tests/test_type_coercion.py` (新增测试类)
- Modify: `converter/flux_gauss.py` `extract_procedures()` 中包变量解析部分 (约 L1567-1572, L1609-1614)

**核心改动逻辑：**

与 Task 4 类似，但在包级变量（CreatePackage 和 CreatePackageBody 中的 Variable）解析时增加类型检查。包变量初始值存储在 `package_vars[var_name]["default"]` 中。

**Step 1: 编写包变量初始值类型检查的测试**

```python
class TestPackageVarDefaultCoercion:
    """Test type coercion for package variable default values."""

    def test_pkg_var_integer_default_to_long(self):
        """Package var: v_count BIGINT := 0 → default coerced to Long.valueOf(0)"""
        # Direct test of the coercion logic
        var_type = fg.sql_type_to_java("bigint")  # "Long"
        default_expr = {"Literal": {"Integer": 0}}
        default_java = fg._expr_to_java(default_expr, None)  # "0"
        default_inferred = fg._infer_expr_type(default_expr, None)  # "Integer"
        
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        
        assert "Long.valueOf(0)" == default_java or "0L" == default_java, \
            f"Expected Long coercion, got: {default_java}"

    def test_pkg_var_string_default_to_integer(self):
        """Package var: v_id INTEGER := '100' → default coerced to Integer.parseInt("100")"""
        var_type = fg.sql_type_to_java("integer")  # "Integer"
        default_expr = {"Literal": {"String": "100"}}
        default_java = fg._expr_to_java(default_expr, None)  # '"100"'
        default_inferred = fg._infer_expr_type(default_expr, None)  # "String"
        
        if fg._needs_coercion(default_inferred, var_type):
            default_java = fg._coerce_type(default_java, default_inferred, var_type)
        
        assert 'Integer.parseInt("100")' == default_java, \
            f"Expected Integer.parseInt coercion, got: {default_java}"
```

**Step 2: 运行测试确认**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py::TestPackageVarDefaultCoercion -v
```

**Step 3: 修改 `extract_procedures()` 中包变量解析**

在 `converter/flux_gauss.py` 的两处包变量解析中（L1570-1572 和 L1612-1614），在 `package_vars[var_name] = ...` 之前插入类型检查。

**位置 1** — `CreatePackage` 中的 Variable（约 L1570-1572）：
```python
# 现有代码:
                            default_expr = item_data.get("default")
                            default_val = _expr_to_java(default_expr, None) if default_expr else None
                            package_vars[var_name] = {"java_type": var_type, "default": default_val}
# 修改为:
                            default_expr = item_data.get("default")
                            default_val = _expr_to_java(default_expr, None) if default_expr else None
                            if default_val and default_expr is not None:
                                default_inferred = _infer_expr_type(default_expr, None)
                                if _needs_coercion(default_inferred, var_type):
                                    default_val = _coerce_type(default_val, default_inferred, var_type)
                            package_vars[var_name] = {"java_type": var_type, "default": default_val}
```

**位置 2** — `CreatePackageBody` 中的 Variable（约 L1612-1614）：
同样的改动。

**Step 4: 运行全部测试**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/test_type_coercion.py -v
```

**Step 5: Commit**

```bash
git add tests/test_type_coercion.py converter/flux_gauss.py
git commit -m "feat: add type checking for package variable default values"
```

---

## Task 6: 回归验证 — 全量测试 + 编译检查

**Files:**
- 不修改文件，只运行验证

**Step 1: 运行全量 Python 测试**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 -m pytest tests/ -v --tb=short
```

预期：所有现有测试保持 PASS，新增测试 PASS

**Step 2: 运行转换 + Maven 编译**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml && cd dest && mvn compile -q
```

预期：`mvn compile` 成功（BUILD SUCCESS），生成代码中包含正确的类型强转

**Step 3: 检查生成的 Java 代码**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java/dest
grep -rn "\.intValue()\|\.longValue()\|\.doubleValue()\|Integer\.parseInt\|Long\.parseLong\|String\.valueOf" src/main/java/ | head -30
```

预期：看到生成的 Java 代码中有适当的类型转换

**Step 4: 如果有编译错误，修复并重新测试**

典型问题：
- 强转后的类型与后续使用不匹配 → 调整 `_coerce_type()` 的返回值格式
- 双重强转（现有逻辑 + 新 fallback 都触发） → 调整 `_already_coerced` 检测列表

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: verify type coercion with full test suite and Maven compile"
```

---

## 实施顺序总结

| Task | 改动区域 | 依赖 | 风险 |
|---|---|---|---|
| 1 | `_coerce_type()` + `_needs_coercion()` 新函数 | 无 | 低（纯新增，不修改现有逻辑） |
| 2 | `_process_assignment()` 增强 | Task 1 | 中（可能影响赋值生成） |
| 3 | `_expr_to_java()` BinaryOp 增强 | Task 1 | 中（可能影响条件表达式） |
| 4 | `analyze_procedure()` 变量声明增强 | Task 1 | 低（只影响初始值） |
| 5 | `extract_procedures()` 包变量增强 | Task 1 | 低（只影响包变量初始值） |
| 6 | 回归验证 | Task 1-5 | N/A |

**Task 2 和 Task 3 是核心改动，需要特别注意与现有逻辑的兼容性。**

**Task 4 和 Task 5 相对独立且低风险，可以并行实施。**

---

## 注意事项

1. **不破坏现有逻辑**：新的 `_coerce_type()` fallback 只在现有 ad-hoc 检查未覆盖时才触发（通过 `_already_coerced` 检测）
2. **_infer_expr_type 的局限性**：类型推断可能返回 `Object`（无法确定类型），此时 `_needs_coercion()` 会返回 False，不进行强转——这是正确行为
3. **Map<String, Object> 场景**：现有的 `_safe_map_cast()` 处理了 Map.get() 的转换，新逻辑不会覆盖这些场景（通过 `_already_coerced` 检测中的 `_safe_map_cast(` 模式排除）
4. **类型规范化**：`_normalize_type()` 处理 primitive/boxed 等价性，避免 `int` vs `Integer` 被误判为类型不一致
