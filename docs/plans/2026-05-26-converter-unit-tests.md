# FluxGauss Converter 单元测试实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `converter/flux_gauss.py` 建立守护性单元测试套件，覆盖核心纯函数、数据类、AST 提取和关键转换逻辑，确保修改/重构时有回归保护。

**Architecture:** pytest 框架 + conftest.py 全局状态重置 + mock 辅助工厂。按纯函数→数据类→AST提取→集成回归四层递进。优先测试最关键的类型转换、命名、SQL 处理函数（273 个函数中约 80 个纯函数可直接测试，无需 mock）。

**Tech Stack:** Python 3.9+, pytest, pytest-mock（可选，初期不需要）

---

## 测试目录结构

```
tests/
├── conftest.py                  # 全局 fixture：状态重置、mock 工厂
├── test_type_conversion.py      # sql_type_to_java/jdbc, java_type_to_jdbc 等
├── test_naming.py               # snake_to_camel/pascal, _java_safe_identifier 等
├── test_sql_processing.py       # _split_sql_statements, _extract_comments_from_text 等
├── test_dataclasses.py          # Parameter, ProcedureInfo, PackageInfo 等
├── test_ast_extraction.py       # extract_parameters, extract_procedures, extract_comments 等
├── test_dml_analysis.py         # _extract_dml_target, _extract_table_names 等
└── test_expression.py           # _expr_to_java, _infer_expr_type, _coerce_java_arg 等
```

---

## Task 1: 测试基础设施搭建

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

### Step 1: 安装 pytest

```bash
pip3 install pytest
```

### Step 2: 创建 tests/__init__.py

空文件，标记 tests 为 Python 包。

### Step 3: 创建 tests/conftest.py

```python
"""
Shared fixtures for flux_gauss unit tests.

Key concern: flux_gauss.py has extensive module-level mutable state
(UNRESOLVED_CALLS, STUB_PROCEDURES, etc.) that MUST be reset between tests.
"""
import pytest
import sys
import os

# Add project root to path so we can import converter.flux_gauss
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import converter.flux_gauss as fg


# ── Global State Reset ──────────────────────────────────────────
# flux_gauss.py uses many module-level mutable containers.
# Every test must start with clean state.

@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset ALL module-level mutable state before each test."""
    # Tracking lists
    fg.UNRESOLVED_CALLS.clear()
    fg.STUB_PROCEDURES.clear()
    fg.UNSUPPORTED_FUNCTIONS.clear()
    fg.TODO_SUMMARY.clear()

    # Tracking dicts
    fg.STUB_REASONS.clear()
    fg._MISSING_OVERLOADS.clear()
    fg._PACKAGE_CONSTANTS.clear()
    fg._PACKAGE_VARIABLES.clear()
    fg._UDF_RETURN_TYPES.clear()
    fg._TABLE_DDL_SOURCE.clear()

    # TYPE_OVERRIDES is a config dict — save/restore to avoid test cross-contamination
    original_overrides = dict(fg.TYPE_OVERRIDES)
    yield
    fg.TYPE_OVERRIDES.clear()
    fg.TYPE_OVERRIDES.update(original_overrides)


# ── Mock Object Factories ──────────────────────────────────────

@pytest.fixture
def make_parameter():
    """Factory to create Parameter instances with sensible defaults."""
    def _make(name="p_test", java_type="String", sql_type="varchar", mode="IN"):
        return fg.Parameter(name=name, java_type=java_type, sql_type=sql_type, mode=mode)
    return _make


@pytest.fixture
def make_procedure():
    """Factory to create ProcedureInfo instances with minimal valid state."""
    def _make(
        name="pkg_test.proc_a",
        package="pkg_test",
        proc_name="proc_a",
        is_function=False,
        return_type=None,
        parameters=None,
        body=None,
        sql_text="BEGIN NULL; END;",
        **overrides,
    ):
        proc = fg.ProcedureInfo(
            name=name,
            package=package,
            proc_name=proc_name,
            is_function=is_function,
            return_type=return_type,
            parameters=parameters or [],
            body=body or {"Block": {"body": {"statements": []}}},
            sql_text=sql_text,
        )
        for k, v in overrides.items():
            setattr(proc, k, v)
        return proc
    return _make


@pytest.fixture
def make_package():
    """Factory to create PackageInfo instances."""
    def _make(package_name="pkg_test", procedures=None, **overrides):
        pkg = fg.PackageInfo(
            package_name=package_name,
            procedures=procedures or [],
        )
        for k, v in overrides.items():
            setattr(pkg, k, v)
        return pkg
    return _make


@pytest.fixture
def make_comment():
    """Factory to create CommentInfo instances."""
    def _make(text="-- test comment", line=1, end_line=1, column=0, comment_type="line"):
        return fg.CommentInfo(text=text, line=line, end_line=end_line, column=column, comment_type=comment_type)
    return _make
```

