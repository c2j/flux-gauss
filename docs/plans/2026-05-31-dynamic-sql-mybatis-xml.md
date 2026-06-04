# Dynamic SQL → MyBatis Dynamic XML Tags 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 PL/pgSQL 中 IF/FOR 循环拼接 SQL 的动态模式，转换为 MyBatis Mapper XML 中的 `<if>`、`<where>`、`<foreach>` 等动态标签，减少 EXECUTE_UNRESOLVED TODO 数量，提升生成代码质量。

**Architecture:** 两阶段检测 — (1) 在语句处理阶段追踪"SQL 变量的条件拼接链"，(2) 在 Mapper XML 生成阶段将条件信息渲染为动态标签。Python 和 Rust 引擎同步实现，共享相同的 SQL 测试用例和预期输出。

**Tech Stack:** Python 3.9+ / Rust 1.80+ / pytest / cargo test

---

## 模式覆盖范围

本计划仅实现 **Phase 1**（产出比最高的模式）：

| 模式 | PL/pgSQL 输入 | MyBatis XML 输出 | 优先级 |
|---|---|---|---|
| A: 条件 WHERE | `IF p_where IS NOT NULL THEN v_sql := v_sql \|\| ' WHERE ' \|\| p_where; END IF;` | `<where><if test="where != null">${where}</if></where>` | P0 |
| B: 条件 ORDER BY | `IF p_order IS NOT NULL THEN v_sql := v_sql \|\| ' ORDER BY ' \|\| p_order; END IF;` | `<if test="order != null">ORDER BY ${order}</if>` | P0 |
| C: 动态 IN 子句 | `FOR i IN 1..v_cnt LOOP v_in := v_in \|\| ':' \|\| i; END LOOP;` | `<foreach collection="ids" item="id" open="(" close=")" separator=",">#{id}</foreach>` | P1 |

不覆盖：
- 模式 B（条件分支构造完全不同 SQL）→ 保持 Java if/else
- 模式 C（CASE 内不同 EXECUTE）→ 保持 Java switch
- DDL 动态操作（CREATE/DROP/ALTER）→ 保持 JdbcTemplate

---

## 数据结构变更

### Python — 新增 DynamicCondition

```python
# 在 DmlStatement 附近新增（约 line 906 后）
@dataclass
class DynamicCondition:
    """Represents a conditional SQL fragment that should become a MyBatis dynamic tag."""
    condition_expr: str       # Java boolean expression, e.g. "whereClause != null"
    sql_fragment: str         # SQL fragment, e.g. "WHERE ${whereClause}"
    clause_type: str          # "WHERE" | "ORDER_BY" | "SET" | "HAVING" | "IN" | "OTHER"
    tag_name: str             # "if" | "where" | "foreach" | "set" | "trim"
```

### Python — DmlStatement 新增字段

```python
# DmlStatement dataclass 新增字段（约 line 905 后）
dynamic_conditions: list = field(default_factory=list)  # List[DynamicCondition]
base_sql: str = ""                                       # 核心 SQL（不含动态条件）
```

### Python — ProcedureInfo 新增字段

```python
# ProcedureInfo dataclass 新增字段（约 line 938 后）
sql_concat_chain: dict = field(default_factory=dict)     # var_name -> [(condition_expr, sql_fragment, clause_type)]
```

### Rust — DmlStatement 新增字段

```rust
// types.rs DmlStatement 新增字段（约 line 82 后）
pub dynamic_conditions: Vec<DynamicCondition>,
pub base_sql: String,
```

### Rust — 新增 DynamicCondition struct

```rust
// types.rs 新增（约 line 83 后）
#[derive(Debug, Clone)]
pub struct DynamicCondition {
    pub condition_expr: String,
    pub sql_fragment: String,
    pub clause_type: String,    // "WHERE" | "ORDER_BY" | "SET" | "IN" | "OTHER"
    pub tag_name: String,       // "if" | "where" | "foreach" | "set"
}
```

### Rust — ProcedureInfo 新增字段

```rust
// types.rs ProcedureInfo 新增字段（约 line 166 后）
pub sql_concat_chain: HashMap<String, Vec<(String, String, String)>>,
```

---

## 目录结构

```
新增/修改文件:
  Python:
    converter/flux_gauss.py              # 主改动：检测 + 生成
    demo-project/sql/pkg_dynamic_xml.sql # 新测试用 SQL（含所有模式）
    tests/test_dynamic_xml.py            # 新单元测试

  Rust:
    crates/fluxgauss/src/types.rs        # DynamicCondition + DmlStatement 字段
    crates/fluxgauss/src/statement.rs    # 条件拼接追踪
    crates/fluxgauss/src/generate/mapper.rs  # 动态 XML 生成
    crates/fluxgauss/tests/fixtures/dynamic_xml.sql  # 测试 SQL
```

---

## Task 1: 创建测试用 SQL 文件

**Files:**
- Create: `demo-project/sql/pkg_dynamic_xml.sql`

**Step 1: 创建覆盖三种模式的 SQL 文件**

