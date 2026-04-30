# Comment Preservation — SQL 注释迁移到 Java

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将原始 SQL 存储过程中的注释（`--` 单行 / `/* */` 块注释）带入迁移后的 Java 源码，增强代码可理解性。

**Architecture:** ogsql-parser 已支持 `--comments` 参数输出 `comments` 数组。FluxGauss 在调用 ogsql 时启用此参数，从 AST JSON 提取注释，按行号区间映射到 ProcedureInfo，在代码生成阶段将注释注入 Java Service/Mapper/Test。

**Tech Stack:** Python 3.9+, ogsql-parser (已内置 `--comments` 支持)

---

## 前置知识

### 文件结构
- **唯一需要修改的文件**: `converter/flux_gauss.py`（4795 行）
- ogsql-parser binary 已更新，支持 `--comments` 全局参数

### 关键数据流
```
SQL file → parse_sql_file() → AST JSON (含 comments[])
                                    ↓
                          extract_procedures() → ProcedureInfo[]
                                    ↓
                          analyze_procedure() → 填充 dml/java_logic
                                    ↓
                          generate_project() → Java files
```

### 注释映射策略
```
SQL 注释位置                        → Java 输出位置
───────────────────────────────────────────────────
过程声明前的连续注释 (leading)        → 方法 // Source: 之后的 // 行
过程体内的注释 (inline)              → 方法体开头的 // 行
不属于任何过程的注释                 → 类级别 // Source: 之后的 // 行
```

### 现有注入点（代码行号参考当前版本）

| 函数 | 行号 | 注入位置 |
|------|------|---------|
| `_write_service_class()` | ~3354-3357 | 类级 `// Source: {file}` 之后 |
| `_build_service_method()` | ~3594-3595 | 方法级 `// Source:` 之后 |
| `_build_mapper_method()` | ~3136-3138 | Mapper 方法注释 |
| `_build_mapper_statement()` | ~3238-3241 | XML 注释 |
| `_write_test_class()` | ~3749-3751 | 测试类 `// Source:` 之后 |

---

## Task 1: 启用 ogsql `--comments` 并提取注释数据

**Files:**
- Modify: `converter/flux_gauss.py:645-684` (`parse_sql_file`)

**Step 1: 修改 `parse_sql_file()` — 给 ogsql 命令加 `--comments` 参数**

在 `parse_sql_file()` 中，两处调用 ogsql 的命令列表里插入 `"--comments"`：

```python
# 第一处（单语句，约 line 653-654）
result = subprocess.run(
    [OGSQL_BIN, "--comments", "-f", sql_path, "parse", "-j"],
    capture_output=True, text=True
)

# 第二处（拆分语句，约 line 667-669）
result = subprocess.run(
    [OGSQL_BIN, "--comments", "-f", tmp_path, "parse", "-j"],
    capture_output=True, text=True, timeout=10,
)
```

同时在合并 AST 时也合并 comments：

```python
# 约在 line 661，初始化时加入 comments
combined_ast = {"statements": [], "errors": [], "comments": []}

# 约在 line 674-675，extend 时也合并 comments
combined_ast["statements"].extend(stmt_ast.get("statements", []))
combined_ast["errors"].extend(stmt_ast.get("errors", []))
combined_ast["comments"].extend(stmt_ast.get("comments", []))
```

**Step 2: 验证 — 手动运行确认 AST JSON 包含 comments**

```bash
python3 -c "
import sys; sys.path.insert(0, 'converter')
from flux_gauss import parse_sql_file
import json
ast = parse_sql_file('demo-project/sql/pkg_order.sql')
print('comments count:', len(ast.get('comments', [])))
for c in ast.get('comments', []):
    print(json.dumps(c, ensure_ascii=False))
"
```