### Step 4: 验证 pytest 可以发现并运行

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
python3 -m pytest tests/ -v --collect-only
```

Expected: 能发现 conftest.py 中的 fixtures，无 import 错误。

---

## Task 2: 类型转换函数测试

**Files:**
- Create: `tests/test_type_conversion.py`

**测试目标函数:**
- `sql_type_to_java(sql_type)` — 核心类型映射，最关键
- `sql_type_to_jdbc(sql_type)` — JDBC 类型映射
- `java_type_to_jdbc(java_type)` — 反向映射
- `is_simple_java_type(java_type)` — 类型分类
- `_infer_type_from_column_name(column_name)` — 列名推断类型
- `_resolve_import(java_type)` — import 语句生成

### Step 1: 创建 tests/test_type_conversion.py

```python
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
        assert fg.sql_type_to_java("timestamptz") == "java.sql.Timestamp"

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
        assert fg.sql_type_to_java("") == "Map<String, Object>"

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
        # order_id → bigint (heuristic: "id" → bigint)
        assert result == "Long"

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
        assert result == "Long"  # "id" heuristic → bigint

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
        assert fg._infer_type_from_column_name("order_id") == "bigint"

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

    def test_num_in_name_returns_integer(self):
        assert fg._infer_type_from_column_name("row_num") == "integer"

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
```

### Step 2: 运行测试验证通过

```bash
python3 -m pytest tests/test_type_conversion.py -v
```

Expected: 全部通过。

---

## Task 3: 命名函数测试

**Files:**
- Create: `tests/test_naming.py`

**测试目标函数:**
- `snake_to_camel(s)` — snake_case → camelCase
- `snake_to_pascal(s)` — snake_case → PascalCase
- `package_to_classname(pkg_name)` — pkg_name → ClassName
- `java_method_name(proc_name)` — proc_name → methodName
- `_java_safe_identifier(s)` — Java 安全标识符
- `_custom_type_classname(sql_type_name)` — SQL type → Java class name

### Step 1: 创建 tests/test_naming.py

```python
"""
Tests for naming/identifier functions in converter/flux_gauss.py.

These pure functions are critical for correct Java code generation —
every class name, method name, and variable name passes through them.
"""
import pytest
import converter.flux_gauss as fg


class TestSnakeToCamel:
    """Test snake_to_camel() — snake_case → camelCase."""

    def test_simple(self):
        assert fg.snake_to_camel("create_order") == "createOrder"

    def test_single_word(self):
        assert fg.snake_to_camel("order") == "order"

    def test_three_words(self):
        assert fg.snake_to_camel("get_product_info") == "getProductInfo"

    def test_uppercase_input(self):
        assert fg.snake_to_camel("PKG_ORDER") == "pkgOrder"

    def test_empty_string(self):
        assert fg.snake_to_camel("") == "_"

    def test_already_camel(self):
        # No underscores → returns as-is (lowered)
        assert fg.snake_to_camel("createOrder") == "createorder"


class TestSnakeToPascal:
    """Test snake_to_pascal() — snake_case → PascalCase."""

    def test_simple(self):
        assert fg.snake_to_pascal("pkg_order") == "PkgOrder"

    def test_single_word(self):
        assert fg.snake_to_pascal("order") == "Order"

    def test_three_words(self):
        assert fg.snake_to_pascal("get_product_info") == "GetProductInfo"

    def test_empty_string(self):
        assert fg.snake_to_pascal("") == "_"


