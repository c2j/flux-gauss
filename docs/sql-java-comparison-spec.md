# SQL ↔ Java 转换等价性对比 — 需求规格

> 本文档定义了可复用的对比框架，供后续转换器优化后再次执行对比时参照。

---

## 1. 对比目标

将 `demo-project/sql/` 下的原始 SQL 存储过程与转换器生成的 Java + MyBatis 代码进行**语义等价性**对比，识别转换结果与原始 SQL 之间的差异。

**不做**：
- 不修改任何源代码或生成代码
- 不评估转换器本身的代码质量（只关注转换结果的等价性）
- 不做性能对比
- 不涉及"哪个转换器更好"的价值判断

---

## 2. 输入文件

### 2.1 SQL 源文件（44 个）

路径：`demo-project/sql/*.sql`

分为三类：

| 类别 | 数量 | 说明 |
|------|------|------|
| **有 Java 输出（需对比）** | 37 | 包含 CREATE PROCEDURE/FUNCTION 的业务包 |
| **纯 DDL（跳过）** | 5 | tables.sql, missing_tables.sql, pkg_mapper_param_test_tables.sql, PKG_WARPDRIVER_STRESS_TEST-DDL.sql, DAT_DATACLEAR_CONFIG.sql |
| **MERGE（暂不支持）** | 2 | pkg_merge_example.sql, pkg_merge_fix1.sql |

### 2.2 转换输出目录

| 标识 | 路径 | 说明 |
|------|------|------|
| **dest_py** | `dest_py/` | Python 转换器（flux_gauss.py）输出 |
| **dest_ru** | `dest_ru/` | Rust 转换器输出 |

输出文件结构（每个 SQL 包对应 3-4 个文件）：
```
src/main/java/ced/service/{Name}Service.java   — 业务逻辑
src/main/java/ced/mapper/{Name}Mapper.java      — Mapper 接口
src/main/resources/mapper/{Name}Mapper.xml      — MyBatis SQL 映射
src/test/java/ced/service/{Name}ServiceTest.java — 单元测试（可选）
```



## 3. 对比方法

### 3.1 对比粒度

**逐过程（procedure）对比**：SQL 中的每个 `CREATE PROCEDURE` / `CREATE FUNCTION` 对应 Java Service 中的一个 `public` 方法。

### 3.2 对比维度（8 个）

| # | 维度 | 说明 | 检查要点 |
|---|------|------|----------|
| 1 | **过程覆盖率** | 每个 SQL procedure 是否都有对应的 Java 方法 | 提取 SQL 中所有 CREATE PROCEDURE/FUNCTION 名，与 Java public 方法名 1:1 对照 |
| 2 | **参数映射** | 名称、类型(IN/OUT/INOUT)是否正确 | SQL 参数名 → Java 方法参数名；SQL 类型 → Java 类型（NUMBER→BigDecimal 等）；IN/OUT/INOUT 模式是否保留 |
| 3 | **控制流** | IF/FOR/WHILE/GOTO 转换是否等价 | IF/ELSIF→if/else if；FOR→for；WHILE→while；LOOP→while(true)；GOTO→标签变量/状态机——每条路径可达 |
| 4 | **DML 等价性** | Mapper XML 中的 SQL 与原始 DML 语义一致 | 表名、字段列表、WHERE 条件、JOIN 关系、占位符参数（`#{}`）是否对应 |
| 5 | **游标生命周期** | OPEN/FETCH/CLOSE → mapper.select() | 每个 OPEN 都生成了 mapper SELECT；FETCH 有行提取；REFCURSOR 正确物化为查询 |
| 6 | **异常处理** | EXCEPTION WHEN → try/catch | 每个 EXCEPTION WHEN 块都有对应 catch；SQLERRM/SQLSTATE 信息保留 |
| 7 | **函数调用** | SQL 内置函数 → Java 等价实现 | substr→substring, upper→toUpperCase, nvl→Optional, coalesce→Optional, to_char→DateTimeFormatter 等 |
| 8 | **注释保留** | SQL 注释是否带入 Java 代码 | leading_comments（过程前注释）和 inline_comments（过程内注释） |

### 3.3 差异分级