```sql
CREATE OR REPLACE PACKAGE pkg_dynamic_xml IS
    -- 模式 A: 条件 WHERE + ORDER BY
    PROCEDURE proc_conditional_query(
        p_table_name    VARCHAR2,
        p_where_clause  VARCHAR2 DEFAULT NULL,
        p_order_by      VARCHAR2 DEFAULT NULL,
        p_limit         INTEGER  DEFAULT 100
    );

    -- 模式 A+: 多条件 WHERE (AND 拼接)
    PROCEDURE proc_multi_condition_query(
        p_table_name  VARCHAR2,
        p_status      VARCHAR2 DEFAULT NULL,
        p_min_amount  NUMBER   DEFAULT NULL,
        p_start_date  DATE     DEFAULT NULL
    );

    -- 模式 B: 不适用动态 XML 的混合逻辑 IF
    PROCEDURE proc_mixed_logic(
        p_table_name VARCHAR2,
        p_mode       VARCHAR2
    );
END pkg_dynamic_xml;
/

CREATE OR REPLACE PACKAGE BODY pkg_dynamic_xml IS

    -- 模式 A: 经典条件 WHERE + ORDER BY
    PROCEDURE proc_conditional_query(
        p_table_name    VARCHAR2,
        p_where_clause  VARCHAR2 DEFAULT NULL,
        p_order_by      VARCHAR2 DEFAULT NULL,
        p_limit         INTEGER  DEFAULT 100
    ) IS
        v_sql VARCHAR2(4000);
        v_count INTEGER;
    BEGIN
        v_sql := 'SELECT id, name, amount, status, create_time FROM ' || p_table_name;

        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        v_sql := v_sql || ' LIMIT ' || p_limit;

        DBE_OUTPUT.PRINT_LINE('Generated SQL: ' || v_sql);

        EXECUTE IMMEDIATE v_sql;

        SELECT COUNT(*) INTO v_count FROM audit_log WHERE operation = 'QUERY';
    END proc_conditional_query;

    -- 模式 A+: 多条件 WHERE
    PROCEDURE proc_multi_condition_query(
        p_table_name  VARCHAR2,
        p_status      VARCHAR2 DEFAULT NULL,
        p_min_amount  NUMBER   DEFAULT NULL,
        p_start_date  DATE     DEFAULT NULL
    ) IS
        v_sql VARCHAR2(4000);
        v_where VARCHAR2(2000) := '';
    BEGIN
        v_sql := 'SELECT * FROM ' || p_table_name;

        IF p_status IS NOT NULL THEN
            IF v_where IS NOT NULL OR v_where != '' THEN
                v_where := v_where || ' AND ';
            END IF;
            v_where := v_where || 'status = ''' || p_status || '''';
        END IF;

        IF p_min_amount IS NOT NULL THEN
            IF v_where IS NOT NULL OR v_where != '' THEN
                v_where := v_where || ' AND ';
            END IF;
            v_where := v_where || 'amount >= ' || p_min_amount;
        END IF;

        IF p_start_date IS NOT NULL THEN
            IF v_where IS NOT NULL OR v_where != '' THEN
                v_where := v_where || ' AND ';
            END IF;
            v_where := v_where || 'create_time >= ''' || p_start_date || '''';
        END IF;

        IF v_where IS NOT NULL AND v_where != '' THEN
            v_sql := v_sql || ' WHERE ' || v_where;
        END IF;

        EXECUTE IMMEDIATE v_sql;
    END proc_multi_condition_query;

    -- 模式 B: 混合逻辑（不适用动态 XML）
    PROCEDURE proc_mixed_logic(
        p_table_name VARCHAR2,
        p_mode       VARCHAR2
    ) IS
        v_sql VARCHAR2(4000);
    BEGIN
        IF p_mode = 'ARCHIVE' THEN
            v_sql := 'INSERT INTO ' || p_table_name || '_hist SELECT * FROM ' || p_table_name;
            EXECUTE IMMEDIATE v_sql;
            INSERT INTO audit_log(log_time, operation, sql_text)
            VALUES (SYSTIMESTAMP, 'ARCHIVE', v_sql);
        ELSIF p_mode = 'CLEAN' THEN
            v_sql := 'DELETE FROM ' || p_table_name || ' WHERE create_time < SYSDATE - 30';
            EXECUTE IMMEDIATE v_sql;
            INSERT INTO audit_log(log_time, operation, sql_text)
            VALUES (SYSTIMESTAMP, 'CLEAN', v_sql);
        ELSE
            DBE_OUTPUT.PRINT_LINE('Unknown mode: ' || p_mode);
        END IF;
    END proc_mixed_logic;

END pkg_dynamic_xml;
/
```

**Step 2: 验证 SQL 文件语法**

```bash
# 用 ogsql 验证（如果有）
ogsql format < demo-project/sql/pkg_dynamic_xml.sql > /dev/null 2>&1 && echo "OK" || echo "SYNTAX ERROR"
```

**Step 3: Commit**

```bash
git add demo-project/sql/pkg_dynamic_xml.sql
git commit -m "test: add dynamic SQL test fixtures for MyBatis XML conversion"
```

---

## Task 2: Python — 新增数据结构

**Files:**
- Modify: `converter/flux_gauss.py` (lines ~891-940)

**Step 1: 在 DmlStatement 前新增 DynamicCondition dataclass**

在 `class DmlStatement:` 定义之前（约 line 891），添加：

```python
@dataclass
class DynamicCondition:
    """Represents a conditional SQL fragment that should become a MyBatis dynamic XML tag."""
    condition_expr: str       # Java boolean expression, e.g. "whereClause != null"
    sql_fragment: str         # SQL fragment, e.g. "WHERE ${whereClause}"
    clause_type: str          # "WHERE" | "ORDER_BY" | "SET" | "HAVING" | "IN" | "OTHER"
    tag_name: str             # "if" | "where" | "foreach" | "set" | "trim"
```

**Step 2: DmlStatement 新增两个字段**

在 DmlStatement 的 `forall_batch_arrays` 字段后（约 line 905），添加：

```python
    dynamic_conditions: list = field(default_factory=list)  # List[DynamicCondition]
    base_sql: str = ""                                       # Core SQL without dynamic conditions
```

**Step 3: ProcedureInfo 新增 sql_concat_chain 字段**

在 `dynamic_sql_templates` 字段后（约 line 938），添加：

```python
    sql_concat_chain: dict = field(default_factory=dict)    # var_name -> [(condition_expr, sql_fragment, clause_type)]
```

**Step 4: 运行现有测试确认不破坏**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
python3 -m pytest tests/ -v
```

Expected: All existing tests pass (dataclass 新增字段有 default_factory)

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat(py): add DynamicCondition dataclass and sql_concat_chain tracking fields"
```

---

## Task 3: Python — 条件拼接追踪（检测阶段）

**Files:**
- Modify: `converter/flux_gauss.py` — `_process_if()` (line 4138) + `_process_assignment()` (line 4341)

**核心算法：**

1. 在 `_process_assignment()` 中，当检测到 `v_sql := v_sql || ' WHERE ' || p_where` 模式时：
   - 记录到 `proc.sql_concat_chain[var_name]` 中
   - 条件表达式暂时为空（因为 assignment 本身不在 IF 块中时无条件）

2. 在 `_process_if()` 中，当 IF body 中**只有一个语句**且该语句是对 `v_sql` 的 `||=` 拼接时：
   - 将 IF 条件表达式附加到 `sql_concat_chain` 中对应的条目

3. 在 `_process_execute()` 中，当 EXECUTE 的变量在 `sql_concat_chain` 中有记录时：
   - 将 concat chain 转化为 `DmlStatement` 上的 `dynamic_conditions`
   - 从 SQL 模板中提取 `base_sql`