class TestPackageToClassname:
    """Test package_to_classname() — SQL package name → Java class name."""

    def test_pkg_prefix(self):
        assert fg.package_to_classname("pkg_order") == "Order"

    def test_pkg_prefix_uppercase(self):
        assert fg.package_to_classname("PKG_ORDER") == "Order"

    def test_pack_prefix(self):
        assert fg.package_to_classname("pack_log") == "Log"

    def test_no_prefix(self):
        assert fg.package_to_classname("order") == "Order"

    def test_complex(self):
        assert fg.package_to_classname("pkg_product") == "Product"


class TestJavaMethodName:
    """Test java_method_name() — SQL proc name → Java method name."""

    def test_simple(self):
        assert fg.java_method_name("create_order") == "createOrder"

    def test_getter(self):
        assert fg.java_method_name("get_product_info") == "getProductInfo"

    def test_batch(self):
        assert fg.java_method_name("batch_create_orders") == "batchCreateOrders"


class TestJavaSafeIdentifier:
    """Test _java_safe_identifier() — sanitize for Java."""

    def test_normal_string(self):
        assert fg._java_safe_identifier("order_id") == "order_id"

    def test_starts_with_digit(self):
        assert fg._java_safe_identifier("123abc") == "_123abc"

    def test_java_keyword(self):
        assert fg._java_safe_identifier("return") == "_return"

    def test_java_keyword_case_insensitive(self):
        assert fg._java_safe_identifier("Return") == "_Return"

    def test_empty_string(self):
        assert fg._java_safe_identifier("") == "_"

    def test_special_chars_stripped(self):
        assert fg._java_safe_identifier("name$#") == "name"

    def test_underscore_only(self):
        assert fg._java_safe_identifier("_") == "_unnamed"

    def test_non_ascii_stripped(self):
        # Chinese characters stripped
        assert fg._java_safe_identifier("名称") == "_"

    def test_plpgsql_keyword_old(self):
        assert fg._java_safe_identifier("old") == "_old"

    def test_plpgsql_keyword_new(self):
        assert fg._java_safe_identifier("new") == "_new"

    def test_plpgsql_keyword_raise(self):
        assert fg._java_safe_identifier("raise") == "_raise"


class TestCustomTypeClassname:
    """Test _custom_type_classname() — SQL type name → Java class name."""

    def test_t_prefix(self):
        assert fg._custom_type_classname("t_coord_rec") == "CoordRec"

    def test_type_prefix(self):
        assert fg._custom_type_classname("type_order_item") == "OrderItem"

    def test_no_prefix(self):
        assert fg._custom_type_classname("order_detail") == "OrderDetail"
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_naming.py -v
```

---

## Task 4: SQL 处理函数测试

**Files:**
- Create: `tests/test_sql_processing.py`

**测试目标函数:**
- `_split_sql_statements(sql_text)` — SQL 文本分割
- `_extract_comments_from_text(sql_text)` — 注释提取
- `_is_parse_warning(err)` — 解析警告判断
- `_format_validate_error(err)` — 错误格式化

### Step 1: 创建 tests/test_sql_processing.py

```python
"""
Tests for SQL processing functions in converter/flux_gauss.py.
"""
import pytest
import converter.flux_gauss as fg


