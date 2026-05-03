# 统一表结构提取：用 ogsql AST 替代 parse_table_ddl 正则

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 删除 `parse_table_ddl()` 正则解析器，统一使用 ogsql-parser AST 的 CreateTable 输出填充 `TYPE_OVERRIDES` 和 `_TABLE_COLUMNS`，消除 5 个正则 bug。

**Architecture:** 把 Phase 0（正则预扫描）和 Phase 1 中已有的 CreateTable AST 处理合并。新增 `_ogsql_data_type_to_sql()` 函数将 ogsql 的结构化 `data_type` 还原为 SQL 类型字符串（如 `{"Numeric": [18, 4]}` → `"NUMERIC(18,4)"`），替代正则解析。

**Tech Stack:** Python, ogsql-parser AST

---

## 变更概览

**删除：**
- `parse_table_ddl()` 函数（~60 行）
- Phase 0 预扫描循环（~12 行）

**新增：**
- `_ogsql_data_type_to_sql()` — 将 ogsql data_type 还原为 SQL 类型字符串（~30 行）
- `_populate_type_overrides_from_ast()` — 从 AST 提取 CreateTable 填充 TYPE_OVERRIDES（~15 行）

**修改：**
- `extract_procedures()` 中的 CreateTable 处理 → 同时填充 `TYPE_OVERRIDES`（SQL 类型）和 `_TABLE_COLUMNS`（Java 类型）
- `_itest_collect_schemas()` → 改为读取新的 `_TABLE_SCHEMA` 字典（保留 SQL 精度），替代从 `TYPE_OVERRIDES` 读取
- `_itest_write_schema_sql()` → 移除为正则 bug 添加的补丁过滤器（`--` 列名、`GENERATED ALWAYS`、`_valid_types` 白名单）
- `_itest_write_fixture_sql()` — fixture 中的列名来自干净的 schema，不再有虚假列

---

### Task 1: 新增 `_ogsql_data_type_to_sql()` 函数

**Files:**
- Modify: `converter/flux_gauss.py` (在 `sql_type_to_java()` 函数附近插入)

**Step 1: 实现函数**

在 `sql_type_to_java()` 函数之后（约 500 行）添加：

```python
def _ogsql_data_type_to_sql(data_type) -> str:
    """Convert ogsql-parser structured data_type back to SQL type string.

    Examples:
        "BigSerial"                 -> "BIGSERIAL"
        {"BigInt": null}            -> "BIGINT"
        {"Numeric": [18, 4]}        -> "NUMERIC(18,4)"
        {"Varchar": 100}            -> "VARCHAR(100)"
        "Date"                      -> "DATE"
        {"Timestamp": [null, null]} -> "TIMESTAMP"
        "Text"                      -> "TEXT"
        {"Integer": null}           -> "INTEGER"
    """
    if isinstance(data_type, str):
        return data_type.upper()
    if isinstance(data_type, dict):
        for key, val in data_type.items():
            name = key.upper()
            if val is None:
                return name
            if isinstance(val, list):
                # {"Numeric": [18, 4]} -> NUMERIC(18,4), {"Timestamp": [null, null]} -> TIMESTAMP
                parts = [str(v) for v in val if v is not None]
                if parts:
                    return f"{name}({','.join(parts)})"
                return name
            if isinstance(val, int):
                return f"{name}({val})"
            return name
    return "VARCHAR"
```

**Step 2: 验证**

