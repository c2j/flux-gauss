# 混合参数策略（Flat + DTO）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现混合参数策略：简单场景（≤3 参数，无字段访问）使用扁平 `@Param`，复杂场景（>3 参数或有字段访问）使用 DTO `parameterType`。修复 `#{param}.field` 非法 MyBatis 语法。

**Architecture:** 判断每个 procedure 的参数模式，对需要 DTO 的场景生成 Java DTO 类文件，改造 Mapper XML 使用 `parameterType` + 字段级 `#{field}`，改造 Mapper Java 接口使用 DTO 参数，改造 Service 层填充 DTO。

**Tech Stack:** Python 3.9+，仅修改 `converter/flux_gauss.py`

---

## 前置知识

### 唯一修改文件
- `converter/flux_gauss.py`（~6579 行）

### 当前问题清单（来自 MapperParamTestMapper.xml 基线）

| 问题 | 出现场景 | 当前输出 | 期望输出 |
|------|---------|----------|----------|
| `#{pNewRec}.customer_id` | ROWTYPE 参数字段访问 | 非法 MyBatis | `#{pNewRec_customerId, jdbcType=BIGINT}` (flat) 或 DTO |
| `#{vDetail}.quantity` | 自定义 TYPE 变量字段访问 | 非法 MyBatis | 同上 |
| `#{vOrder}.customer_id` | ROWTYPE 局部变量字段访问 | 非法 MyBatis | 同上 |
| `v_rec.order_id` | RECORD FOR 循环变量 | 未转换 | 在 Service 层循环中处理 |
| `$1, $2` | EXECUTE IMMEDIATE USING | 未转换 | `#{param_1, jdbcType=X}` |

### 关键数据结构

```python
# Parameter (L650-666): proc params
@dataclass
class Parameter:
    name: str; java_type: str; sql_type: str; mode: Optional[str]

# DmlStatement (L680-687): extracted DML
@dataclass
class DmlStatement:
    sql_type: str; method_id: str; sql_text: str
    result_type: Optional[str]; parameter_types: dict
    optional_filters: list; returns_list: bool

# ProcedureInfo (L700-732): per-procedure
local_vars: dict  # name -> java_type (flat types only currently)

# PackageInfo (L736-745): per-package
custom_types: dict  # name -> {"kind": "record"/"varray", "fields": [...]}
```

### 参数流经路径（5 层改动链）

```
analyze_procedure() 填充 proc.local_vars / proc.parameters
  ↓
_build_mapper_statement() L4557: 调用 _convert_params_to_mybatis()
  ↓
_convert_params_to_mybatis() L4618: 用 \bregex 替换 → 产生 #{param}.field bug
  ↓
_build_mapper_method() L4457: 用 proc.parameters 构建 Java 签名
  ↓
_build_param_args() L4214: 用 proc.parameters 构建 Service 调用参数
  ↓
_build_test_methods() L5434: 用 proc.parameters 构建测试
```

### 判定规则

```python
def _needs_dto(proc: ProcedureInfo) -> bool:
    """判断 procedure 是否需要 DTO 方案。

    条件（任一满足即使用 DTO）：
    1. 有字段访问（DML 中出现 var.field 模式）
    2. IN 参数 > 5 个
    """
```

**为什么阈值是 5 而不是 3**：
- 测试发现场景 3（8 参数）是 DTO 优势场景
- 4-5 个参数用 @Param 仍然可读
- 场景 2（2 参数+局部变量）用 flat 更简洁

### 验证用例（已创建）

- `demo-project/sql/pkg_mapper_param_test.sql` — 9 个场景
- `demo-project/sql/pkg_mapper_param_test_tables.sql` — 3 张测试表
- 已注册到 `demo-project/fluxgauss.yaml`

---

## Task 1: 增强 `_convert_params_to_mybatis()` — 字段访问扁平化

**目标:** 修复 `#{param}.field` bug。将 `var.field` 模式转换为 `#{var_field, jdbcType=X, javaType=Y}` 扁平参数。

**Files:**
- Modify: `converter/flux_gauss.py:4618-4652` (`_convert_params_to_mybatis`)

**Step 1: 理解 bug 根因**

当前 `_convert_params_to_mybatis()` 在 L4632-4636 用 `\b{p.name}\b` 正则替换。对于参数 `p_new_rec`（ROWTYPE），正则匹配到 `p_new_rec.customer_id` 中的 `p_new_rec` 部分，将其替换为 `#{pNewRec}`，留下 `.customer_id` 尾巴 → `#{pNewRec}.customer_id`。