class TestSplitSqlStatements:
    """Test _split_sql_statements() — splits SQL text into statements."""

    def test_single_statement(self):
        sql = "CREATE OR REPLACE FUNCTION foo() RETURNS void $$ BEGIN NULL; END; $$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1
        assert stmts[0][1] == 1  # start line

    def test_multiple_statements(self):
        sql = (
            "CREATE OR REPLACE FUNCTION foo() RETURNS void $$ BEGIN NULL; END; $$ LANGUAGE PLPGSQL;\n"
            "CREATE OR REPLACE FUNCTION bar() RETURNS void $$ BEGIN NULL; END; $$ LANGUAGE PLPGSQL;"
        )
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 2

    def test_empty_input(self):
        assert fg._split_sql_statements("") == []

    def test_whitespace_only(self):
        assert fg._split_sql_statements("   \n  \n  ") == []

    def test_preserves_content(self):
        sql = "CREATE OR REPLACE PROCEDURE pkg_test.do_something(p_id IN BIGINT)\n$$\nBEGIN\n  INSERT INTO t VALUES(p_id);\nEND;\n$$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1
        assert "do_something" in stmts[0][0]

    def test_dollar_quote_with_tag(self):
        sql = "CREATE FUNCTION foo() RETURNS void $body$ BEGIN NULL; END; $body$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1

    def test_nested_dollar_quotes(self):
        sql = (
            "CREATE FUNCTION outer() RETURNS void $$ BEGIN\n"
            "  CREATE FUNCTION inner() RETURNS void $inner$ BEGIN NULL; END; $inner$ LANGUAGE PLPGSQL;\n"
            "END; $$ LANGUAGE PLPGSQL;"
        )
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1  # outer should not be split at inner $$


class TestExtractCommentsFromText:
    """Test _extract_comments_from_text() — extracts comments with line numbers."""

    def test_single_line_comment(self):
        sql = "-- This is a comment\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["text"] == "-- This is a comment"
        assert comments[0]["line"] == 1
        assert comments[0]["type"] == "line"

    def test_block_comment(self):
        sql = "/* block comment */\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["type"] == "block"
        assert comments[0]["line"] == 1

    def test_multiline_block_comment(self):
        sql = "/* line 1\n   line 2\n   line 3 */\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["line"] == 1
        assert comments[0]["end_line"] == 3

    def test_multiple_comments(self):
        sql = "-- comment 1\nSELECT 1;\n-- comment 2\nSELECT 2;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 2
        assert comments[0]["line"] == 1
        assert comments[1]["line"] == 3

    def test_no_comments(self):
        sql = "SELECT 1; SELECT 2;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 0

    def test_empty_input(self):
        assert fg._extract_comments_from_text("") == []

    def test_comment_inside_dollar_body(self):
        sql = "CREATE FUNCTION f() $$ BEGIN\n-- inner comment\nNULL; END; $$ LANGUAGE PLPGSQL;"
        comments = fg._extract_comments_from_text(sql)
        # Should find the inner comment
        assert any(c["text"] == "-- inner comment" for c in comments)


class TestIsParseWarning:
    """Test _is_parse_warning() — identifies non-fatal parse warnings."""

    def test_warning_dict(self):
        assert fg._is_parse_warning({"Warning": "something"}) is True

    def test_reserved_keyword(self):
        assert fg._is_parse_warning({"ReservedKeywordAsIdentifier": {"keyword": "user"}}) is True

    def test_real_error(self):
        assert fg._is_parse_warning({"UnexpectedToken": {"got": ";"}}) is False

    def test_string_input(self):
        assert fg._is_parse_warning("some error") is False

    def test_none_input(self):
        assert fg._is_parse_warning(None) is False