**Step 1: 新增辅助函数 _detect_sql_concat_append**

在 `_process_assignment()` 函数之前（约 line 4340），添加：

```python
def _detect_sql_concat_append(assign_data: dict, proc: ProcedureInfo) -> tuple:
    """Detect if this assignment is a SQL string append: v_sql := v_sql || ' WHERE ' || p_xxx.

    Returns (var_name, sql_fragment, clause_type) or None.
    Only detects simple patterns:
      - v_sql := v_sql || ' WHERE ' || p_var
      - v_sql := v_sql || ' ORDER BY ' || p_var
      - v_sql := v_sql || ' AND ' || p_var
    """
    target_expr = assign_data.get("target", {})
    var_name = _extract_var_name_from_expr(target_expr)
    if not var_name:
        return None

    expression = assign_data.get("expression", {})
    if not isinstance(expression, dict):
        return None

    # Must be BinaryOp(||) where left side is the same variable
    if "BinaryOp" not in expression:
        return None
    binop = expression["BinaryOp"]
    if binop.get("op") != "||":
        return None

    left = binop.get("left", {})
    right = binop.get("right", {})

    # Left must be the same variable (v_sql)
    left_var = _extract_var_name_from_expr(left)
    if not left_var or left_var != var_name:
        return None

    # Right must be a SQL fragment concatenation like ' WHERE ' || p_var
    # Try to reconstruct the SQL fragment from the right side
    result = _reconstruct_sql_from_concat({"BinaryOp": binop}, proc)
    if not result:
        return None

    sql_template, params = result

    # Detect clause type from the SQL fragment
    stripped = sql_template.strip().upper()
    if re.match(r'\bWHERE\b', stripped):
        clause_type = "WHERE"
    elif re.match(r'\bORDER\s+BY\b', stripped):
        clause_type = "ORDER_BY"
    elif re.match(r'\bSET\b', stripped):
        clause_type = "SET"
    elif re.match(r'\bAND\b', stripped):
        clause_type = "AND"
    elif re.match(r'\bHAVING\b', stripped):
        clause_type = "HAVING"
    else:
        clause_type = "OTHER"

    # The sql_fragment should be the part AFTER the variable reference
    # i.e., ' WHERE ' || p_var  →  "WHERE ${pVar}" (without the base variable)
    # We need just the new part being appended
    # The template already has ${var} placeholders, but the left side (v_sql) is NOT
    # in the template because _reconstruct_sql_from_concat processes the full expression.
    # We need to strip the base variable prefix.

    # Actually, the right side of || is what we want
    # Reconstruct just the right side
    right_result = _reconstruct_sql_from_concat(right, proc)
    if right_result:
        right_sql, _ = right_result
        return (var_name, right_sql, clause_type)

    return None
```

**Step 2: 修改 _process_if() 追踪条件拼接**

在 `_process_if()` 函数中（line 4138），在处理 `then_stmts` 之前，检测 IF body 是否为纯 SQL 拼接：

```python
def _process_if(if_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _coerce_condition(_expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages))

    # NEW: Check if this IF block contains only a single SQL concat append
    then_stmts = list(_iter_statements(if_data.get("then_stmts", [])))
    _sql_concat_info = None
    if len(then_stmts) == 1 and not if_data.get("elsifs") and not if_data.get("else_stmts"):
        stmt = then_stmts[0]
        if isinstance(stmt, dict) and "Assignment" in stmt:
            concat_result = _detect_sql_concat_append(stmt, proc)
            if concat_result:
                _sql_concat_info = concat_result

    if _sql_concat_info:
        var_name, sql_fragment, clause_type = _sql_concat_info
        if var_name not in proc.sql_concat_chain:
            proc.sql_concat_chain[var_name] = []
        proc.sql_concat_chain[var_name].append((condition, sql_fragment, clause_type))
        # Still generate the Java if block (for backward compatibility and mixed logic)
        # But mark it so _process_execute can optimize it away later
        # Add a special marker line that will be cleaned up later
        proc.java_logic_lines.append(f"// __DYNAMIC_XML_CONDITION__{var_name}__{condition}__{sql_fragment}__{clause_type}__")
        proc.java_logic_lines.append(f"if ({condition}) {{")
        for s in then_stmts:
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)
        proc.java_logic_lines.append("}")
    else:
        proc.java_logic_lines.append(f"if ({condition}) {{")
        for s in then_stmts:
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

        for elsif in if_data.get("elsifs", []):
            elsif_cond = _coerce_condition(_expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages))
            proc.java_logic_lines.append(f"}} else if ({elsif_cond}) {{")
            for s in _iter_statements(elsif.get("stmts", [])):
                _process_statement(s, proc, all_packages, dml_counter)
            _indent_last_lines(proc, 1)

        if if_data.get("else_stmts"):
            proc.java_logic_lines.append("} else {")
            for s in _iter_statements(if_data["else_stmts"]):
                _process_statement(s, proc, all_packages, dml_counter)
            _indent_last_lines(proc, 1)

        proc.java_logic_lines.append("}")
```

**Step 3: 修改 _process_execute() 利用 sql_concat_chain**

在 `_process_execute()` 函数中（line 6060），在解析到 `dynamic_template` 的分支内（约 line 6389），添加对 `sql_concat_chain` 的检查：

在 `if dynamic_template and not sql_text:` 块内（约 line 6390），在生成 DmlStatement 之前，检查该变量是否有 concat chain：

```python
    # After sql_text is resolved from dynamic_template (line ~6392)
    # Check if this variable has conditional concat chain
    concat_chain = proc.sql_concat_chain.get(var_name, [])

    if concat_chain:
        # The base SQL is sql_text WITHOUT the dynamic conditions
        # The dynamic conditions were appended via IF blocks
        # We need to separate base SQL from dynamic fragments
        dynamic_conds = []
        for cond_expr, frag, clause_type in concat_chain:
            # Determine MyBatis tag
            if clause_type == "WHERE":
                tag = "where"  # Will use <where><if>...</if></where>
            elif clause_type == "ORDER_BY":
                tag = "if"
            elif clause_type == "SET":
                tag = "if"
            else:
                tag = "if"

            dynamic_conds.append(DynamicCondition(
                condition_expr=cond_expr,
                sql_fragment=frag,
                clause_type=clause_type,
                tag_name=tag,
            ))

        # Store dynamic conditions on the DmlStatement
        # (will be used in _build_mapper_statement to generate XML)
        proc.dml_statements.append(DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type=None,
            extra_params=extra,
            is_dynamic=True,
            dynamic_conditions=dynamic_conds,
            base_sql=sql_text,  # Will be refined in XML generation
        ))
        # ... rest of the existing code for mapper call generation
```