**Step 2: 实现 `_flatten_field_access()` 辅助函数**

在 `_convert_params_to_mybatis()` 之前添加（约 L4616 处）：

```python
def _flatten_field_access(sql: str, var_name: str, java_name: str, field_types: dict) -> tuple:
    """Replace var_name.field patterns with #{javaName_fieldName, jdbcType=X, javaType=Y}.

    Args:
        sql: SQL text to transform
        var_name: original variable name (snake_case, e.g. 'p_new_rec')
        java_name: camelCase version (e.g. 'pNewRec')
        field_types: dict of {field_name: {"jdbc": "BIGINT", "java": "Long"}}
    Returns:
        (transformed_sql, set_of_flattened_field_names)
    """
    flattened = set()
    def _replacer(m):
        field_name = m.group(1)
        field_java = snake_to_camel(field_name)
        flat_name = f"{java_name}_{field_java}"
        ft = field_types.get(field_name)
        if ft:
            placeholder = f'#{{{flat_name}, jdbcType={ft["jdbc"]}, javaType={ft["java"]}}}'
        else:
            placeholder = f'#{{{flat_name}}}'
        flattened.add(flat_name)
        return placeholder

    result = re.sub(
        rf'\b{re.escape(var_name)}\.(\w+)',
        _replacer,
        sql,
        flags=re.IGNORECASE
    )
    return result, flattened
```

**Step 3: 修改 `_convert_params_to_mybatis()` — 在简单参数替换之前，先做字段访问扁平化**

修改 L4618-4652。关键改动：**先处理 `var.field` 模式，再处理简单 `var` 模式**。

```python
def _convert_params_to_mybatis(sql: str, params: list, local_vars: dict = None,
                                field_type_map: dict = None) -> str:
    """Convert SQL parameter references to MyBatis #{{paramName}} syntax.

    Three-pass:
    1. Flatten var.field patterns → #{varField, jdbcType=X, javaType=Y}
    2. Replace simple param references → #{param, jdbcType=X, javaType=Y}
    3. Replace remaining local var references → #{var, jdbcType=X, javaType=Y}

    field_type_map: {var_name: {field_name: {"jdbc": "..", "java": "..."}}}
    """
    all_flattened = set()

    # Pass 1: Flatten field access (var.field) for params
    if field_type_map:
        for var_name, fields in field_type_map.items():
            java_name = snake_to_camel(var_name)
            sql, flattened = _flatten_field_access(sql, var_name, java_name, fields)
            all_flattened.update(flattened)

    # Pass 2: Simple param replacement (proc params)
    for p in params:
        jdbc = sql_type_to_jdbc(p.sql_type)
        java = p.java_type
        if jdbc and java:
            placeholder = f'#{{{p.java_name}, jdbcType={jdbc}, javaType={java}}}'
        else:
            placeholder = f'#{{{p.java_name}}}'
        # Use word boundary, but skip if already part of a flattened field name
        sql = re.sub(
            rf'\b{re.escape(p.name)}\b',
            placeholder,
            sql,
            flags=re.IGNORECASE
        )

    # Pass 3: Simple local var replacement
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

**注意:** 此 Task 只做扁平化修复。DTO 方案在后续 Task 中实现。扁平化是基础——即使最终采用 DTO，XML 内部的 `#{field}` 引用也需要字段类型信息，而 `_flatten_field_access()` 的 `field_types` 参数为此提供数据。

**Step 4: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
# 检查 MapperParamTestMapper.xml 不再包含 #{xxx}.yyy
grep '}\\.' dest/src/main/resources/mapper/MapperParamTestMapper.xml
# 期望：无输出
cd dest && mvn compile -q
# 期望：可能仍有 Service 层类型错误（已知缺陷），但 Mapper XML 语法正确
```

---

## Task 2: 构建 `field_type_map` — 从表定义/TYPE 定义推断字段类型

**目标:** 为每个 procedure 构建一个 `field_type_map`，提供复合类型变量的字段级类型信息。

**Files:**
- Modify: `converter/flux_gauss.py` — 在 `analyze_procedure()` 附近或新增辅助函数

**Step 1: 新增 `_build_field_type_map()` 函数**

此函数从多个来源收集字段类型信息：

```python
def _build_field_type_map(proc: ProcedureInfo, all_packages: dict = None) -> dict:
    """Build a map of {var_name: {field_name: {"jdbc": "..", "java": "..."}}}.

    Sources:
    1. %ROWTYPE params → column types from table definition
    2. %ROWTYPE local vars → column types from table definition
    3. Custom TYPE vars → field types from TYPE definition
    4. RECORD vars from FOR..IN SELECT → column types from SELECT projection (best effort)

    Returns: {"p_new_rec": {"customer_id": {"jdbc": "BIGINT", "java": "Long"}, ...}}
    """