class TestFormatValidateError:
    """Test _format_validate_error() — formats parse errors for display."""

    def test_unexpected_token(self):
        err = {"UnexpectedToken": {"location": {"line": 5, "column": 10}, "expected": "';'", "got": "'END'"}}
        result = fg._format_validate_error(err)
        assert "line 5" in result
        assert "col 10" in result

    def test_simple_error_string(self):
        err = {"SomeError": "plain message"}
        result = fg._format_validate_error(err)
        assert "SomeError" in result

    def test_tokenizer_error(self):
        err = {"TokenizerError": {"message": "bad char", "location": {"line": 1, "column": 5}}}
        result = fg._format_validate_error(err)
        assert "TokenizerError" in result or "bad char" in result
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_sql_processing.py -v
```

---

## Task 5: 数据类测试

**Files:**
- Create: `tests/test_dataclasses.py`

**测试目标:**
- `Parameter` — properties: `java_name`, `is_out`, `is_refcursor`
- `CommentInfo` — 简单数据容器
- `DmlStatement` — 默认值、字段
- `ProcedureInfo` — 初始化和字段默认值
- `PackageInfo` — 集合操作
- `SkippedItem`, `ProcedureMapping`, `ConversionReport` — 基础数据类

### Step 1: 创建 tests/test_dataclasses.py

```python
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
        assert p.is_out is False

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
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_dataclasses.py -v
```

---

## Task 6: AST 提取函数测试

**Files:**
- Create: `tests/test_ast_extraction.py`

**测试目标函数:**
- `extract_comments(ast)` — AST → CommentInfo 列表
- `_map_comments_to_procedures(comments, procedures, source_file)` — 注释→过程映射
- `extract_non_procedure_statements(ast, source_file)` — DDL/grant 跳过项提取
- `_is_ddl_type(stmt_type)` — DDL 类型判断

### Step 1: 创建 tests/test_ast_extraction.py

```python
"""
Tests for AST extraction functions in converter/flux_gauss.py.

These functions convert raw ogsql-parser JSON AST into structured
Python dataclasses. Tests verify correct parsing of AST nodes.
"""
import pytest
import converter.flux_gauss as fg


class TestExtractComments:
    """Test extract_comments() — AST comments → CommentInfo list."""

    def test_empty_ast(self):
        assert fg.extract_comments({}) == []

    def test_no_comments_key(self):
        assert fg.extract_comments({"statements": []}) == []

    def test_single_line_comment(self):
        ast = {"comments": [{"text": "-- hello", "line": 1, "end_line": 1, "column": 0, "type": "line"}]}
        result = fg.extract_comments(ast)
        assert len(result) == 1
        assert isinstance(result[0], fg.CommentInfo)
        assert result[0].text == "-- hello"
        assert result[0].comment_type == "line"

    def test_multiple_comments(self):
        ast = {
            "comments": [
                {"text": "-- first", "line": 1, "end_line": 1, "column": 0, "type": "line"},
                {"text": "/* second */", "line": 3, "end_line": 5, "column": 0, "type": "block"},
            ]
        }
        result = fg.extract_comments(ast)
        assert len(result) == 2
        assert result[1].comment_type == "block"

    def test_missing_fields_default(self):
        ast = {"comments": [{}]}
        result = fg.extract_comments(ast)
        assert len(result) == 1
        assert result[0].text == ""
        assert result[0].line == 0


class TestMapCommentsToProcedures:
    """Test _map_comments_to_procedures() — assign comments to procedures by line proximity."""

    def test_empty_inputs(self):
        assert fg._map_comments_to_procedures([], []) == []

    def test_no_procedures_returns_all_as_package_level(self):
        comments = [fg.CommentInfo(text="-- pkg comment", line=1, end_line=1, column=0, comment_type="line")]
        result = fg._map_comments_to_procedures(comments, [])
        assert len(result) == 1  # all become package-level

    def test_leading_comment_assigned_to_procedure(self):
        proc = fg.ProcedureInfo(
            name="pkg.p1", package="pkg", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
            source_start_line=5, source_end_line=10,
        )
        comment = fg.CommentInfo(text="-- doc for p1", line=3, end_line=3, column=0, comment_type="line")
        result = fg._map_comments_to_procedures([comment], [proc])
        assert len(proc.leading_comments) == 1
        assert proc.leading_comments[0].text == "-- doc for p1"
        assert len(result) == 0  # no package-level comments

    def test_inline_comment_inside_procedure(self):
        proc = fg.ProcedureInfo(
            name="pkg.p1", package="pkg", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
            source_start_line=1, source_end_line=10,
        )
        comment = fg.CommentInfo(text="-- inline", line=5, end_line=5, column=0, comment_type="line")
        fg._map_comments_to_procedures([comment], [proc])
        assert len(proc.inline_comments) == 1

    def test_comment_between_procedures_is_leading(self):
        proc1 = fg.ProcedureInfo(
            name="pkg.p1", package="pkg", proc_name="p1",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
            source_start_line=1, source_end_line=5,
        )
        proc2 = fg.ProcedureInfo(
            name="pkg.p2", package="pkg", proc_name="p2",
            is_function=False, return_type=None, parameters=[],
            body={}, sql_text="",
            source_start_line=10, source_end_line=15,
        )
        comment = fg.CommentInfo(text="-- between", line=7, end_line=7, column=0, comment_type="line")
        fg._map_comments_to_procedures([comment], [proc1, proc2])
        assert len(proc2.leading_comments) == 1
        assert proc1.leading_comments == []