| 严重程度 | 定义 | 示例 |
|----------|------|------|
| **🔴 Critical** | 语义不等价：运行结果与 SQL 不同 | SELECT INTO 变量未解包导致错误值；游标循环体为死代码；常量值全部为 0 |
| **🟡 Major** | 功能缺失：过程未转换或关键特性丢失 | 整个文件未生成输出；MERGE INTO 不支持；RETURNING INTO 未捕获 |
| **🟢 Minor** | 风格差异：不影响功能的偏差 | 注释丢失；源码行号不准确；String.valueOf 不必要包装 |

---

## 4. 文件分组（便于并行对比）

将 37 个可比较 SQL 文件按特性分组，每组可独立对比：

| 组 | 文件数 | SQL 文件 | 核心特性 |
|----|--------|----------|----------|
| **CRUD 样式** | 4 | gauss_select/insert/update/delete_all_styles.sql | 各种 SELECT/INSERT/UPDATE/DELETE 写法（120 个过程） |
| **核心业务** | 4 | pkg_order.sql, pkg_payment.sql, pkg_inventory.sql, pkg_product.sql | 业务逻辑、跨包调用、事务、异常处理 |
| **报表/通用** | 2 | pkg_report.sql, pkg_common.sql | 公共工具方法、报表生成 |
| **游标模式** | 2 | pkg_cursor_patterns.sql, pkg_employee_comments.sql | OPEN/FETCH/CLOSE、REFCURSOR |
| **控制流** | 3 | proc_GOto.sql, proc_Five_Gotos.sql, gauss_update_select.sql | GOTO 跳转、状态机模拟 |
| **游标/FOR/常量** | 3 | PKG_CURSOR.sql, PKG_FOR.sql, gauss_package_constants.sql | 游标拆分、FOR IN SELECT、包常量 |
| **函数调用** | 3 | gauss_function_calls.sql, astro_functions_pkg.sql, gauss_complete_examples.sql | SQL 内置函数、天文函数、完整示例 |
| **类型/变量/函数** | 4 | pkg_type_test.sql, pkg_package_vars_test.sql, pkg_builtin_funcs_test.sql, pkg_custom_funcs_test.sql | TYPE/RECORD、%TYPE/%ROWTYPE、60+ 内置函数、自定义函数 |
| **复杂业务** | 3 | complex_clearing_pkg.sql, PKG_AAS_DATACLEAR.sql, pkg_aas_lob_dataclear.sql | 清算逻辑、LOB 操作、DBE_SCHEDULER |
| **压力/批量** | 3 | PKG_WARPDRIVER_STRESS_TEST.sql, PKG_RPT_BATCH_DOWNLOAD.sql, PKG_2008802001_MGT.sql | 大量 GOTO、CLOB、动态 SQL |
| **金融/日志** | 5 | PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql, PACK_LOG.sql, DB_LOG.sql, SWH_ALL_KIND.sql, pkg_test_patterns.sql | 子查询、PRAGMA AUTONOMOUS_TRANSACTION、多文件合并 |
| **参数/模式** | 2 | pkg_mapper_param_test.sql, pkg_test_patterns.sql | 参数传递机制、FOR/WHILE/嵌套 IF |

### 特殊映射关系

部分多个 SQL 文件合并到同一个 Java 类，需交叉验证：

| Java 类 | SQL 文件 |
|---------|----------|
| PackLogService | PACK_LOG.sql + DB_LOG.sql |
| TestService | SWH_ALL_KIND.sql + pkg_test_patterns.sql |

### dest_ru 额外拆分

| SQL 文件 | dest_py 类 | dest_ru 类 |
|----------|-----------|-----------|
| PKG_CURSOR.sql | DynamicForLoopService（1 个） | CursorAdvancedService + CursorLifecycleService + CursorPatternsService（3 个） |
| PKG_FOR.sql | ForInSelectService（1 个） | OpenCursorService + ForInSelectService（2 个） |
| gauss_function_calls.sql | GaussFunctionCallsService | FunctionCallsService（命名不同） |

---

## 5. 报告输出格式

保存路径：`docs/sql-java-comparison-report-Vx.md`

### 报告结构

