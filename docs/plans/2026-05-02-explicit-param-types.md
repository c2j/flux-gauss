# MyBatis Mapper 参数类型增强 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将生成的 Mapper XML 中 `#{paramName}` 简单占位符增强为 `#{paramName, jdbcType=X, javaType=Y}` 显式类型形式，提升可维护性和跨数据库兼容性。

**Architecture:** 新增 `SQL_TO_JDBC_TYPE` 映射表（类比已有 `SQL_TO_JAVA`），新建 `java_type_to_jdbc()` 辅助函数，改造 3 个参数替换点，对无法映射的复合类型回退到简单形式。

**Tech Stack:** Python 3.9+，仅修改 `converter/flux_gauss.py`

---

## 前置知识

### 唯一修改文件
- `converter/flux_gauss.py`（~6400 行）

### 参数替换的 3 个代码位置

| 函数 | 行号 | 场景 | 有类型信息？ |
|------|------|------|-------------|
| `_convert_params_to_mybatis()` | 4455-4473 | **主路径**：替换 SQL 中 proc params 和 local vars | ✅ params 有 `Parameter.sql_type`/`java_type`；local_vars 有 java_type |
| `_convert_placeholders_to_mybatis()` | 2710-2713 | 辅助路径：`:param` 和 `$N` 占位符 | ❌ 纯正则，无上下文 |
| USING args 替换 | 2579-2582 | EXECUTE ... USING 中的参数 | ❌ 纯正则，无上下文 |

### 关键数据结构

```python
# Parameter dataclass (L540-556)
@dataclass
class Parameter:
    name: str           # SQL 参数名 (snake_case)
    java_type: str      # Java 类型 (e.g., "String", "Long")
    sql_type: str       # 原始 SQL 类型 (e.g., "varchar", "bigint")
    mode: Optional[str] # IN, OUT, INOUT

# local_vars dict (ProcedureInfo.local_vars)
#   key = var_name (snake_case), value = java_type (str)
#   e.g., {"v_count": "Integer", "v_amount": "java.math.BigDecimal"}
```

### 已有类型映射
- `SQL_TO_JAVA` (L240-279): 35+ SQL → Java 映射
- **不存在** SQL → jdbcType 映射（需新建）

### 输出中的 3 种占位符模式

1. **简单参数**：`#{pAccountId}` — 需增强为 `#{pAccountId, jdbcType=BIGINT, javaType=Long}`
2. **字段访问**：`#{vEmp}.emp_name` — 复合类型，**回退到简单形式**（jdbcType 无法表达 record 字段）
3. **位置参数**：`$1`, `$2` — 部分场景未转换（已知 bug），**本次不处理**

---

## Task 1: 新建 SQL_TO_JDBC_TYPE 映射表

**Files:**
- Modify: `converter/flux_gauss.py` — 在 `SQL_TO_JAVA` (L279) 之后插入

**Step 1: 添加映射表**

在 `SQL_TO_JAVA` 字典之后（约 L280）、`TYPE_OVERRIDES` 之前（约 L282），插入新映射表：

```python
# ── SQL → MyBatis jdbcType Mapping ─────────────────────────────
# Maps normalized SQL type names to MyBatis JdbcType enum values.
# Used when generating #{param, jdbcType=X} in mapper XML.
SQL_TO_JDBC_TYPE = {
    # Integer types
    "bigint": "BIGINT",
    "biginteger": "BIGINT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "int4": "INTEGER",
    "int8": "BIGINT",
    "smallint": "SMALLINT",
    "serial": "INTEGER",
    "bigserial": "BIGINT",
    "number": "NUMERIC",
    # Decimal types
    "numeric": "NUMERIC",
    "decimal": "DECIMAL",
    "real": "REAL",
    "float4": "REAL",
    "float8": "DOUBLE",
    "double precision": "DOUBLE",
    "double": "DOUBLE",
    # String types
    "varchar": "VARCHAR",
    "varchar2": "VARCHAR",
    "character varying": "VARCHAR",
    "char": "CHAR",
    "text": "LONGVARCHAR",
    "string": "VARCHAR",
    # Boolean
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    # Date/Time
    "timestamp": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
    "date": "DATE",
    "time": "TIME",
    # Binary
    "bytea": "BINARY",
    "blob": "BLOB",
    "clob": "CLOB",
    # JSON (mapped to VARCHAR in JDBC)
    "json": "VARCHAR",
    "jsonb": "VARCHAR",
    "uuid": "OTHER",
    # Special
    "record": None,       # composite → fallback
    "exception": "VARCHAR",
}
```

**Step 2: 验证映射完整性**