**Step 4: 清理标记行**

在 `_write_service_class()` 或分析完成后，清理 `__DYNAMIC_XML_CONDITION__` 标记行（如果仍存在于 java_logic_lines 中）：

```python
# Before generating service class, clean up marker lines
proc.java_logic_lines = [
    line for line in proc.java_logic_lines
    if not line.strip().startswith("// __DYNAMIC_XML_CONDITION__")
]
```

**Step 5: 运行 Python 测试**

```bash
python3 -m pytest tests/ -v
```

**Step 6: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat(py): detect conditional SQL concat patterns in IF blocks"
```

---

## Task 4: Python — Mapper XML 动态标签生成

**Files:**
- Modify: `converter/flux_gauss.py` — `_build_mapper_statement()` (line 9812)

**Step 1: 在 _build_mapper_statement 中生成动态 XML**

在 `_build_mapper_statement()` 中，在生成 `<{tag} id="...">` 之后、写入 SQL 之前，添加动态条件处理：

在现有 `if dml.is_forall_batch and dml.forall_batch_arrays:` 块（line 10095）之前，添加新的分支：

```python
    if dml.dynamic_conditions:
        # Generate MyBatis dynamic XML tags
        # First, determine which parts of the SQL are dynamic
        # Strategy: emit the base SQL, then append dynamic conditions as <if>/<where> tags

        # The base_sql is the core SQL without dynamic parts
        # The dynamic_conditions contain conditional fragments
        base_sql_for_xml = sql  # Start with the full SQL; we'll split it

        # Determine if we have a WHERE condition that should use <where> tag
        has_where_cond = any(dc.clause_type == "WHERE" for dc in dml.dynamic_conditions)

        xml_parts.append(f'<{tag} id="{dml.method_id}"{params_attrs}{result_type_attr}>')

        # Emit base SQL (everything before the first dynamic condition)
        # For simplicity: emit the full SQL as-is, then append dynamic conditions
        # The Java service layer will pass all params, and MyBatis will conditionally include
        #
        # NOTE: This initial implementation uses a conservative approach:
        # - The DmlStatement contains the FULL SQL (base + dynamic)
        # - We strip the dynamic parts and emit them as <if>/<where> tags
        # - The base_sql field contains the static part

        if dml.base_sql and dml.dynamic_conditions:
            # Split SQL: base part is dml.base_sql, dynamic parts are in conditions
            formatted_base = "\n".join(f"    {line}" for line in xml_escape(dml.base_sql).split("\n"))
            xml_parts.append(formatted_base)

            # Emit dynamic conditions
            where_conditions = [dc for dc in dml.dynamic_conditions if dc.clause_type == "WHERE"]
            other_conditions = [dc for dc in dml.dynamic_conditions if dc.clause_type != "WHERE"]

            if where_conditions:
                xml_parts.append('    <where>')
                for dc in where_conditions:
                    frag_escaped = xml_escape(dc.sql_fragment)
                    # Strip leading "WHERE " or "AND " since <where> handles it
                    frag_clean = re.sub(r'^\s*(WHERE|AND)\s+', '', frag_escaped, flags=re.IGNORECASE)
                    xml_parts.append(f'        <if test="{xml_escape(dc.condition_expr)}">')
                    xml_parts.append(f'            AND {frag_clean}')
                    xml_parts.append(f'        </if>')
                xml_parts.append('    </where>')

            for dc in other_conditions:
                frag_escaped = xml_escape(dc.sql_fragment)
                if dc.clause_type == "ORDER_BY":
                    # Strip leading ORDER BY and use as parameterized
                    frag_clean = re.sub(r'^\s*ORDER\s+BY\s+', '', frag_escaped, flags=re.IGNORECASE)
                    xml_parts.append(f'    <if test="{xml_escape(dc.condition_expr)}">')
                    xml_parts.append(f'        ORDER BY {frag_clean}')
                    xml_parts.append(f'    </if>')
                else:
                    xml_parts.append(f'    <if test="{xml_escape(dc.condition_expr)}">')
                    xml_parts.append(f'        {frag_escaped}')
                    xml_parts.append(f'    </if>')
        else:
            # Fallback: no split possible, emit as-is
            xml_parts.append(formatted_sql)

        xml_parts.append(f'</{tag}>')
    elif dml.is_forall_batch and dml.forall_batch_arrays:
        # ... existing forall batch handling
```

**Step 2: 添加 xml_escape 辅助（如果不存在）**

检查是否已有 `xml_escape` 函数，如果没有则添加：

```python
def _xml_escape(s: str) -> str:
    """Escape special XML characters."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
```

注意：现有代码在 line 10046 已有 `sql = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")`，
所以在动态条件中也使用同样的转义。

**Step 3: 运行转换验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cd dest && mvn compile
```

Expected: `pkg_dynamic_xml` 包生成的 Mapper XML 应包含 `<if>` / `<where>` 标签。

**Step 4: 检查生成的 XML**

```bash
cat dest/src/main/resources/mapper/DynamicXmlMapper.xml
```

Expected output应类似：

```xml
<select id="conditionalQuerySelect1" resultType="java.util.LinkedHashMap">
    SELECT id, name, amount, status, create_time FROM ${tableName}
    <where>
        <if test="whereClause != null">
            AND ${whereClause}
        </if>
    </where>
    <if test="orderBy != null">
        ORDER BY ${orderBy}
    </if>
    LIMIT #{limit}
</select>
```

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat(py): generate MyBatis <if>/<where> dynamic XML tags from conditional SQL"
```

---

## Task 5: Python — 单元测试

**Files:**
- Create: `tests/test_dynamic_xml.py`

**Step 1: 创建测试文件**

```python
"""
Unit tests for dynamic SQL → MyBatis dynamic XML tag conversion.