```

**Step 2: 实现各来源的类型推断**

1. **%ROWTYPE 参数**: `proc.parameters` 中 `sql_type` 含 `%ROWTYPE` → 解析表名 → 查 `TABLE_COLUMNS` 全局缓存
2. **%ROWTYPE 局部变量**: `proc.local_vars` 中 java_type 为 `Map<String, Object>` → 追溯 `var_assignments` 查找 `SELECT * INTO var FROM table`
3. **Custom TYPE**: 从 `proc.custom_types` 或 `pkg.custom_types` 中查找字段定义
4. **RECORD FOR..IN**: 从循环 SELECT 的列名 → 查 `TABLE_COLUMNS`

**关键依赖:** 需要一个表结构缓存。检查是否已存在：

```python
# 搜索 TABLE_COLUMNS / table_columns / column_cache / schema_cache
```

**Step 3: 集成到 `analyze_procedure()` 的流程中**

`_build_field_type_map()` 需要在 `analyze_procedure()` 完成（local_vars、var_assignments 已填充）后调用。将结果存入 `proc` 的新字段 `field_type_map`。

在 `ProcedureInfo` dataclass 中添加：
```python
field_type_map: dict = field(default_factory=dict)
```

**Step 4: 传递 `field_type_map` 到 `_build_mapper_statement()`**

修改 `_build_mapper_statement()` L4557，从 `proc.field_type_map` 取值，传给 `_convert_params_to_mybatis()` 的新参数。

**Step 5: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
# 检查字段访问已转为带类型的扁平参数
grep 'pNewRec_customerId\|vDetail_quantity\|vOrder_customerId' dest/src/main/resources/mapper/MapperParamTestMapper.xml
# 期望：有输出，且包含 jdbcType/javaType
```

---

## Task 3: 实现 DTO 方案 — 判定逻辑 + DTO 类生成

**目标:** 对需要 DTO 的 procedure 生成 Java DTO 类文件。

**Files:**
- Modify: `converter/flux_gauss.py`

**Step 1: 新增判定函数 `_needs_dto()`**

```python
def _needs_dto(proc: ProcedureInfo) -> bool:
    """Determine if a procedure needs DTO parameter mode."""
    # Condition 1: has field access in DML
    for dml in proc.dml_statements:
        if re.search(r'\w+\.\w+', dml.sql_text):
            # Check if the dot-access involves a param or local var
            for p in proc.parameters:
                if re.search(rf'\b{re.escape(p.name)}\.\w+', dml.sql_text, re.IGNORECASE):
                    return True
            for var_name in proc.local_vars:
                if re.search(rf'\b{re.escape(var_name)}\.\w+', dml.sql_text, re.IGNORECASE):
                    return True
    # Condition 2: too many IN params
    in_params = [p for p in proc.parameters if not p.is_out]
    if len(in_params) > 5:
        return True
    return False
```

**Step 2: 设计 DTO 类结构**

DTO 类需要包含：
- procedure 的所有 IN 参数（作为直接字段）
- 所有被字段访问的复合类型变量的所有字段（作为扁平字段）
- 标准的 getters/setters（或 Lombok @Data）

DTO 类命名规则: `{ProcedureName}Params`（如 `CreateOrderParams`, `ProcessOrderChangeParams`）
DTO 类位置: 与 Service 同包的 `dto/` 子包，或直接放在 `mapper/` 包下

**推荐方案:** 放在 `{base_package}.dto` 包下，文件路径 `src/main/java/{base_package}/dto/{ClassName}.java`

**Step 3: 实现 `_generate_dto_class()`**

```python
def _generate_dto_class(proc: ProcedureInfo, pkg: PackageInfo) -> str:
    """Generate Java DTO class for a procedure's parameters.

    Fields include:
    - All IN parameters (flat types)
    - All flattened fields from composite type access
    """
```

**Step 4: 在 `generate_project()` 中添加 DTO 文件生成**

在 `_write_mapper_xml()` 之前，遍历所有 procedure，对 `_needs_dto()` 为 True 的生成 DTO 类文件。