```bash
python3 -c "
from converter.flux_gauss import _ogsql_data_type_to_sql
assert _ogsql_data_type_to_sql('BigSerial') == 'BIGSERIAL'
assert _ogsql_data_type_to_sql({'BigInt': None}) == 'BIGINT'
assert _ogsql_data_type_to_sql({'Numeric': [18, 4]}) == 'NUMERIC(18,4)'
assert _ogsql_data_type_to_sql({'Varchar': 100}) == 'VARCHAR(100)'
assert _ogsql_data_type_to_sql('Date') == 'DATE'
assert _ogsql_data_type_to_sql({'Timestamp': [None, None]}) == 'TIMESTAMP'
assert _ogsql_data_type_to_sql('Text') == 'TEXT'
assert _ogsql_data_type_to_sql({'Integer': None}) == 'INTEGER'
print('All assertions passed')
"
```

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: add _ogsql_data_type_to_sql() for AST-to-SQL type conversion"
```

---

### Task 2: 扩展 extract_procedures 中的 CreateTable 处理，同时填充 TYPE_OVERRIDES

**Files:**
- Modify: `converter/flux_gauss.py:1030` — 新增 `_TABLE_SCHEMA` 全局字典
- Modify: `converter/flux_gauss.py:1074-1084` — CreateTable 处理逻辑

**Step 1: 添加 `_TABLE_SCHEMA` 全局字典**

在 `_TABLE_COLUMNS` 声明（行 1030）处，改为：

```python
_TABLE_COLUMNS: dict = {}  # table_name_lower -> {col_name -> java_type}
_TABLE_SCHEMA: dict = {}   # table_name_lower -> {col_name -> sql_type_string}  (from ogsql AST)
```

**Step 2: 修改 extract_procedures 中的 CreateTable 分支**

将行 1074-1084 的 CreateTable 处理从：

```python
elif stmt_type == "CreateTable":
    tbl_name_parts = stmt_data.get("name", [])
    tbl_name = tbl_name_parts[-1] if isinstance(tbl_name_parts, list) and tbl_name_parts else ""
    if tbl_name:
        cols = {}
        for col in stmt_data.get("columns", []):
            col_name = col.get("name", "")
            if col_name:
                cols[col_name.lower()] = sql_type_to_java(col.get("data_type", "varchar"))
        if cols:
            _TABLE_COLUMNS[tbl_name.lower()] = cols
```

改为：

```python
elif stmt_type == "CreateTable":
    tbl_name_parts = stmt_data.get("name", [])
    tbl_name = tbl_name_parts[-1] if isinstance(tbl_name_parts, list) and tbl_name_parts else ""
    if tbl_name:
        java_cols = {}
        sql_cols = {}
        for col in stmt_data.get("columns", []):
            col_name = col.get("name", "")
            if col_name:
                col_lower = col_name.lower()
                dt = col.get("data_type", "varchar")
                java_cols[col_lower] = sql_type_to_java(dt)
                sql_cols[col_lower] = _ogsql_data_type_to_sql(dt)
        if java_cols:
            _TABLE_COLUMNS[tbl_name.lower()] = java_cols
        if sql_cols:
            _TABLE_SCHEMA[tbl_name.lower()] = sql_cols
            # Also populate TYPE_OVERRIDES for %TYPE resolution
            for col_lower, sql_type_str in sql_cols.items():
                TYPE_OVERRIDES[(tbl_name.lower(), col_lower)] = sql_type_str
```

**Step 3: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full 2>&1 | tail -5
```

确认转换正常完成，无报错。

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: populate TYPE_OVERRIDES from ogsql AST instead of regex"
```

---

### Task 3: 删除 Phase 0 预扫描和 parse_table_ddl

**Files:**
- Modify: `converter/flux_gauss.py:176-235` — 删除 `parse_table_ddl()` 函数
- Modify: `converter/flux_gauss.py:7087-7097` — 删除 Phase 0 循环

**Step 1: 删除 parse_table_ddl 函数**

删除行 176-235（整个 `parse_table_ddl` 函数）。

**Step 2: 删除 Phase 0 循环**

删除行 7087-7097（`# ── Phase 0: Pre-scan for table DDL ──` 及其循环体）。

**Step 3: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "refactor: remove parse_table_ddl() regex, use ogsql AST exclusively"
```

---

### Task 4: 重写 _itest_collect_schemas，使用 _TABLE_SCHEMA

**Files:**
- Modify: `converter/flux_gauss.py:4400-4406` — `_itest_collect_schemas()`

**Step 1: 简化函数**

将：

```python
def _itest_collect_schemas() -> dict:
    tables = {}
    for (tbl, col), sql_type in TYPE_OVERRIDES.items():
        if tbl not in tables:
            tables[tbl] = {}
        tables[tbl][col] = sql_type
    return tables
```

改为：

```python
def _itest_collect_schemas() -> dict:
    return dict(_TABLE_SCHEMA)