Expected: `comments count: 0`（pkg_order.sql 无注释）。换一个有注释的文件应有输出。

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: enable --comments flag in ogsql parse calls"
```

---

## Task 2: 新增数据模型 — CommentInfo + ProcedureInfo 扩展

**Files:**
- Modify: `converter/flux_gauss.py` (~line 529-560)

**Step 1: 在 `DmlStatement` 之前添加 `CommentInfo` dataclass**

```python
@dataclass
class CommentInfo:
    """A single SQL comment with source position."""
    text: str          # 原始注释文本，保留 -- 或 /* */ 前后缀
    line: int          # 起始行号 (1-based)
    end_line: int      # 结束行号
    column: int        # 列号
    comment_type: str  # "line" 或 "block"
```

**Step 2: 给 `ProcedureInfo` 添加注释字段**

在 `source_end_line: int = 0` 之后添加：

```python
    leading_comments: list = field(default_factory=list)   # List[CommentInfo] — 过程声明前的注释
    inline_comments: list = field(default_factory=list)    # List[CommentInfo] — 过程体内的注释
```

**Step 3: 给 `PackageInfo` 添加注释字段**

在 `source_file: str = ""` 之后添加：

```python
    comments: list = field(default_factory=list)  # List[CommentInfo] — 不属于任何过程的注释
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: add CommentInfo dataclass and comment fields to ProcedureInfo/PackageInfo"
```

---

## Task 3: 注释提取与映射逻辑

**Files:**
- Modify: `converter/flux_gauss.py` — 在 `extract_procedures()` 之后添加新函数，并修改调用链

**Step 1: 添加 `extract_comments()` 函数**

在 `extract_procedures()` 函数之后（约 line 803）添加：

```python
def extract_comments(ast: dict) -> list:
    """Extract CommentInfo list from AST JSON comments array."""
    comments = []
    for c in ast.get("comments", []):
        comments.append(CommentInfo(
            text=c.get("text", ""),
            line=c.get("line", 0),
            end_line=c.get("end_line", 0),
            column=c.get("column", 0),
            comment_type=c.get("type", "line"),
        ))
    return comments


def _map_comments_to_procedures(comments: list, procedures: list, source_file: str = ""):
    """Assign comments to procedures based on line number proximity.

    Rules:
    - Comments between prev_proc end and current proc start → leading_comments
    - Comments between proc start_line and end_line → inline_comments
    - Comments not inside any procedure → returned as package-level comments
    """
    if not comments or not procedures:
        return comments  # all become package-level

    # Sort procedures by start line
    sorted_procs = sorted(procedures, key=lambda p: p.source_start_line)

    package_level = []

    for comment in comments:
        # Check if comment is inside any procedure body
        target_proc = None
        for proc in sorted_procs:
            if proc.source_start_line <= comment.line <= proc.source_end_line:
                target_proc = proc
                break

        if target_proc:
            target_proc.inline_comments.append(comment)
            continue

        # Check if comment is a leading comment (before a procedure)
        assigned = False
        for proc in sorted_procs:
            # Find the procedure whose start_line is closest AFTER this comment
            # Comment should be between previous proc end and this proc start
            prev_end = 0
            idx = sorted_procs.index(proc)
            if idx > 0:
                prev_end = sorted_procs[idx - 1].source_end_line

            if prev_end < comment.line < proc.source_start_line:
                proc.leading_comments.append(comment)
                assigned = True
                break

        if not assigned:
            package_level.append(comment)

    return package_level
```

**Step 2: 修改主管线 — 在 `extract_procedures()` 之后调用映射**

在主入口函数中（约 line 4678），找到：

```python
            procedures, pkg_vars = extract_procedures(ast, source_file=basename)
```

在其后添加：

```python
            comments = extract_comments(ast)
            pkg_level_comments = _map_comments_to_procedures(comments, procedures, source_file=basename)
```

然后在创建 `PackageInfo` 时（约 line 4684）添加 `comments=pkg_level_comments`：

```python
            pkg = PackageInfo(
                package_name=pkg_name,
                procedures=procedures,
                package_vars=pkg_vars,
                source_file=basename,
                java_package=sql_file_to_java_package.get(sql_file, ""),
                comments=pkg_level_comments,
            )