```python
# 在 generate_project() 的循环中添加
dto_dir = base_path / _pkg_base_dir(pkg) / "dto"
dto_dir.mkdir(parents=True, exist_ok=True)
for proc in pkg.procedures:
    if proc.dml_statements and _needs_dto(proc):
        dto_code = _generate_dto_class(proc, pkg)
        dto_name = f"{package_to_classname(proc.proc_name)}Params"
        (dto_dir / f"{dto_name}.java").write_text(dto_code)
```

**Step 5: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
# 检查 DTO 文件已生成
ls dest/src/main/java/com/example/demo/dto/
# 期望：CreateOrderParams.java, ProcessOrderChangeParams.java 等
```

---

## Task 4: 改造 Mapper XML — DTO 模式使用 `parameterType`

**目标:** 对 DTO 模式的 procedure，Mapper XML 使用 `parameterType="...CreateOrderParams"` + 字段级 `#{field}`。

**Files:**
- Modify: `converter/flux_gauss.py:4557-4615` (`_build_mapper_statement`)
- Modify: `converter/flux_gauss.py:4589-4594` (`parameterType` 逻辑)

**Step 1: 修改 `_build_mapper_statement()` 感知 DTO 模式**

当前 L4591-4594 的 `parameterType` 逻辑只处理单类型简单场景。需要扩展：

```python
# 当前代码 (L4591-4594):
if proc.parameters:
    param_types = set(p.java_type for p in proc.parameters if not p.is_out)
    if len(param_types) == 1:
        params_attrs = f' parameterType="{list(param_types)[0].lower()}"'

# 改为:
dto_class = _get_dto_class(proc)  # None if flat mode
if dto_class:
    params_attrs = f' parameterType="{_pkg_java_package(pkg)}.dto.{dto_class}"'
elif proc.parameters:
    param_types = set(p.java_type for p in proc.parameters if not p.is_out)
    if len(param_types) == 1:
        params_attrs = f' parameterType="{list(param_types)[0].lower()}"'
```

**Step 2: DTO 模式下，XML 中的 `#{}` 引用使用 DTO 字段名**

DTO 模式下，`_convert_params_to_mybatis()` 不需要做字段扁平化（因为 DTO 自身就是扁平的）。XML 中应该用 DTO 的字段名：

- `p_customer_id` → `#{pCustomerId, jdbcType=BIGINT}`
- `p_new_rec.customer_id` → `#{pNewRecCustomerId, jdbcType=BIGINT}`
- `v_order.total_amount` → `#{vOrderTotalAmount, jdbcType=NUMERIC}`

这要求 `_build_mapper_statement()` 根据 DTO/flat 模式选择不同的替换策略。

**Step 3: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
# 检查 DTO 模式的 Mapper XML 使用 parameterType
grep 'parameterType.*Params' dest/src/main/resources/mapper/MapperParamTestMapper.xml
# 期望：CreateOrderParams, ProcessOrderChangeParams 等
```

---

## Task 5: 改造 Mapper Java 接口 — DTO 参数

**目标:** DTO 模式的 Mapper 方法使用 DTO 类作为参数。

**Files:**
- Modify: `converter/flux_gauss.py:4457-4501` (`_build_mapper_method`)

**Step 1: 修改 `_build_mapper_method()`**

```python
# 当前 (L4462-4468):
for p in proc.parameters:
    if p.is_out:
        continue
    params.append(f"@Param(\"{p.java_name}\") {p.java_type} {p.java_name}")

# 改为:
dto_class = _get_dto_class(proc)
if dto_class:
    dto_fqn = f"{_pkg_java_package(pkg)}.dto.{dto_class}"
    imports.add(f"import {dto_fqn};")
    params.append(f"{dto_class} params")
else:
    for p in proc.parameters:
        if p.is_out:
            continue
        params.append(f"@Param(\"{p.java_name}\") {p.java_type} {p.java_name}")
```

**注意:** `_build_mapper_method()` 当前没有 `pkg` 参数，需要从调用处传入。

**Step 2: 更新所有调用点**

`_build_mapper_method(proc, dml, imports)` 在哪里被调用？搜索确认：
- `_write_mapper_java()` 中循环调用

需要将 `pkg` 传入。

**Step 3: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
grep 'CreateOrderParams\|ProcessOrderChangeParams' dest/src/main/java/com/example/demo/mapper/MapperParamTestMapper.java
# 期望：DTO 类名出现在方法参数中
```

---

## Task 6: 改造 Service 层 — DTO 填充 + 传递

**目标:** Service 层在调用 DTO 模式的 Mapper 方法前，将参数填充到 DTO 对象。

**Files:**
- Modify: `converter/flux_gauss.py` — `_build_param_args()` 调用链和 Service 方法生成

**Step 1: 修改 `_build_param_args()`**