目视确认 `SQL_TO_JAVA` 中的每个 key 都在 `SQL_TO_JDBC_TYPE` 中有对应条目。目前两者完全对齐。

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: add SQL_TO_JDBC_TYPE mapping table for MyBatis parameter typing"
```

---

## Task 2: 新建类型查询辅助函数

**Files:**
- Modify: `converter/flux_gauss.py` — 在 `sql_type_to_java()` 函数（L440）之后插入

**Step 1: 添加 `sql_type_to_jdbc()` 函数**

在 `sql_type_to_java()` 函数之后（约 L441）、`is_simple_java_type()` 之前（约 L443），插入：

```python
def sql_type_to_jdbc(sql_type) -> Optional[str]:
    """Convert SQL type to MyBatis JdbcType enum value. Returns None for unmappable types (composites, etc.)."""
    if not sql_type:
        return None
    # Handle dict types (PercentType, RefCursor, etc.) — same logic as sql_type_to_java
    if isinstance(sql_type, dict):
        if "TypeName" in sql_type:
            return sql_type_to_jdbc(sql_type["TypeName"])
        elif "PercentType" in sql_type:
            pt = sql_type["PercentType"]
            column = (pt.get("column") or "").lower()
            return sql_type_to_jdbc(_infer_type_from_column_name(column))
        # PercentRowType, Record, RefCursor, etc. → not mappable
        return None
    if isinstance(sql_type, str):
        pct_match = re.match(r'^(\w+)\.(\w+)%type$', sql_type, re.IGNORECASE)
        if pct_match:
            column = pct_match.group(2).lower()
            override = TYPE_OVERRIDES.get((pct_match.group(1).lower(), column))
            if override:
                return sql_type_to_jdbc(override)
            return sql_type_to_jdbc(_infer_type_from_column_name(column))
    normalized = str(sql_type).lower().strip()
    normalized = re.sub(r"\(.*\)", "", normalized).strip()
    return SQL_TO_JDBC_TYPE.get(normalized)
```

**Step 2: 添加 `java_type_to_jdbc()` 函数**

紧接上面函数之后插入。此函数用于从 Java 类型（local_vars 场景）反推 jdbcType：

```python
# Reverse mapping: Java type → jdbcType (for local_vars which only store java_type)
_JAVA_TO_JDBC = {
    "String": "VARCHAR",
    "Long": "BIGINT",
    "Integer": "INTEGER",
    "Boolean": "BOOLEAN",
    "Double": "DOUBLE",
    "Float": "REAL",
    "java.math.BigDecimal": "NUMERIC",
    "java.sql.Timestamp": "TIMESTAMP",
    "java.sql.Date": "DATE",
    "java.sql.Time": "TIME",
    "byte[]": "BINARY",
    "Object": None,                     # cannot determine
    "Map<String, Object>": None,        # composite, cannot determine
}

def java_type_to_jdbc(java_type: str) -> Optional[str]:
    """Convert Java type name to MyBatis JdbcType. Returns None for unmappable types."""
    if not java_type:
        return None
    # Direct lookup
    result = _JAVA_TO_JDBC.get(java_type)
    if result is not None or java_type in _JAVA_TO_JDBC:
        return result
    # Handle List<X>, custom types, etc.
    if java_type.startswith("List<"):
        return None
    return None
```

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: add sql_type_to_jdbc() and java_type_to_jdbc() helper functions"
```

---

## Task 3: 改造 `_convert_params_to_mybatis()` — 主路径

**Files:**
- Modify: `converter/flux_gauss.py:4455-4473`

**Step 1: 重写函数**

将 `_convert_params_to_mybatis()` (L4455-4473) 替换为以下实现：

```python
def _convert_params_to_mybatis(sql: str, params: list, local_vars: dict = None) -> str:
    """Convert SQL parameter references to MyBatis #{{paramName, jdbcType=X, javaType=Y}} syntax.

    Falls back to simple #{{paramName}} for composite/unknown types.
    """
    for p in params:
        jdbc = sql_type_to_jdbc(p.sql_type)
        java = p.java_type
        if jdbc and java:
            placeholder = f'#{{{p.java_name}, jdbcType={jdbc}, javaType={java}}}'
        else:
            placeholder = f'#{{{p.java_name}}}'
        sql = re.sub(
            rf'\b{re.escape(p.name)}\b',
            placeholder,
            sql,
            flags=re.IGNORECASE
        )
    if local_vars:
        for var_name, var_java_type in local_vars.items():
            java_name = snake_to_camel(var_name)
            jdbc = java_type_to_jdbc(var_java_type)
            if jdbc and var_java_type:
                placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={var_java_type}}}'
            else:
                placeholder = f'#{{{java_name}}}'
            sql = re.sub(
                rf'\b{re.escape(var_name)}\b',
                placeholder,
                sql,
                flags=re.IGNORECASE
            )
    return sql
```