```

**Step 3: 验证 — 打印注释映射结果**

```bash
python3 -c "
import sys; sys.path.insert(0, 'converter')
from flux_gauss import *
import json

# 使用有注释的测试文件
with open('/tmp/test_proc_comments.sql', 'w') as f:
    f.write('''-- 这是创建订单的存储过程
-- 作者: admin
CREATE OR REPLACE PROCEDURE pkg_test.demo(
    p_id BIGINT
) AS \$\$
DECLARE
    v_count INT;  -- 记录总数
BEGIN
    -- 插入新记录
    INSERT INTO t_test(id) VALUES (p_id);
    /* 批量更新
       注意并发问题 */
    UPDATE t_test SET name = 'x' WHERE id = p_id;
END;
\$\$ LANGUAGE plpgsql;
''')

ast = parse_sql_file('/tmp/test_proc_comments.sql')
comments = extract_comments(ast)
print(f'Total comments: {len(comments)}')
for c in comments:
    print(f'  L{c.line}: {c.text!r}')

procedures, _ = extract_procedures(ast, source_file='test.sql')
pkg_level = _map_comments_to_procedures(comments, procedures, 'test.sql')

for proc in procedures:
    print(f'\nProcedure {proc.name} (L{proc.source_start_line}-{proc.source_end_line}):')
    print(f'  Leading: {[c.text for c in proc.leading_comments]}')
    print(f'  Inline: {[c.text for c in proc.inline_comments]}')
print(f'Package-level: {[c.text for c in pkg_level]}')
"
```

Expected output:
```
Total comments: 5
Procedure pkg_test.demo (L3-L16):
  Leading: ['-- 这是创建订单的存储过程', '-- 作者: admin']
  Inline: ['-- 记录总数', '-- 插入新记录', '/* 批量更新\n       注意并发问题 */']
Package-level: []
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: extract and map SQL comments to procedures by line proximity"
```

---

## Task 4: Java Service 代码生成 — 注入注释

**Files:**
- Modify: `converter/flux_gauss.py` (`_write_service_class`, `_build_service_method`)

**Step 1: 添加注释格式化辅助函数**

在 `_build_service_method()` 之前添加：

```python
def _format_comment_for_java(comment: CommentInfo) -> str:
    """Format a SQL comment as a Java comment line."""
    text = comment.text
    # Strip SQL comment markers and trim
    if text.startswith('--'):
        text = text[2:].strip()
    elif text.startswith('/*') and text.endswith('*/'):
        text = text[2:-2].strip()
        # For multi-line block comments, join lines
        text = ' '.join(line.strip() for line in text.split('\n') if line.strip())
    return f"// {text}" if text else ""
```

**Step 2: 修改 `_build_service_method()` — 在 `// Source:` 行之后注入 leading + inline 注释**

找到（约 line 3593-3595）：

```python
    method_lines = []
    source_info = f"{proc.source_file}:{proc.source_start_line}-{proc.source_end_line}" if proc.source_file else ""
    method_lines.append(f"    // Source: {proc.name} ({'FUNCTION' if proc.is_function else 'PROCEDURE'}) — {source_info}")
```

在其后（`if has_complex_issues:` 之前）添加注释注入：

```python
    # Inject original SQL comments
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            method_lines.append(f"    {formatted}")
    for c in proc.inline_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            method_lines.append(f"    {formatted}")
```

**Step 3: 修改 `_write_service_class()` — 在类级 `// Source:` 之后注入 package-level 注释**

找到（约 line 3355-3357）：

```python
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    lines.append(f"public class {class_name} {{")
```

修改为：

```python
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    # Inject package-level comments
    for c in pkg.comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            lines.append(formatted)
    lines.append(f"public class {class_name} {{")
```

**Step 4: 验证 — 生成并检查 Java Service 输出**