class TestIsDdlType:
    """Test _is_ddl_type() — identifies DDL statement types."""

    def test_create_table(self):
        assert fg._is_ddl_type("CreateTable") is True

    def test_alter_table(self):
        assert fg._is_ddl_type("AlterTable") is True

    def test_drop_table(self):
        assert fg._is_ddl_type("DropTable") is True

    def test_select_is_not_ddl(self):
        assert fg._is_ddl_type("Select") is False

    def test_insert_is_not_ddl(self):
        assert fg._is_ddl_type("Insert") is False

    def test_create_function_is_not_ddl(self):
        # CreateFunction is a procedure type, not DDL
        assert fg._is_ddl_type("CreateFunction") is False

    def test_create_procedure_is_not_ddl(self):
        assert fg._is_ddl_type("CreateProcedure") is False


class TestExtractNonProcedureStatements:
    """Test extract_non_procedure_statements() — identifies DDL/grants/types to skip."""

    def test_empty_ast(self):
        result = fg.extract_non_procedure_statements({"statements": []}, "test.sql")
        assert result == []

    def test_skips_create_table(self):
        ast = {
            "statements": [
                {"CreateTable": {"name": [{"Identifier": {"value": "orders"}}], "location": {"line": 1}}},
            ]
        }
        result = fg.extract_non_procedure_statements(ast, "test.sql")
        assert len(result) == 1
        assert result[0].category == "DDL"

    def test_preserves_create_function(self):
        """CreateFunction should NOT be skipped — it's a procedure."""
        ast = {
            "statements": [
                {"CreateFunction": {"name": "test_func"}},
            ]
        }
        result = fg.extract_non_procedure_statements(ast, "test.sql")
        assert len(result) == 0

    def test_grant_is_skipped(self):
        ast = {
            "statements": [
                {"Grant": {"parameters": {"kind": "privileges"}}},
            ]
        }
        result = fg.extract_non_procedure_statements(ast, "test.sql")
        assert len(result) == 1
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_ast_extraction.py -v
```

---

## Task 7: DML 分析函数测试

**Files:**
- Create: `tests/test_dml_analysis.py`

**测试目标函数:**
- `_extract_dml_target(dml_data)` — 从 DML AST 提取目标表
- `_extract_table_names(from_clause)` — FROM 子句表名提取
- `_extract_table_names_from_insert(insert_data)` — INSERT 表名
- `_extract_table_names_from_update(update_data)` — UPDATE 表名
- `_extract_table_name_from_dml(dml_data)` — 通用 DML 表名

### Step 1: 创建 tests/test_dml_analysis.py

```python
"""
Tests for DML analysis functions in converter/flux_gauss.py.

These functions extract table names and targets from DML AST nodes.
"""
import pytest
import converter.flux_gauss as fg


class TestExtractTableNames:
    """Test _extract_table_names() — FROM clause table name extraction."""

    def test_simple_table(self):
        from_clause = [{"Table": {"name": [{"Identifier": {"value": "orders"}}]}}]
        result = fg._extract_table_names(from_clause)
        assert "orders" in result

    def test_multiple_tables(self):
        from_clause = [
            {"Table": {"name": [{"Identifier": {"value": "orders"}}]}},
            {"Table": {"name": [{"Identifier": {"value": "customers"}}]}},
        ]
        result = fg._extract_table_names(from_clause)
        assert "orders" in result
        assert "customers" in result

    def test_empty_from(self):
        assert fg._extract_table_names([]) == []

    def test_schema_qualified(self):
        from_clause = [
            {"Table": {"name": [
                {"Identifier": {"value": "public"}},
                {"Identifier": {"value": "orders"}},
            ]}}
        ]
        result = fg._extract_table_names(from_clause)
        assert len(result) >= 1