**Step 2: 验证无语法错误**

```bash
python3 -c "import converter.flux_gauss" 2>&1 || python3 -c "import ast; ast.parse(open('converter/flux_gauss.py').read()); print('Syntax OK')"
```

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: enhance _convert_params_to_mybatis() to emit jdbcType and javaType"
```

---

## Task 4: 改造 `_convert_placeholders_to_mybatis()` — 辅助路径

**Files:**
- Modify: `converter/flux_gauss.py:2710-2713`

### 设计决策

`_convert_placeholders_to_mybatis()` 当前是无上下文的纯正则替换，处理两种模式：
- `:paramName` → `#{paramName}` (PL/pgSQL 绑定变量)
- `$1` → `#{param1}` (位置参数)

此函数在两个调用点使用：
- L2531：`_analyze_execute_statement()` 内的 `sql_text = _convert_placeholders_to_mybatis(query)`
- L2654：动态 SQL 场景的 `sql_text = _convert_placeholders_to_mybatis(sql_text)`

**策略：增强函数签名，接受可选的 `proc` 参数以获取类型信息。** 对于 `:paramName` 形式可尝试从 `proc.parameters` 或 `proc.local_vars` 查找类型。`$N` 形式无法可靠映射，保持简单形式。

**Step 1: 修改函数**

将 `_convert_placeholders_to_mybatis()` (L2710-2713) 替换为：

```python
def _convert_placeholders_to_mybatis(sql: str, proc=None) -> str:
    """Convert :param and $N placeholders to MyBatis #{{param}} syntax.

    When proc is provided, attempts to add jdbcType/javaType for :param references.
    $N positional params always use simple form (no reliable type mapping).
    """
    if proc:
        # Build lookup: param_name_lower → (java_name, jdbc_type, java_type)
        _type_map = {}
        for p in proc.parameters:
            jdbc = sql_type_to_jdbc(p.sql_type)
            if jdbc:
                _type_map[p.name.lower()] = (p.java_name, jdbc, p.java_type)
            else:
                _type_map[p.name.lower()] = (p.java_name, None, None)
        for var_name, var_java_type in proc.local_vars.items():
            java_name = snake_to_camel(var_name)
            jdbc = java_type_to_jdbc(var_java_type)
            if jdbc:
                _type_map[var_name.lower()] = (java_name, jdbc, var_java_type)
            else:
                _type_map[var_name.lower()] = (java_name, None, None)

        def _colon_replacer(m):
            raw_name = m.group(1)
            info = _type_map.get(raw_name.lower())
            if info and info[1] and info[2]:
                return f'#{{{info[0]}, jdbcType={info[1]}, javaType={info[2]}}}'
            elif info:
                return f'#{{{info[0]}}}'
            else:
                # Unknown param — use snake_to_camel as before
                return f'#{{{snake_to_camel(raw_name)}}}'

        sql = re.sub(r':(\w+)', _colon_replacer, sql)
    else:
        sql = re.sub(r':(\w+)', lambda m: f'#{{{snake_to_camel(m.group(1))}}}', sql)

    # $N positional params — always simple form
    sql = re.sub(r'\$(\d+)', lambda m: f'#{{param{m.group(1)}}}', sql)
    return sql
```

**Step 2: 更新调用点**

找到两个调用点，传入 `proc` 参数：

调用点 1（约 L2531）：
```python
# Before:
sql_text = _convert_placeholders_to_mybatis(query)
# After:
sql_text = _convert_placeholders_to_mybatis(query, proc=proc)
```

调用点 2（约 L2654）：
```python
# Before:
sql_text = _convert_placeholders_to_mybatis(sql_text)
# After:
sql_text = _convert_placeholders_to_mybatis(sql_text, proc=proc)
```

**Step 3: 验证语法**

```bash
python3 -c "import ast; ast.parse(open('converter/flux_gauss.py').read()); print('Syntax OK')"
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: enhance _convert_placeholders_to_mybatis() with type-aware :param conversion"
```

---

## Task 5: 改造 USING args 替换

**Files:**
- Modify: `converter/flux_gauss.py:2575-2583`

**Step 1: 修改替换逻辑**

将 USING args 的替换段（约 L2575-2583）改为类型感知版本：