```bash
rm -rf dest/.fluxgauss
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
# 检查生成的 Service 文件中是否有注释
grep -n "// " dest/src/main/java/com/example/demo/service/*.java | head -20
```

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: inject SQL comments into Java Service class and methods"
```

---

## Task 5: Mapper 接口和 XML — 注入注释

**Files:**
- Modify: `converter/flux_gauss.py` (`_build_mapper_method`, `_build_mapper_statement`)

**Step 1: 修改 `_build_mapper_method()` — 在 `// Source:` 行之后注入 leading 注释**

找到（约 line 3136-3138）：

```python
    source_info = f"// {proc.source_file}:{proc.source_start_line} — {proc.name}" if proc.source_file else ""
    prefix = f"    {source_info}\n    " if source_info else "    "
    return f"{prefix}{ret} {method_name}({params_str});"
```

修改为：

```python
    source_info = f"// {proc.source_file}:{proc.source_start_line} — {proc.name}" if proc.source_file else ""
    comment_lines = ""
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            comment_lines += f"    {formatted}\n"
    prefix = f"    {source_info}\n{comment_lines}    " if source_info else f"{comment_lines}    "
    return f"{prefix}{ret} {method_name}({params_str});"
```

**Step 2: 修改 `_build_mapper_statement()` — XML 中注入注释**

找到（约 line 3238-3241）：

```python
    source_info = f"Source: {proc.source_file}:{proc.source_start_line}-{proc.source_end_line} — {proc.name}.{dml.method_id}" if proc.source_file else f"Source: {proc.name}.{dml.method_id}"
    comment_prefix = f"<!-- {source_info} -->\n" if source_info else ""
```

在其后添加 leading comments：

```python
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            comment_prefix += f"<!-- {formatted.lstrip('/ ').strip()} -->\n"
```

**Step 3: 验证 — 检查 Mapper 文件**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
grep -n "original\|Source\|<!--" dest/src/main/resources/mapper/*.xml | head -20
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: inject SQL comments into Mapper interface and XML"
```

---

## Task 6: 端到端验证 + 回归测试

**Files:**
- 无代码修改，纯验证

**Step 1: 创建带注释的测试 SQL**

```sql
-- 包级注释：订单管理模块
-- 作者: admin, 创建日期: 2024-01-01
CREATE OR REPLACE PROCEDURE pkg_test.create_order(
    p_user_id BIGINT,
    p_product_id BIGINT
) AS $$
BEGIN
    -- 验证库存
    INSERT INTO t_orders(user_id, product_id) VALUES (p_user_id, p_product_id);
    /* 记录操作日志 */
    PERFORM pkg_common.log_operation('ORDER', 'CREATE', p_user_id);
END;
$$ LANGUAGE plpgsql;

-- 取消订单
CREATE OR REPLACE PROCEDURE pkg_test.cancel_order(
    p_order_id BIGINT
) AS $$
BEGIN
    UPDATE t_orders SET status = 'CANCELLED' WHERE id = p_order_id;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: 运行 FluxGauss 并检查输出**

```bash
python3 converter/flux_gauss.py -o /tmp/test_output -s /tmp/test_comment_migration.sql
```

验证点：
1. ✅ Service 类类级注释包含 `// 包级注释：订单管理模块`
2. ✅ `createOrder` 方法 `// Source:` 之后包含 `-- 验证库存` 和 `/* 记录操作日志 */`
3. ✅ `cancelOrder` 方法 `// Source:` 之后包含 `-- 取消订单`
4. ✅ Mapper 接口包含对应注释
5. ✅ Mapper XML 包含对应注释

**Step 3: 全量回归测试 — demo 项目**

```bash
rm -rf dest/.fluxgauss
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cd dest && mvn compile
```

Expected: 0 errors，所有 47 个过程正常生成。

**Step 4: Final Commit**

```bash
git add -A
git commit -m "feat: complete SQL comment preservation in Java migration"
```