class TestExtractTableNamesFromInsert:
    """Test _extract_table_names_from_insert() — INSERT target table."""

    def test_simple_insert(self):
        insert_data = {"table": {"name": [{"Identifier": {"value": "orders"}}]}}
        result = fg._extract_table_names_from_insert(insert_data)
        assert "orders" in result

    def test_empty(self):
        assert fg._extract_table_names_from_insert({}) == []


class TestExtractTableNamesFromUpdate:
    """Test _extract_table_names_from_update() — UPDATE target table."""

    def test_simple_update(self):
        update_data = {"table": {"name": [{"Identifier": {"value": "orders"}}]}}
        result = fg._extract_table_names_from_update(update_data)
        assert "orders" in result

    def test_with_alias(self):
        update_data = {
            "table": {"name": [{"Identifier": {"value": "orders"}}]},
            "from": [],
        }
        result = fg._extract_table_names_from_update(update_data)
        assert "orders" in result


class TestExtractTableNameFromDml:
    """Test _extract_table_name_from_dml() — generic DML table name extraction."""

    def test_insert(self):
        dml = {"Insert": {"table": {"name": [{"Identifier": {"value": "orders"}}]}}}
        assert fg._extract_table_name_from_dml(dml) == "orders"

    def test_update(self):
        dml = {"Update": {"table": {"name": [{"Identifier": {"value": "products"}}]}}}
        assert fg._extract_table_name_from_dml(dml) == "products"

    def test_delete(self):
        dml = {"Delete": {"table": {"name": [{"Identifier": {"value": "logs"}}]}}}
        assert fg._extract_table_name_from_dml(dml) == "logs"

    def test_select_empty(self):
        dml = {"Select": {"body": {"Select": {"from": []}}}}
        assert fg._extract_table_name_from_dml(dml) == ""

    def test_unknown_dml(self):
        assert fg._extract_table_name_from_dml({}) == ""
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_dml_analysis.py -v
```

---

## Task 8: 表达式转换函数测试

**Files:**
- Create: `tests/test_expression.py`

**测试目标函数:**
- `_literal_to_java(lit)` — SQL 字面量 → Java
- `_java_op(sql_op)` — SQL 操作符 → Java
- `_is_numeric_literal(expr)` — 数字判断
- `_is_numeric_literal_expr(java_str)` — Java 数字表达式判断
- `_coerce_for_int(expr)` — int 强制转换
- `_escape_java_string(s)` — Java 字符串转义
- `_indent(text, level)` — 缩进
- `_flatten_comment(text)` — 注释格式化

### Step 1: 创建 tests/test_expression.py

```python
"""
Tests for expression conversion functions in converter/flux_gauss.py.

These pure functions handle the critical SQL → Java expression mapping.
"""
import pytest
import converter.flux_gauss as fg


class TestLiteralToJava:
    """Test _literal_to_java() — SQL literal → Java expression."""

    def test_string_literal(self):
        result = fg._literal_to_java({"SingleQuotedString": {"value": "hello"}})
        assert result == '"hello"'

    def test_number_literal(self):
        result = fg._literal_to_java({"Number": {"value": "42"}})
        assert result == "42"

    def test_boolean_true(self):
        result = fg._literal_to_java({"Boolean": {"value": True}})
        assert result == "true"

    def test_boolean_false(self):
        result = fg._literal_to_java({"Boolean": {"value": False}})
        assert result == "false"

    def test_null(self):
        result = fg._literal_to_java({"Null": {}})
        assert result == "null"

    def test_negative_number(self):
        result = fg._literal_to_java({"Number": {"value": "-1"}})
        assert result == "-1"


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

    def test_is_not(self):
        assert fg._java_op("IS NOT") == "!="

    def test_is(self):
        assert fg._java_op("IS") == "=="

    def test_concat(self):
        assert fg._java_op("||") == "+"