```python
            using_args = execute_data.get("using_args", [])
            for arg in using_args:
                if isinstance(arg, dict):
                    argument = arg.get("argument", {})
                    arg_name = _extract_var_name_from_expr(argument)
                    if arg_name:
                        java_name = snake_to_camel(arg_name)
                        # Try to find type info from proc params or local vars
                        jdbc = None
                        java = None
                        for p in proc.parameters:
                            if p.name.lower() == arg_name.lower():
                                jdbc = sql_type_to_jdbc(p.sql_type)
                                java = p.java_type
                                break
                        if not jdbc and arg_name in proc.local_vars:
                            java = proc.local_vars[arg_name]
                            jdbc = java_type_to_jdbc(java)
                        if jdbc and java:
                            placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
                        else:
                            placeholder = f'#{{{java_name}}}'
                        sql_text = re.sub(
                            rf'\b{re.escape(arg_name)}\b',
                            placeholder,
                            sql_text, flags=re.IGNORECASE
                        )
```

**Step 2: 验证语法**

```bash
python3 -c "import ast; ast.parse(open('converter/flux_gauss.py').read()); print('Syntax OK')"
```

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: enhance USING args substitution with type info"
```

---

## Task 6: 端到端验证

**Files:**
- No code changes — verification only

**Step 1: 清理缓存，全量重新生成**

```bash
rm -rf dest/.fluxgauss
python3 converter/flux_gauss.py -c fluxgauss.yaml
```

Expected: 转换成功完成，无 Python 异常。

**Step 2: 检查生成的 Mapper XML 参数格式**

```bash
grep -n '#{' dest/src/main/resources/mapper/*.xml | head -40
```

Expected 规则：
- 有类型信息的参数：`#{pAccountId, jdbcType=BIGINT, javaType=Long}`
- 复合类型/Map 参数：`#{pNewRec}` （简单形式，回退）
- 字段访问模式不变：`#{vEmp}.emp_name`
- 位置参数不变：`#{param1}` 或 `$1`（如果未转换）

**Step 3: 抽样对比关键文件**

对 `ComplexClearingPkgMapper.xml` 手动验证：

Before:
```xml
<select id="selectCheckCrossTableConsistency" resultType="java.math.BigDecimal">
    SELECT balance
    FROM account
    WHERE account_id = #{pAccountId}
</select>
```

Expected After:
```xml
<select id="selectCheckCrossTableConsistency" resultType="java.math.BigDecimal">
    SELECT balance
    FROM account
    WHERE account_id = #{pAccountId, jdbcType=VARCHAR, javaType=String}
</select>
```

> 注：实际 jdbcType 取决于原始 SQL 中 `p_account_id` 参数的声明类型，此处仅为示例。

**Step 4: 编译验证**

```bash
cd dest && mvn compile
```

Expected: BUILD SUCCESS。Mapper XML 的参数格式变更不影响 Java 编译（MyBatis 在运行时解析 XML）。

**Step 5: 运行测试**

```bash
cd dest && mvn test
```

Expected: 所有测试通过。如果测试因 Mock 参数签名变化而失败，检查是否需要更新 mock 配置。

**Step 6: Commit（如有生成物调整）**

如果验证中发现需要微调（如 XML 格式化），修正后 commit：

```bash
git add converter/flux_gauss.py
git commit -m "fix: adjust parameter type formatting after e2e verification"
```

---

## 边界情况备忘

以下场景在实现时需特别注意，已在各 Task 中处理：

| 场景 | 处理方式 |
|------|----------|
| `Map<String, Object>` 类型参数 | `java_type_to_jdbc()` 返回 None → 回退简单形式 |
| `%ROWTYPE` 参数 | `sql_type_to_jdbc()` dict 分支返回 None → 回退 |
| `#{vEmp}.emp_name` 字段访问 | MyBatis 的 `#{}` 内不包含 `.field`，点号在 `}` 外面，不影响 |
| `PercentType` (table.column%TYPE) | 走 `_infer_type_from_column_name()` 推断后查 jdbcType |
| 未知/自定义 SQL 类型 | 映射表未覆盖 → `sql_type_to_jdbc()` 返回 None → 回退 |
| `$1`/`$2` 位置参数 | 无法可靠推断类型 → 始终简单形式 |
| `local_vars` 中 `Object` 类型 | `java_type_to_jdbc()` 返回 None → 回退 |

---

## 风险与回退

- **低风险**：所有改动都是生成逻辑增强，不影响已有 Java 编译
- **回退方案**：如果显式类型导致特定场景问题，将 `sql_type_to_jdbc()` / `java_type_to_jdbc()` 的 return 改为 `None` 即可全局回退到简单形式
- **增量发布**：可先只实施 Task 1-3（主路径），观察效果后再实施 Task 4-5（辅助路径）