```
1. 概述（比较范围、方法、统计摘要）
2. 差异总览表（按严重程度 × 差异类型的汇总表）
3. dest_py 详细差异
   3.1 Critical 差异（每个条目：编号、文件、过程名、问题描述、影响、根因）
   3.2 Major 差异
   3.3 Minor 差异
   3.4 优势总结
4. dest_ru 详细差异（同上结构）
5. 未转换文件分析（MERGE 文件原因、DDL 文件确认）
6. 原因分析汇总（共享问题 + 各转换器特有问题）
7. 改进建议（按优先级 P0/P1/P2 分级，含具体技术方案）
8. 附录：完整文件映射表（44 行，含对比深度标记）
9. 结论（核心发现、推荐优先修复）
```

### 每个差异条目必须包含

- **编号**：C-PY-XX 或 C-RU-XX（Critical）、M-PY-XX 或 M-RU-XX（Major）
- **文件**：SQL 源文件名
- **过程名**：SQL procedure/function 名 → Java 方法名
- **问题描述**：具体什么不等价
- **影响**：对运行结果的实际影响
- **根因**（Critical 必须）：转换器哪一步出了问题

---

## 6. 基线结果（2026-05-23 首次对比）

供后续对比时衡量改进幅度：

| 指标 | dest_py 首次 | dest_ru 首次 |
|------|-------------|-------------|
| 有输出的 SQL 文件 | 37/37 | 35/37 |
| 过程级覆盖率 | ~95% | ~80% |
| Critical 差异 | ~25 | ~35 |
| Major 差异 | ~18 | ~22 |
| Minor 差异 | ~20 | ~30 |
| 综合评级 | B | C+ |

### 共同 Critical 问题（优化后应优先验证）

| 问题 | 影响范围 | 验证方法 |
|------|----------|----------|
| SELECT INTO 变量未解包 | 取消订单释放库存(0,0) | 检查 cancelOrder 中 vProductId/vQty 是否从 Map 提取 |
| MERGE INTO 完全缺失 | 3+ 过程为空壳 | 检查 demo_12/demo_17/demo_18 方法体是否非空 |
| RETURNING INTO 未捕获 | 6+ 过程丢失返回值 | 检查 INSERT ... RETURNING 是否生成 useGeneratedKeys |
| FORALL BULK 语义丢失 | 批量操作失效 | 检查 demo_11/demo_15 是否有批量执行逻辑 |

### dest_ru 高优先级问题（优化后应优先验证）

| 问题 | 影响范围 | 验证方法 |
|------|----------|----------|
| 游标 OPEN 未生成 mapper SELECT | 5+ 过程死代码 | 检查 CursorPatternsMapper.xml 是否有 selectPrcCursorWalk |
| REFCURSOR 返回空列表 | 分页查询不可用 | 检查 listByDept 是否返回 Collections.emptyList() |
| pkg_type_test 整文件缺失 | 14 过程 | 检查 dest_ru 中是否有 TypeTestService.java |
| 包常量值全为 0/null | 所有常量 | 检查 CompanyConstantsService 中 COMPANYNAME 是否为 null |
| GOTO 状态机无条件覆盖 | 状态机失效 | 检查 sp_order_state_machine 中 case 后是否有无条件 Done |

---

## 7. 执行检查清单

重新对比时，按此清单执行：

```bash
# Step 1: 重新转换（清除旧输出）
rm -rf dest_py/.fluxgauss    # 清除 Python 转换器缓存
python3 converter/flux_gauss.py -c fluxgauss.yaml

# Step 2: 确认输出文件完整
ls dest_py/src/main/java/ced/service/*.java | wc -l   # 应 35+
ls dest_ru/src/main/java/ced/service/*.java | wc -l   # 应 35+

# Step 3: 执行对比（参照第 4 节分组，按组逐一对比）
# 每组对比时：
#   1. 从 SQL 提取所有 CREATE PROCEDURE/FUNCTION 名
#   2. 从 Java Service 提取所有 public 方法名
#   3. 逐一比对 8 个维度（第 3.2 节）
#   4. 差异按 3 级分级（第 3.3 节）

# Step 4: 汇总报告（按第 5 节格式）

# Step 5: 与基线对比（第 6 节）
# 特别关注：Critical 数量是否减少？高优先级问题是否修复？
```