class TestIsNumericLiteral:
    """Test _is_numeric_literal() — detects numeric AST nodes."""

    def test_number_literal(self):
        assert fg._is_numeric_literal({"Number": {"value": "42"}}) is True

    def test_negative_number(self):
        assert fg._is_numeric_literal({"Number": {"value": "-5"}}) is True

    def test_string_literal(self):
        assert fg._is_numeric_literal({"SingleQuotedString": {"value": "42"}}) is False

    def test_unary_minus(self):
        # Some parsers wrap negative numbers as UnaryOp(-, Number)
        assert fg._is_numeric_literal({"UnaryOp": {"op": "-", "expr": {"Number": {"value": "5"}}}}) is True

    def test_non_numeric(self):
        assert fg._is_numeric_literal({"ColumnRef": {}}) is False


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
        assert "intValue" in result or "Integer" in result or result != "42L"

    def test_plain_int_unchanged(self):
        result = fg._coerce_for_int("42")
        assert result == "42"


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
    """Test _flatten_comment() — normalizes comment text."""

    def test_strips_leading_whitespace(self):
        result = fg._flatten_comment("  -- hello")
        assert result == "-- hello"

    def test_collapses_whitespace(self):
        result = fg._flatten_comment("-- hello   world")
        assert "  " not in result or result.count("  ") == 0


class TestCleanSql:
    """Test _clean_sql() — normalizes SQL for mapper XML."""

    def test_strips_trailing_semicolon(self):
        result = fg._clean_sql("SELECT * FROM t;")
        assert not result.endswith(";")

    def test_collapses_whitespace(self):
        result = fg._clean_sql("SELECT   *   FROM   t")
        assert "   " not in result

    def test_strips_leading_trailing_whitespace(self):
        result = fg._clean_sql("  SELECT 1  ")
        assert result == result.strip()
```

### Step 2: 运行测试

```bash
python3 -m pytest tests/test_expression.py -v
```

---

## Task 9: 全量运行 + 验证

### Step 1: 运行所有测试

```bash
python3 -m pytest tests/ -v --tb=short
```

Expected: 全部通过。如有失败，根据错误信息修正。

### Step 2: 运行覆盖率检查（可选）

```bash
pip3 install pytest-cov
python3 -m pytest tests/ --cov=converter.flux_gauss --cov-report=term-missing --tb=short
```

目标：纯函数覆盖率 > 80%。

### Step 3: 确认测试可在项目根目录直接运行

```bash
python3 -m pytest tests/ -v
```

确认无需任何额外环境变量或文件即可运行。

---

## 测试覆盖率目标

| 模块 | 函数数 | 测试覆盖 | 优先级 |
|------|--------|---------|--------|
| 类型转换 (602-726) | 6 | 30+ 测试 | CRITICAL |
| 命名工具 (774-845) | 6 | 20+ 测试 | CRITICAL |
| SQL 处理 (1063-1790) | 4 | 20+ 测试 | HIGH |
| 数据类 (860-1046) | 12 | 20+ 测试 | HIGH |
| AST 提取 (1732-1827) | 4 | 15+ 测试 | HIGH |
| DML 分析 (8694-8723) | 5 | 12+ 测试 | MEDIUM |
| 表达式转换 (8621-8644) | 8 | 18+ 测试 | MEDIUM |

**总计约 135+ 个测试用例**，覆盖最关键、最容易因重构而退化的纯函数。

---

## 后续扩展方向（本次不做，仅记录）

1. **集成回归测试** — 用 demo-project/sql/ 的真实 SQL 文件运行完整转换，对比 dest/ 中的生成结果
2. **_expr_to_java 测试** — 需要 mock ProcedureInfo，但覆盖面极广
3. **_process_statement 测试** — 需要 mock 多层依赖
4. **parse_sql_file 测试** — 需要 mock subprocess.run（调用 ogsql 二进制）
5. **generate_project 测试** — 需要 mock 文件系统操作
6. **性能回归测试** — 大文件转换耗时不应退化