```

**Step 2: 验证**（确认 schema 输出正确）

```bash
python3 -c "
from converter.flux_gauss import *
# Run a quick parse to populate _TABLE_SCHEMA
import json, subprocess
result = subprocess.run([OGSQL_BIN, 'parse', '-j', '-f', 'demo-project/sql/tables.sql'], capture_output=True, text=True)
ast = json.loads(result.stdout)
extract_procedures(ast, 'tables.sql')
print('_TABLE_SCHEMA:', json.dumps(_TABLE_SCHEMA, indent=2))
print()
print('TYPE_OVERRIDES sample:', {k: v for k, v in list(TYPE_OVERRIDES.items())[:5]})
"
```

确认 `_TABLE_SCHEMA` 包含正确的表定义，没有 `constraint` 虚假列。

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "refactor: _itest_collect_schemas uses _TABLE_SCHEMA from ogsql AST"
```

---

### Task 5: 清理 _itest_write_schema_sql 中的正则 bug 补丁

**Files:**
- Modify: `converter/flux_gauss.py` — `_itest_write_schema_sql()` 函数

**Step 1: 移除不再需要的过滤器**

在 `_itest_write_schema_sql()` 中，移除以下为正则 bug 添加的补丁：

1. 删除 `_valid_types` 白名单检查（ogsql AST 不会产生无效类型）
2. 删除 `if col.startswith("--"):` 检查（ogsql 不会把注释解析为列名）
3. 删除 `if "GENERATED ALWAYS" in col_type.upper():` 检查（ogsql 不输出 GENERATED 列为类型字符串，但如果需要可以保留作为 OpenGauss 兼容性保障）

简化后的列定义循环：

```python
        col_defs = []
        for col, col_type in sorted(cols.items()):
            col_defs.append(f'    "{col}" {col_type}')
        if not col_defs:
            continue
```

**Step 2: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full 2>&1 | tail -5
cat dest/src/test/resources/itest-schema.sql | head -40
```

确认 schema 中：
- 没有 `--` 列名
- 没有 `constraint` 列
- 没有 `LIKE trade_record` 类型
- trade_record 有正确的列（trade_id, account_id, amount, fee, status, trade_date, processed_at, batch_seq）
- 没有 trade_backup 表（因为它是注释掉的）

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "refactor: remove regex bug workarounds from schema generation"
```

---

### Task 6: 端到端验证

**Step 1: 重新生成并编译**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full
cd dest && mvn compile test-compile -q
cd dest && mvn test
```

预期：107 单元测试全部通过，无回归。

**Step 2: 运行集成测试**

```bash
cd dest && mvn verify -Pintegration 2>&1 | grep "Tests run:" | grep "IT\b"
```

预期：
- 不再有 `column "constraint" does not exist` 错误（14 个 → 0）
- 不再有因 `--` 列名导致的 schema 错误
- trade_record 的 fixture INSERT 不再包含虚假的 `constraint` 列
- 整体通过数应从 28 增加到 ~42+

**Step 3: 验证 TYPE_OVERRIDES 功能正常**

确认 `%TYPE` 引用（如 `v_amount trade_record.amount%TYPE`）仍能正确推导为 Java 类型。检查生成的 Service.java 中相关变量类型。

**Step 4: Commit (如果有调整)**

```bash
git add -A
git commit -m "fix: adjust fixture generation for clean schema data"
```

---

## 风险与注意事项

1. **ogsql 对包体外 CREATE TABLE 的处理**：当前 `parse -j` 对包含 `CREATE PACKAGE BODY` 的文件只输出包定义/包体，包体外的独立 `CREATE TABLE`（如 `observations`）可能不在 AST 的 `statements` 中。需要验证。如果确实缺失，`observations` 表不会进入 `_TABLE_SCHEMA`，但其 DDL 本身就包含 OpenGauss 不兼容语法（`GENERATED ALWAYS AS`），缺失对集成测试无影响。

2. **`_TABLE_SCHEMA` 与 `_TABLE_COLUMNS` 的填充时机**：两者现在都在 Phase 1（ogsql 解析后）填充。`TYPE_OVERRIDES` 的读取（`%TYPE` 推导）发生在 `extract_procedures()` 内部的 `analyze_procedure()` 调用中。由于 `extract_procedures()` 先处理 CreateTable 再处理 Procedure/Function，且同一文件内的 CreateTable 在 AST 中排在 Procedure 之前，所以时序正确。**跨文件依赖**（如 `pkg_order.sql` 引用 `tables.sql` 中定义的表类型）需要验证——`TYPE_OVERRIDES` 在处理第一个文件后就已填充，后续文件应该能正确读取。

3. **`TYPE_OVERRIDES` 的手动初始化值**：行 336-340 的初始空 dict 保持不变，Phase 0 删除后它仅由 ogsql AST 填充。