当前只传 proc.parameters 的 IN 参数。DTO 模式下需要传 DTO 对象。

但 `_build_param_args()` 被 22 处调用（Service 层各逻辑分支），不能简单改签名。

**策略:** 不改 `_build_param_args()`，而是在 Service 生成时判断：
- Flat 模式：`mapper.insertCreateOrder(pCustomerId, pProductId, ...)`
- DTO 模式：先生成 `CreateOrderParams params = new CreateOrderParams(); params.setPCustomerId(pCustomerId); ...` 然后 `mapper.insertCreateOrder(params);`

**Step 2: 在 Service 方法体生成中添加 DTO 填充逻辑**

在 `_write_service_class()` 中，当 `_needs_dto(proc)` 为 True 时：
1. 在 Service 方法开头插入 DTO 构造和字段赋值代码
2. 所有 `mapper.method({_build_param_args()})` 调用替换为 `mapper.method(params)`

这需要在 `_emit_mapper_call()` 等函数中添加 DTO 感知分支。

**Step 3: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cd dest && mvn compile -q
# 期望：BUILD SUCCESS
```

---

## Task 7: 改造 Test 类 — DTO 参数

**目标:** 生成的单元测试对 DTO 模式的 procedure 构造 DTO 对象。

**Files:**
- Modify: `converter/flux_gauss.py:5434+` (`_build_test_methods`)

**Step 1: 修改测试方法生成**

DTO 模式下，测试方法需要：
1. 构造 DTO 对象
2. 设置各字段值
3. 调用 Service 方法

**Step 2: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cd dest && mvn test -q
# 期望：所有测试编译通过（运行时可能因无数据库而失败，这是正常的）
```

---

## Task 8: 全量验证 + 回归测试

**目标:** 确保所有 20 个 package 的输出正确，无回归。

**Step 1: 全量重生成**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full
```

**Step 2: 编译验证**

```bash
cd dest && mvn compile -q
# 期望：BUILD SUCCESS
```

**Step 3: 测试验证**

```bash
cd dest && mvn test -q
# 期望：所有测试编译通过
```

**Step 4: 针对性检查 MapperParamTestMapper**

对照 9 个场景的预期输出：

| 场景 | 期望模式 | 期望参数形式 |
|------|---------|-------------|
| 1. simple_select | Flat | `#{pOrderId, jdbcType=BIGINT}` |
| 2. simple_insert | Flat | `#{pCustomerId}, #{vQty}, #{pProductId}` |
| 3. create_order | DTO | `parameterType="...CreateOrderParams"`, `#{pCustomerId}, ...` |
| 4. process_order_change | DTO | `parameterType="...ProcessOrderChangeParams"`, `#{pNewRecCustomerId}` |
| 5. create_from_detail | DTO | `parameterType="...CreateFromDetailParams"`, `#{vDetailQuantity}` |
| 6. batch_approve | Flat | `#{pApprover}, #{pMinAmount}`, 循环内 DML 在 Service 层处理 |
| 7. dynamic_update | Flat | `$1` → `#{param1}` (已知问题，不在本次范围) |
| 8. calc_order_summary | Flat | `#{pCustomerId}, #{pOrderCount}` |
| 9. comprehensive_workflow | DTO | `parameterType="...ComprehensiveWorkflowParams"` |

**Step 5: 回归检查其他 Mapper XML**

确保 `TypeTestMapper.xml`、`ComplexClearingPkgMapper.xml` 等已有文件不受影响：
```bash
# 对比 DTO 方案实施前后的已有文件
git diff --stat dest/
```

---

## 实施顺序与依赖

```
Task 1 (字段访问扁平化)
  ↓
Task 2 (构建 field_type_map)
  ↓  (并行可开始)
Task 3 (DTO 判定 + 类生成)
  ↓
Task 4 (Mapper XML DTO 模式)
  ↓
Task 5 (Mapper Java DTO 参数)
  ↓
Task 6 (Service DTO 填充) ← 最复杂，改动最多
  ↓
Task 7 (Test DTO 参数)
  ↓
Task 8 (全量验证)
```

**风险评估:**
- Task 6（Service 层 DTO 填充）改动面最大（22 处 `_build_param_args` 调用点），是最高风险点
- Task 2（field_type_map 构建）依赖表结构缓存是否存在，如不存在需新建
- Task 1 可以独立完成，即使不做 DTO 也能修复 `#{param}.field` bug

**建议实施顺序:** Task 1 → Task 2 → Task 8（验证扁平化修复）→ Task 3-7（DTO 方案）