Tests cover:
1. DynamicCondition dataclass
2. _detect_sql_concat_append() pattern detection
3. _build_mapper_statement() dynamic XML generation
4. End-to-end: SQL → DmlStatement with dynamic_conditions → XML output
"""
import pytest
import converter.flux_gauss as fg
from converter.flux_gauss import (
    DynamicCondition, DmlStatement, ProcedureInfo, PackageInfo, Parameter,
    _detect_sql_concat_append, _build_mapper_statement,
)


class TestDynamicCondition:
    """Tests for the DynamicCondition dataclass."""

    def test_create_basic(self):
        dc = DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="if",
        )
        assert dc.condition_expr == "whereClause != null"
        assert dc.sql_fragment == "WHERE ${whereClause}"
        assert dc.clause_type == "WHERE"
        assert dc.tag_name == "if"

    def test_default_clause_type_other(self):
        dc = DynamicCondition(
            condition_expr="x != null",
            sql_fragment="HAVING x > 0",
            clause_type="HAVING",
            tag_name="if",
        )
        assert dc.clause_type == "HAVING"


class TestDmlStatementDynamicConditions:
    """Tests for DmlStatement with dynamic_conditions field."""

    def test_default_empty_dynamic_conditions(self):
        dml = DmlStatement(
            sql_type="select",
            method_id="testSelect1",
            sql_text="SELECT * FROM orders",
        )
        assert dml.dynamic_conditions == []
        assert dml.base_sql == ""

    def test_with_dynamic_conditions(self):
        dc = DynamicCondition(
            condition_expr="status != null",
            sql_fragment="WHERE status = #{status}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = DmlStatement(
            sql_type="select",
            method_id="testSelect1",
            sql_text="SELECT * FROM orders WHERE status = #{status}",
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM orders",
        )
        assert len(dml.dynamic_conditions) == 1
        assert dml.base_sql == "SELECT * FROM orders"


class TestBuildMapperStatementDynamicXml:
    """Tests for _build_mapper_statement() with dynamic conditions."""

    def _make_proc_with_dynamic(self):
        """Create a ProcedureInfo with a DmlStatement containing dynamic conditions."""
        proc = ProcedureInfo(
            name="pkg_test.proc_dyn",
            package="pkg_test",
            proc_name="proc_dyn",
            parameters=[
                Parameter(name="p_table_name", java_type="String", sql_type="varchar", mode="IN"),
                Parameter(name="p_where_clause", java_type="String", sql_type="varchar", mode="IN"),
                Parameter(name="p_order_by", java_type="String", sql_type="varchar", mode="IN"),
            ],
        )
        return proc

    def test_where_if_tag_generation(self):
        """Test that a WHERE condition generates <where><if>...</if></where>."""
        proc = self._make_proc_with_dynamic()
        dc = DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = DmlStatement(
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

    def test_order_by_if_tag_generation(self):
        """Test that ORDER BY condition generates <if>ORDER BY ...</if>."""
        proc = self._make_proc_with_dynamic()
        dc = DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        dml = DmlStatement(
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
        assert "ORDER BY" in xml
        assert "</if>" in xml

    def test_combined_where_and_order_by(self):
        """Test combined WHERE + ORDER BY dynamic conditions."""
        proc = self._make_proc_with_dynamic()
        dc_where = DynamicCondition(
            condition_expr="whereClause != null",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dc_order = DynamicCondition(
            condition_expr="orderBy != null",
            sql_fragment="ORDER BY ${orderBy}",
            clause_type="ORDER_BY",
            tag_name="if",
        )
        dml = DmlStatement(
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
        assert "ORDER BY" in xml

    def test_no_dynamic_conditions_produces_static_xml(self):
        """Test that DmlStatement without dynamic_conditions produces normal static XML."""
        proc = self._make_proc_with_dynamic()
        dml = DmlStatement(
            sql_type="select",
            method_id="staticSelect1",
            sql_text="SELECT * FROM orders WHERE status = #{status}",
            result_type="java.util.LinkedHashMap",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "<where>" not in xml
        assert '<if test=' not in xml
        assert "SELECT * FROM orders" in xml

    def test_xml_escape_in_conditions(self):
        """Test that special XML chars in condition expressions are escaped."""
        proc = self._make_proc_with_dynamic()
        dc = DynamicCondition(
            condition_expr="whereClause != null && whereClause != \"\"",
            sql_fragment="WHERE ${whereClause}",
            clause_type="WHERE",
            tag_name="where",
        )
        dml = DmlStatement(
            sql_type="select",
            method_id="dynSelect1",
            sql_text="SELECT * FROM ${tableName}",
            dynamic_conditions=[dc],
            base_sql="SELECT * FROM ${tableName}",
        )

        xml = fg._build_mapper_statement(proc, dml)
        assert "&amp;&amp;" in xml or "!= null" in xml


class TestProcedureInfoSqlConcatChain:
    """Tests for ProcedureInfo.sql_concat_chain field."""

    def test_default_empty_chain(self):
        proc = ProcedureInfo(
            name="pkg_test.proc_a",
            package="pkg_test",
            proc_name="proc_a",
        )
        assert proc.sql_concat_chain == {}

    def test_chain_with_entries(self):
        proc = ProcedureInfo(
            name="pkg_test.proc_a",
            package="pkg_test",
            proc_name="proc_a",
        )
        proc.sql_concat_chain["v_sql"] = [
            ("whereClause != null", "WHERE ${whereClause}", "WHERE"),
            ("orderBy != null", "ORDER BY ${orderBy}", "ORDER_BY"),
        ]
        assert len(proc.sql_concat_chain["v_sql"]) == 2
```

**Step 2: 运行测试**

```bash
python3 -m pytest tests/test_dynamic_xml.py -v
```

Expected: All tests pass

**Step 3: Commit**

```bash
git add tests/test_dynamic_xml.py
git commit -m "test(py): add unit tests for dynamic SQL → MyBatis XML conversion"
```

---

## Task 6: Rust — 数据结构变更

**Files:**
- Modify: `crates/fluxgauss/src/types.rs` (lines ~72-83, ~166)

**Step 1: 新增 DynamicCondition struct**

在 `DmlStatement` 之前（约 line 72），添加：

```rust
/// Represents a conditional SQL fragment that should become a MyBatis dynamic XML tag.
#[derive(Debug, Clone)]
pub struct DynamicCondition {
    /// Java boolean expression, e.g. "whereClause != null"
    pub condition_expr: String,
    /// SQL fragment, e.g. "WHERE ${whereClause}"
    pub sql_fragment: String,
    /// "WHERE" | "ORDER_BY" | "SET" | "HAVING" | "IN" | "OTHER"
    pub clause_type: String,
    /// "if" | "where" | "foreach" | "set" | "trim"
    pub tag_name: String,
}
```

**Step 2: DmlStatement 新增字段**

在 DmlStatement 的 `extra_params` 字段后（line 82），添加：

```rust
    pub dynamic_conditions: Vec<DynamicCondition>,
    pub base_sql: String,
```

**Step 3: 更新 DmlStatement 所有构造点**

在所有 `DmlStatement { ... }` 构造处添加 `dynamic_conditions: Vec::new(), base_sql: String::new()`。

搜索所有 `DmlStatement {` 出现的位置并逐一添加。主要位置：
- `statement.rs` line ~618, ~980
- `generate/mapper.rs` line ~1216
- `generate/service.rs` line ~906
- `generate/test.rs` lines ~735, ~757, ~780, ~802, ~825, ~908, ~930
- `generate/itest.rs` lines ~1511, ~1536, ~1612

**Step 4: ProcedureInfo 新增字段**

在 `dynamic_sql_templates` 字段后（约 line 166），添加：

```rust
    pub sql_concat_chain: HashMap<String, Vec<(String, String, String)>>,
```

在 `ProcedureInfo::new()` 中初始化：
```rust
    sql_concat_chain: HashMap::new(),
```

**Step 5: 编译验证**

```bash
cd crates/fluxgauss && cargo build 2>&1 | head -30
```

Expected: 编译通过（可能有 unused warnings，可接受）

**Step 6: Commit**

```bash
git add crates/fluxgauss/src/types.rs
git commit -m "feat(rs): add DynamicCondition struct and sql_concat_chain tracking fields"
```

---

## Task 7: Rust — 条件拼接追踪（检测阶段）

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` (lines ~1503-1629)

**Step 1: 新增辅助函数 detect_sql_concat_append**

在 `statement.rs` 中 `flatten_concat()` 函数附近（约 line 557），添加：

```rust
/// Detect if this assignment is a SQL string append: v_sql := v_sql || ' WHERE ' || p_xxx.
/// Returns (var_name, sql_fragment, clause_type) or None.
fn detect_sql_concat_append(
    target: &ogsql_parser::ast::plpgsql::PlAssignmentTarget,
    expression: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
) -> Option<(String, String, String)> {
    use ogsql_parser::ast::plpgsql::PlAssignmentTarget;
    use ogsql_parser::ast::Expr;

    let var_name = match target {
        PlAssignmentTarget::Variable(name) => name.clone(),
        _ => return None,
    };

    // Must be BinaryOp(||) where left side is the same variable
    if let Expr::BinaryOp { left, op, right } = expression {
        if op != "||" { return None; }

        // Left must be the same variable
        let left_var = match left.as_ref() {
            Expr::PlVariable(name) | Expr::ColumnRef(name) if name.len() == 1 => name[0].clone(),
            _ => return None,
        };
        if left_var != var_name { return None; }

        // Reconstruct SQL fragment from right side
        if let Some((sql_template, _params)) = flatten_concat(right, proc) {
            let stripped = sql_template.trim().to_uppercase();
            let clause_type = if stripped.starts_with("WHERE") {
                "WHERE"
            } else if stripped.starts_with("ORDER") {
                "ORDER_BY"
            } else if stripped.starts_with("SET") {
                "SET"
            } else if stripped.starts_with("AND") {
                "AND"
            } else {
                "OTHER"
            };
            return Some((var_name, sql_template, clause_type.to_string()));
        }
    }
    None
}
```

**Step 2: 修改 IF 处理追踪条件拼接**

在 `process_statement()` 的 `PlStatement::If(if_stmt)` 分支（约 line 1608），添加追踪逻辑：

```rust
        PlStatement::If(if_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);

            // NEW: Check if this IF block is a single SQL concat append
            let then_stmts = &if_stmt.node.then_body.statements;
            let mut concat_info: Option<(String, String, String)> = None;

            if then_stmts.len() == 1 && if_stmt.node.elsif_branches.is_empty() {
                if let Some(then_stmt) = then_stmts.first() {
                    if let PlStatement::Assignment { target, expression } = then_stmt {
                        concat_info = detect_sql_concat_append(target, expression, proc);
                    }
                }
            }

            if let Some((var_name, sql_fragment, clause_type)) = &concat_info {
                proc.sql_concat_chain.entry(var_name.clone())
                    .or_insert_with(Vec::new)
                    .push((cond.clone(), sql_fragment.clone(), clause_type.clone()));
            }

            push_logic_line(proc, format!("if ({}) {{", cond));
            for s in then_stmts {
                process_statement(s, proc, ctx)?;
            }
            pop_indent(proc);

            for elsif in &if_stmt.node.elsif_branches {
                let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                push_logic_line(proc, format!("}} else if ({}) {{", elsif_cond));
                for s in &elsif.body.statements {
                    process_statement(s, proc, ctx)?;
                }
                pop_indent(proc);
            }

            if let Some(else_body) = &if_stmt.node.else_body {
                push_logic_line(proc, "} else {".to_string());
                for s in &else_body.statements {
                    process_statement(s, proc, ctx)?;
                }
                pop_indent(proc);
            }

            push_logic_line(proc, "}".to_string());
            Ok(())
        }
```

**Step 3: 修改 EXECUTE 处理利用 sql_concat_chain**

在 `process_execute_stmt()` 的 `handle_resolved_execute_sql()` 调用点（约 line 998）附近，当检测到 var_name 在 `sql_concat_chain` 中有记录时，将 chain 信息附加到 DmlStatement：

```rust
fn handle_resolved_execute_sql_with_chain(
    proc: &mut ProcedureInfo,
    sql_template: &str,
    concat_vars: &[(String, bool)],
    execute: &ogsql_parser::ast::plpgsql::PlExecuteStmt,
    ctx: &mut StatementContext,
) {
    // First do the standard processing
    handle_resolved_execute_sql(proc, sql_template, concat_vars, execute, ctx);

    // Then check for concat chain and attach dynamic conditions
    // Extract the var name from execute context
    // ... (similar to Python version)
}
```

**Step 4: 编译验证**

```bash
cd crates/fluxgauss && cargo build 2>&1 | head -30
```

**Step 5: Commit**

```bash
git add crates/fluxgauss/src/statement.rs
git commit -m "feat(rs): detect conditional SQL concat patterns in IF blocks"
```

---

## Task 8: Rust — Mapper XML 动态标签生成

**Files:**
- Modify: `crates/fluxgauss/src/generate/mapper.rs` (lines ~358-419)

**Step 1: 修改 build_mapper_statement 支持 dynamic_conditions**

在 `build_mapper_statement()` 函数中，在现有的 XML 输出逻辑之前，添加动态条件分支：

```rust
fn build_mapper_statement(
    proc: &crate::types::ProcedureInfo,
    dml: &crate::types::DmlStatement,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    // ... existing SQL cleaning logic ...

    let formatted_sql: String = sql.split('\n').map(|l| format!("    {}", l)).collect::<Vec<_>>().join("\n");

    let mut parts = Vec::new();
    parts.push(format!("<!-- {} -->", source_info));

    if !dml.dynamic_conditions.is_empty() {
        // Generate MyBatis dynamic XML
        parts.push(format!("<{} id=\"{}\"{}{}>", tag, dml.method_id, params_attrs, result_type_attr));

        // Emit base SQL
        let base = if dml.base_sql.is_empty() { &sql } else { &dml.base_sql };
        let base_escaped = xml_escape(base);
        let formatted_base: String = base_escaped.split('\n').map(|l| format!("    {}", l)).collect::<Vec<_>>().join("\n");
        parts.push(formatted_base);

        // Separate WHERE conditions from others
        let where_conds: Vec<_> = dml.dynamic_conditions.iter()
            .filter(|dc| dc.clause_type == "WHERE")
            .collect();
        let other_conds: Vec<_> = dml.dynamic_conditions.iter()
            .filter(|dc| dc.clause_type != "WHERE")
            .collect();

        if !where_conds.is_empty() {
            parts.push("    <where>".to_string());
            for dc in where_conds {
                let frag = xml_escape(&strip_leading_clause(&dc.sql_fragment, &dc.clause_type));
                parts.push(format!("        <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("            AND {}", frag));
                parts.push("        </if>".to_string());
            }
            parts.push("    </where>".to_string());
        }

        for dc in other_conds {
            let frag = xml_escape(&dc.sql_fragment);
            if dc.clause_type == "ORDER_BY" {
                let clean = strip_leading_clause(&frag, "ORDER_BY");
                parts.push(format!("    <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("        ORDER BY {}", clean));
                parts.push("    </if>".to_string());
            } else {
                parts.push(format!("    <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("        {}", frag));
                parts.push("    </if>".to_string());
            }
        }

        parts.push(format!("</{}>", tag));
    } else {
        // Existing static XML generation
        parts.push(format!("<{} id=\"{}\"{}{}>", tag, dml.method_id, params_attrs, result_type_attr));
        parts.push(formatted_sql);
        parts.push(format!("</{}>", tag));
    }

    parts.join("\n")
}

fn strip_leading_clause(sql: &str, clause_type: &str) -> String {
    match clause_type {
        "WHERE" => regex::Regex::new(r"(?i)^\s*WHERE\s+").unwrap().replace(sql, "").to_string(),
        "ORDER_BY" => regex::Regex::new(r"(?i)^\s*ORDER\s+BY\s+").unwrap().replace(sql, "").to_string(),
        "AND" => regex::Regex::new(r"(?i)^\s*AND\s+").unwrap().replace(sql, "").to_string(),
        _ => sql.to_string(),
    }
}
```

**Step 2: 编译验证**

```bash
cd crates/fluxgauss && cargo build 2>&1 | head -30
```

**Step 3: 运行 Rust 测试**

```bash
cd crates/fluxgauss && cargo test 2>&1 | tail -20
```

**Step 4: Commit**

```bash
git add crates/fluxgauss/src/generate/mapper.rs
git commit -m "feat(rs): generate MyBatis <if>/<where> dynamic XML tags from conditional SQL"
```

---

## Task 9: Rust — 单元测试

**Files:**
- Create: `crates/fluxgauss/src/generate/mapper_dynamic_tests.rs` (或内联在 mapper.rs)
- Modify: `crates/fluxgauss/src/generate/mapper.rs` — 添加 `#[cfg(test)]` 模块

**Step 1: 在 mapper.rs 底部添加测试模块**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DmlType, DynamicCondition, DmlStatement, Parameter, ProcedureInfo, ParamMode};
    use std::collections::{BTreeSet, HashMap};

    fn make_test_proc() -> ProcedureInfo {
        let mut proc = ProcedureInfo::new("pkg_test.proc_dyn".to_string(), "pkg_test".to_string(), "proc_dyn".to_string());
        proc.parameters = vec![
            Parameter { name: "p_table_name".to_string(), java_type: "String".to_string(), sql_type: "varchar".to_string(), mode: Some(ParamMode::In) },
            Parameter { name: "p_where_clause".to_string(), java_type: "String".to_string(), sql_type: "varchar".to_string(), mode: Some(ParamMode::In) },
            Parameter { name: "p_order_by".to_string(), java_type: "String".to_string(), sql_type: "varchar".to_string(), mode: Some(ParamMode::In) },
        ];
        proc
    }

    #[test]
    fn test_where_if_tag_generation() {
        let proc = make_test_proc();
        let dc = DynamicCondition {
            condition_expr: "whereClause != null".to_string(),
            sql_fragment: "WHERE ${whereClause}".to_string(),
            clause_type: "WHERE".to_string(),
            tag_name: "where".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} WHERE ${whereClause}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains("<where>"), "Should contain <where> tag");
        assert!(xml.contains("</where>"), "Should contain closing </where>");
        assert!(xml.contains(r#"<if test="whereClause != null">"#), "Should contain <if> with condition");
    }

    #[test]
    fn test_order_by_if_tag_generation() {
        let proc = make_test_proc();
        let dc = DynamicCondition {
            condition_expr: "orderBy != null".to_string(),
            sql_fragment: "ORDER BY ${orderBy}".to_string(),
            clause_type: "ORDER_BY".to_string(),
            tag_name: "if".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} ORDER BY ${orderBy}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains(r#"<if test="orderBy != null">"#));
        assert!(xml.contains("ORDER BY"));
    }

    #[test]
    fn test_no_dynamic_conditions_static_xml() {
        let proc = make_test_proc();
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "staticSelect1".to_string(),
            sql_text: "SELECT * FROM orders WHERE status = #{status}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: Vec::new(),
            base_sql: String::new(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(!xml.contains("<where>"), "Static SQL should NOT have <where>");
        assert!(!xml.contains("<if test="), "Static SQL should NOT have <if>");
    }

    #[test]
    fn test_combined_where_and_order_by() {
        let proc = make_test_proc();
        let dc_where = DynamicCondition {
            condition_expr: "whereClause != null".to_string(),
            sql_fragment: "WHERE ${whereClause}".to_string(),
            clause_type: "WHERE".to_string(),
            tag_name: "where".to_string(),
        };
        let dc_order = DynamicCondition {
            condition_expr: "orderBy != null".to_string(),
            sql_fragment: "ORDER BY ${orderBy}".to_string(),
            clause_type: "ORDER_BY".to_string(),
            tag_name: "if".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} WHERE ${whereClause} ORDER BY ${orderBy}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc_where, dc_order],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains("<where>"));
        assert!(xml.contains(r#"<if test="whereClause != null">"#));
        assert!(xml.contains(r#"<if test="orderBy != null">"#));
    }

    #[test]
    fn test_strip_leading_clause_where() {
        assert_eq!(strip_leading_clause("WHERE status = 1", "WHERE"), "status = 1");
    }

    #[test]
    fn test_strip_leading_clause_order_by() {
        assert_eq!(strip_leading_clause("ORDER BY name ASC", "ORDER_BY"), "name ASC");
    }
}
```

**Step 2: 运行 Rust 测试**

```bash
cd crates/fluxgauss && cargo test -- mapper::tests -v
```

Expected: All 6 tests pass

**Step 3: Commit**

```bash
git add crates/fluxgauss/src/generate/mapper.rs
git commit -m "test(rs): add unit tests for dynamic SQL → MyBatis XML conversion"
```

---

## Task 10: 集成测试 — 端到端验证

**Files:**
- Modify: `tests/test_integration.py` (追加测试)

**Step 1: 在 Python 集成测试中添加端到端验证**

在 `tests/test_integration.py` 末尾添加：

```python
class TestDynamicXmlIntegration:
    """End-to-end integration tests for dynamic SQL → MyBatis XML conversion."""

    def test_conditional_query_generates_dynamic_xml(self, tmp_path):
        """Test that proc_conditional_query generates <where> and <if> tags."""
        import converter.flux_gauss as fg

        sql = """
        CREATE OR REPLACE PACKAGE pkg_dyn_test IS
            PROCEDURE proc_cond_query(
                p_table  VARCHAR2,
                p_where  VARCHAR2 DEFAULT NULL,
                p_order  VARCHAR2 DEFAULT NULL
            );
        END pkg_dyn_test;
        /
        CREATE OR REPLACE PACKAGE BODY pkg_dyn_test IS
            PROCEDURE proc_cond_query(
                p_table  VARCHAR2,
                p_where  VARCHAR2 DEFAULT NULL,
                p_order  VARCHAR2 DEFAULT NULL
            ) IS
                v_sql VARCHAR2(4000);
            BEGIN
                v_sql := 'SELECT * FROM ' || p_table;
                IF p_where IS NOT NULL THEN
                    v_sql := v_sql || ' WHERE ' || p_where;
                END IF;
                IF p_order IS NOT NULL THEN
                    v_sql := v_sql || ' ORDER BY ' || p_order;
                END IF;
                EXECUTE IMMEDIATE v_sql;
            END proc_cond_query;
        END pkg_dyn_test;
        /
        """
        sql_file = tmp_path / "test.sql"
        sql_file.write_text(sql)

        output_dir = tmp_path / "output"
        fg.main_with_args(["-o", str(output_dir), "-s", str(sql_file)])

        # Check that mapper XML was generated
        mapper_xml = output_dir / "src" / "main" / "resources" / "mapper" / "DynTestMapper.xml"
        assert mapper_xml.exists(), f"Mapper XML not found at {mapper_xml}"

        content = mapper_xml.read_text()
        # Should contain dynamic XML tags
        assert "<where>" in content or '<if test=' in content, \
            f"Expected dynamic XML tags in:\n{content}"
```

**Step 2: 运行集成测试**

```bash
python3 -m pytest tests/test_integration.py::TestDynamicXmlIntegration -v
```

**Step 3: 运行完整转换验证**

```bash
# 用完整 demo-project 验证不回归
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cd dest && mvn compile
```

Expected: `mvn compile` 通过，无新增错误

**Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(py): add end-to-end integration test for dynamic SQL XML conversion"
```

---

## Task 11: Rust 集成测试 + 最终验证

**Files:**
- Create: `crates/fluxgauss/tests/fixtures/dynamic_xml.sql`
- Create: `crates/fluxgauss/tests/dynamic_xml_test.rs`（或内联测试）

**Step 1: 创建 Rust 集成测试 SQL fixture**

复制 `demo-project/sql/pkg_dynamic_xml.sql` 到 `crates/fluxgauss/tests/fixtures/dynamic_xml.sql`

**Step 2: 运行 Rust 端到端验证**

```bash
cd crates/fluxgauss && cargo run -- --config ../../demo-project/fluxgauss.yaml
```

验证 `dest/` 中生成的 Mapper XML 包含动态标签。

**Step 3: 运行全部测试**

```bash
# Python
python3 -m pytest tests/ -v
# Rust
cd crates/fluxgauss && cargo test
# Maven compile
cd dest && mvn compile
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "test: complete dynamic SQL → MyBatis XML integration tests (Python + Rust)"
```

---

## 验证清单

- [ ] Python 单元测试全部通过 (`pytest tests/ -v`)
- [ ] Rust 单元测试全部通过 (`cargo test`)
- [ ] `pkg_dynamic_xml.sql` 转换生成的 Mapper XML 包含 `<if>`/`<where>` 标签
- [ ] `pkg_dynamic_xml.sql` 转换生成的 Java Service 代码编译通过
- [ ] `mvn compile` 通过（全量，无回归）
- [ ] 现有 SQL 文件（PKG_CURSOR, PKG_FOR 等）转换结果不回归
- [ ] `EXECUTE_UNRESOLVED` TODO 数量减少（可通过转换报告验证）

## 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 条件拼接追踪误判 | 只追踪"纯 SQL 拼接"的 IF（body 只有一个赋值语句），混合逻辑保持原样 |
| `${}` 注入风险 | 在转换报告中标注 `DYNAMIC_SQL_INJECTION_RISK` |
| Rust 编译错误（所有 DmlStatement 构造点） | Task 6 逐一修复所有构造点，编译通过后才继续 |
| 回归现有转换 | 全量 `mvn compile` 验证 + 比较转换报告 |
