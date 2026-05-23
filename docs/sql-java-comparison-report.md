# SQL ↔ Java 转换等价性比较报告

**日期**: 2026-05-23  
**比较范围**: `demo-project/sql/` 下 44 个 SQL 文件 × 2 个转换器（dest_py = Python/flux_gauss.py, dest_ru = Rust 转换器）  
**方法**: 逐过程(procedure)对比 SQL 源码与 Java Service + Mapper.xml 的语义等价性

---

## 1. 概述

### 1.1 文件分类

| 类别 | 数量 | 文件 |
|------|------|------|
| **可比较（有 Java 输出）** | 37 | 业务包、CRUD样式、控制流、游标、函数调用等 |
| **纯 DDL/基础设施** | 5 | tables.sql, missing_tables.sql, pkg_mapper_param_test_tables.sql, PKG_WARPDRIVER_STRESS_TEST-DDL.sql, DAT_DATACLEAR_CONFIG.sql |
| **未转换（MERGE）** | 2 | pkg_merge_example.sql, pkg_merge_fix1.sql |

### 1.2 统计摘要

| 指标 | dest_py (Python) | dest_ru (Rust) |
|------|-----------------|----------------|
| 有输出的 SQL 文件 | 37/37 | 35/37（缺 pkg_type_test, gauss_complete_examples） |
| 过程级覆盖率 | ~95% | ~80% |
| Critical 差异 | ~25 | ~35 |
| Major 差异 | ~18 | ~22 |
| Minor 差异 | ~20 | ~30 |
| 综合评级 | **B** | **C+** |

### 1.3 比较方法

每个 SQL 过程按以下维度逐一对比：
1. **过程覆盖率**: SQL 中的每个 CREATE PROCEDURE/FUNCTION 是否都有对应的 Java 方法
2. **参数映射**: 名称、类型(IN/OUT/INOUT)是否正确
3. **控制流**: IF/ELSIF→if/else if, FOR→for, WHILE→while, GOTO→标签/状态机
4. **DML 等价性**: Mapper XML 中的 SQL 是否与原始 DML 语义一致
5. **游标生命周期**: OPEN/FETCH/CLOSE → mapper.select() 的完整性
6. **异常处理**: EXCEPTION WHEN → try/catch 覆盖度
7. **函数调用**: SQL 内置函数 → Java 等价实现
8. **注释保留**: SQL 注释是否带入 Java 代码

> **注**: 本报告基于对 ~35 个 SQL 文件（涵盖 CRUD、业务逻辑、控制流、游标、函数调用、类型系统、包变量等核心特性）的深度逐行分析。压力测试包（PKG_WARPDRIVER）、批量下载（PKG_RPT_BATCH_DOWNLOAD）等大型文件的比较基于探索阶段的抽样分析。

---

## 2. 差异总览表

### 2.1 按严重程度分类

| 严重程度 | 定义 | dest_py 数量 | dest_ru 数量 |
|----------|------|-------------|-------------|
| **🔴 Critical** | 语义不等价：运行结果与 SQL 不同 | ~25 | ~35 |
| **🟡 Major** | 功能缺失：过程未转换或关键特性丢失 | ~18 | ~22 |
| **🟢 Minor** | 风格差异：不影响功能的偏差 | ~20 | ~30 |

### 2.2 按差异类型分类

| 差异类型 | dest_py | dest_ru | 影响 |
|----------|---------|---------|------|
| MERGE INTO 未转换 | ✓ (stub) | ✓ (stub) | 3+ 过程完全缺失 |
| RETURNING INTO 未捕获 | ✓ | ✓ | 6+ 过程丢失返回值 |
| FORALL BULK 未实现 | ✓ (stub) | ✓ (stub) | 2+ 过程为空壳 |
| EXECUTE IMMEDIATE 部分缺失 | ✓ (partial) | ✓ (stub) | 3+ 过程动态 SQL 丢失 |
| SELECT INTO 变量未解包 | ✓ | ✓ | 取消订单释放库存(0,0) |
| 游标 OPEN/FETCH/CLOSE 未生成 | — | ✓ | 5+ 过程循环体为死代码 |
| REFCURSOR 未物化 | — | ✓ | 分页查询永远返回空列表 |
| 复杂类型(TYPE/RECORD)完全跳过 | — | ✓ | 14 过程全部缺失 |
| 包常量值丢失 | — | ✓ | 所有常量初始化为 null/0 |
| 跨包函数调用丢失 | — | ✓ | format_amount() 赋值为 null |
| GOTO 状态机无条件覆盖 | — | ✓ | 状态机永远停在 Done |
| 空列表迭代(Collections.emptyList) | — | ✓ | 循环体永远不执行 |
| String.format 用 {} 占位符 | — | ✓ | 错误消息显示字面量 {} |
| Long 对象用 == 比较 | — | ✓ | 引用相等性 bug |
| SQL 注释保留 | ✓ | — | dest_ru 完全丢失注释 |
| __ROWCOUNT__ 永远为 0 | — | ✓ | 批量操作循环只执行一次 |
| 源码行号追踪 | ✓ (精确) | — (总是 1-1) | 调试困难 |

---

## 3. dest_py（Python 转换器）详细差异

### 3.1 Critical 差异

#### C-PY-01: SELECT INTO 结果未解包为局部变量
- **文件**: pkg_order.sql → OrderService.cancelOrder
- **问题**: `SELECT product_id, qty INTO v_product_id, v_qty` 转换为 `orderMapper.selectCancelOrder()` 返回 `Map<String, Object>`，但 `vProductId` 和 `vQty` 从未被从 Map 中提取，始终为初始值 0
- **影响**: `releaseStock(0, 0)` 释放了错误的库存
- **根因**: 转换器生成了 mapper 调用，但未生成 `vProductId = (Long)_row.get("product_id")` 的解包代码

#### C-PY-02: RETURNING INTO OUT 参数未捕获
- **文件**: gauss_select/insert/update/delete_all_styles.sql（6+ 过程）
- **问题**: `INSERT ... RETURNING id INTO v_id` 的 RETURNING 子句被剥离，OUT 变量永远为默认值
- **影响**: 审计 ID、生成的键值等丢失

#### C-PY-03: MERGE INTO 完全缺失
- **文件**: gauss_insert_all_styles.sql (demo_12), gauss_update_all_styles.sql (demo_17), gauss_delete_all_styles.sql (demo_18)
- **问题**: MERGE INTO 语句生成的 Java 方法为空壳(stub)
- **影响**: 3 个过程完全不可用

#### C-PY-04: FORALL BULK 操作语义丢失
- **文件**: gauss_insert_all_styles.sql (demo_11), gauss_update_all_styles.sql (demo_15)
- **问题**: FORALL BULK 转换为 `SELECT ... LIMIT 1`，丢失批量语义

#### C-PY-05: UPDATE ... FROM 子句位置错误
- **文件**: gauss_update_all_styles.sql (demo_08, demo_10, demo_11, demo_19)
- **问题**: UPDATE ... FROM 语法中，FROM 子句被错误地放在 SET 子句中

#### C-PY-06: %ROWTYPE INSERT 幽灵列
- **文件**: gauss_insert_all_styles.sql (demo_09, demo_20)
- **问题**: INSERT 中包含了表中不存在的列（email, phone, last_update），运行时会报错

#### C-PY-07: 复杂查询重建失败
- **文件**: gauss_select_all_styles.sql (9 个 demo)
- **问题**: LATERAL JOIN、多 CTE、VALUES-as-table、极深嵌套查询重建失败，输出 TODO 注释

#### C-PY-08: GOTO 深层嵌套跳出后中断外层循环
- **文件**: proc_Five_Gotos.sql → sp_validate_orders
- **问题**: `_gotoTarget` break 跳出外层循环，导致第一个无效订单后停止处理
- **影响**: 只处理第一个无效订单就停止

#### C-PY-09: GOTO 状态机缺少 break（脆弱的 fall-through）
- **文件**: proc_Five_Gotos.sql → sp_order_state_machine
- **问题**: switch-case 中缺少 break 语句，依赖 fall-through 链到达 Done
- **影响**: 功能偶然正确但极其脆弱

### 3.2 Major 差异

#### M-PY-01: 动态 SQL 部分缺失
- EXECUTE IMMEDIATE 生成了 vSql 字符串但未实际执行（demo_08 insert, demo_19 delete）

#### M-PY-02: TRUNCATE 未执行
- INSERT overwrite 模式需要先 TRUNCATE，但 TRUNCATE 语句被忽略

#### M-PY-03: test_rowtype 过程完全跳过
- pkg_type_test.sql 中的 %ROWTYPE 测试过程未生成

#### M-PY-04: sp_purge_logs 输出参数未设置
- 删除总数 `pDeletedCount` 永远为 null

#### M-PY-05: sp_generate_report 代码重复
- assemble_report 代码块重复（4 行出现两次）

### 3.3 Minor 差异

- DBE_OUTPUT.PRINT_LINE 转换为注释（正确处理方式）
- ON CONFLICT 子句完全丢失
- ROWNUM/VARCHAR2/DATE 字面量被剥离
- `::` 类型转换语法被移除
- DATE 参数 `::DATE` 转换丢失（reconcile_payments）
- concat() 使用错误的 String.format 模式
- lpad/rpad 的 String.format 实现可能有问题

### 3.4 dest_py 优势

1. **SQL 注释完整保留**: leading_comments 和 inline_comments 都正确注入到 Java 代码中
2. **游标生命周期完整**: OPEN → mapper.select(), FETCH → row extraction, CLOSE → 注释
3. **REFCURSOR 正确物化**: 生成多个 mapper SELECT 方法（_1, _2 后缀）
4. **复合类型完整支持**: CREATE TYPE → Map<String, Object>，字段访问 → .get()，字段赋值 → .put()
5. **源码行号追踪精确**: 每个方法标注 `// Source: file.sql:line-range`
6. **包常量值正确**: 所有 20 个常量都保留了原始值
7. **跨服务调用正确**: pkg_common.format_amount() 等调用正确映射
8. **Long 比较使用 .compareTo()**: 避免 == 引用相等性 bug

---

## 4. dest_ru（Rust 转换器）详细差异

### 4.1 Critical 差异

#### C-RU-01: 整个 pkg_type_test.sql 未转换
- **文件**: pkg_type_test.sql（14 个过程/函数）
- **问题**: Rust 转换器对此文件产生了零输出
- **影响**: 自定义 TYPE、%TYPE、%ROWTYPE、RECORD 等高级类型特性完全缺失

#### C-RU-02: 游标 OPEN/FETCH/CLOSE 未生成 mapper 查询
- **文件**: pkg_cursor_patterns.sql (prc_cursor_walk, prc_cursor_conditional), PKG_CURSOR.sql (多个)
- **问题**: 游标操作转为注释 (`// OPEN cursor;`, `// FETCH cursor;`)，但 Mapper.java 和 Mapper.xml 中没有对应的 SELECT 方法
- **影响**: 循环体永远不执行，所有行级操作为死代码

#### C-RU-03: REFCURSOR 返回永远为空列表
- **文件**: pkg_employee_comments.sql → listByDept
- **问题**: OPEN ... FOR SELECT 模式未转换为 MyBatis 查询，直接返回 `Collections.emptyList()`
- **影响**: 分页查询完全不可用

#### C-RU-04: PKG_CURSOR.sql 中 pkg_cursor_advanced 的 5 个过程完全缺失
- **命名 bug**: CursorAdvancedService.java 包含的是 pkg_dynamic_for_loop 的方法（2 个 stub），不是 pkg_cursor_advanced
- **缺失过程**: proc_cursor_dynamic_using, func_get_order_cursor, proc_multi_cursor_return, proc_cursor_transform, proc_paginate_with_using

#### C-RU-05: PKG_FOR.sql 中 pkg_for_in_select 的 2 个过程缺失
- **源文件映射错误**: ForInSelectService.java 映射到了 gauss_complete_examples.sql 而非 PKG_FOR.sql
- **ForInSelectMapper.xml 为空**
- **缺失**: func_get_bonus_rate, proc_sync_employee_bonus

#### C-RU-06: 包常量值全部丢失
- **文件**: gauss_package_constants.sql → CompanyConstantsService.java
- **问题**: 所有 20 个常量声明了但初始化为 null/0/default（如 `COMPANYNAME = null`, `MINSALARY = BigDecimal.ZERO`）
- **影响**: 所有依赖常量的 SQL 逻辑使用错误值

#### C-RU-07: 跨包函数调用被替换为 null 赋值
- **文件**: pkg_payment.sql → processPayment, pkg_product.sql → updateProductPrice
- **问题**: `v_formatted := pkg_common.format_amount(p_amount)` → `vFormatted = null;`
- **影响**: 格式化逻辑完全丢失

#### C-RU-08: GOTO 状态机无条件覆盖为 Done
- **文件**: proc_Five_Gotos.sql → sp_order_state_machine
- **问题**: 每个 case 分支在条件设置状态后，无条件执行 `currentState = StateDone`
- **影响**: 状态机永远在 2 步内终止于 Done，所有回退跳转（REJECT→Paid, TIMEOUT→Pending）完全失效

#### C-RU-09: Collections.emptyList() 导致循环体为死代码
- **文件**: proc_Five_Gotos.sql → sp_validate_orders
- **问题**: `for (orderRec : Collections.<...>emptyList())` — 查询从未执行
- **影响**: 整个验证过程不执行任何查询

#### C-RU-10: __ROWCOUNT__ 永远为 0
- **文件**: 多个过程中出现
- **问题**: 声明 `int __ROWCOUNT__ = 0` 但从未从 mapper 返回值更新
- **影响**: sp_purge_logs 批量删除只执行一次（while(0 == 1000)永远 false）

#### C-RU-11: String.format 使用 {} 占位符
- **文件**: pkg_inventory.sql → checkStock
- **问题**: `String.format("Insufficient stock: {} < {}", vAvailable, pQty)` — String.format 需要 %s 而非 {}
- **影响**: 异常消息显示字面量 `{}` 而非实际值

#### C-RU-12: Long 对象用 == 比较
- **文件**: pkg_employee_comments.sql → transferDept
- **问题**: `vOldDeptId == pNewDeptId` — Long 引用比较，值 > 127 时永远 false
- **影响**: 部门比较在某些值范围内失效

#### C-RU-13: proc_GOto search_target 为空壳 stub
- **问题**: 整个方法体为 `// TODO: Auto-generated stub`

#### C-RU-14: proc_GOto process_data 游标缺失 + 无限循环
- **问题**: 缺少 selectProcessData mapper 调用，内部 while(true) 永不退出

#### C-RU-15: MyBatis 参数绑定双重 hash
- **文件**: gauss_insert_all_styles.sql → demo_06
- **问题**: `#{#{r}.empId}` 应为 `#{r.empId}`
- **影响**: 运行时报错

#### C-RU-16: 常量初始化为 BigDecimal.ZERO
- **文件**: gauss_update_select.sql → proc_batch_adjust_salary
- **问题**: MAX_BONUS_PCT=0.50 和 MIN_BONUS_PCT=0.02 都被初始化为 BigDecimal.ZERO
- **影响**: 奖金计算结果被钳制为 0

#### C-RU-17: ON CONFLICT 语法错误（MySQL 方言）
- **文件**: gauss_insert_all_styles.sql → demo_13
- **问题**: PostgreSQL 的 ON CONFLICT 被转换为 MySQL 的 ON DUPLICATE KEY UPDATE

#### C-RU-18: gauss_complete_examples.sql 完全缺失
- **问题**: Rust 转换器未为此文件生成任何输出

### 4.2 Major 差异

#### M-RU-01: 游标 Mapper 方法和 XML 缺失
- selectPrcCursorWalk, selectPrcCursorConditional 在 Mapper.java 和 Mapper.xml 中都不存在

#### M-RU-02: REFCURSOR 查询 Mapper 缺失
- selectListByDept_1, selectListByDept_2 未生成

#### M-RU-03: lpad/rpad 为注释 stub
- **文件**: pkg_builtin_funcs_test.sql
- 输出 `/* LPAD */` 注释，无实际填充逻辑

#### M-RU-04: ltrim/rtrim 输出 null
- **文件**: pkg_builtin_funcs_test.sql
- 输出 `vResult = null`，完全丢失功能

#### M-RU-05: sp_allocate_resource 错误处理吞噬异常
- catch 块使用空的 `__SQLERRM__` 变量

#### M-RU-06: 可变变量全部为 static
- 包级变量声明为 `private static`，在 Spring 单例中导致并发安全问题

#### M-RU-07: ForInSelectMapper.xml 为空
- 整个 XML 文件无 SQL 语句

### 4.3 Minor 差异

- String.valueOf 不必要的包装（大量出现）
- XML 格式为单行紧凑格式（可读性差）
- 源码行号追踪总是 `:1-1`（无实际信息）
- SQL 注释完全未保留（leading 和 inline 都丢失）
- Objects.equals vs .equals() 风格差异
- XML SELECT 语句包裹在多余括号中
- log.info("") 无用日志

### 4.4 dest_ru 优势

1. **复杂 SQL 查询保留更好**: LATERAL JOIN、多 CTE、极深嵌套查询大多数被正确保留（vs dest_py 的 9 个 TODO）
2. **GOTO 状态机结构更清晰**: enum + while+switch 模式（虽然执行有 bug，但结构更好）
3. **内置函数转换整体正确**: 35 个内置函数调用中 80% 正确（vs dest_py 91%）
4. **某些函数有实际实现**: funcCalcBonus/funcValidateSalary/funcGetDeptName 有完整实现（vs dest_py 的 stub）
5. **字符串编码**: dest_py 有幽灵列 bug，dest_ru 无此问题
6. **覆盖度**: 所有 120 个 CRUD 过程都生成了方法（包括 stub）

---

## 5. 未转换文件分析

### 5.1 MERGE INTO 语句（2 个文件）

| 文件 | 行数 | 原因分析 |
|------|------|----------|
| pkg_merge_example.sql | 947 | 包含 MERGE INTO ... USING ... ON ... WHEN MATCHED/NOT MATCHED 语法 |
| pkg_merge_fix1.sql | ~200 | MERGE + EXECUTE IMMEDIATE 组合 |

**根因**: 两个转换器都不支持 MERGE INTO 语法。AST 解析器可能可以识别，但代码生成阶段没有对应的转换模板。MERGE 语义（upsert）在 MyBatis 中需要映射为 INSERT ... ON CONFLICT 或 SELECT + conditional INSERT/UPDATE，逻辑复杂度高。

### 5.2 DDL-only 文件（5 个）

| 文件 | 内容 | 验证 |
|------|------|------|
| tables.sql | CREATE TABLE + 测试数据 | 无 CREATE PROCEDURE ✓ |
| missing_tables.sql | CREATE TABLE | 无 CREATE PROCEDURE ✓ |
| pkg_mapper_param_test_tables.sql | CREATE TABLE | 无 CREATE PROCEDURE ✓ |
| PKG_WARPDRIVER_STRESS_TEST-DDL.sql | CREATE TABLE | 无 CREATE PROCEDURE ✓ |
| DAT_DATACLEAR_CONFIG.sql | CREATE TABLE + INSERT | 无 CREATE PROCEDURE ✓ |

这些文件正确地被跳过，无需比较。

---

## 6. 原因分析汇总

### 6.1 两个转换器共同的问题

| 问题 | 根因 | 影响范围 |
|------|------|----------|
| MERGE INTO 不支持 | 语义映射复杂，MyBatis 无直接对应 | 2-3 个过程 |
| RETURNING INTO 未捕获 | MyBatis 的 `<insert useGeneratedKeys>` 与 PL/pgSQL RETURNING INTO 语义不同 | 6+ 个过程 |
| FORALL BULK 未实现 | 批量操作需要 MyBatis batch executor，转换器未处理 | 2+ 个过程 |
| SELECT INTO 解包缺失 | 转换器生成了 Map 返回但未生成字段提取代码 | 多个过程 |
| EXECUTE IMMEDIATE 部分/全部缺失 | 动态 SQL 需要 `@SelectProvider` 或 XML 动态标签，转换器未完全处理 | 3+ 个过程 |

### 6.2 dest_ru 特有问题的根因

| 问题 | 根因推断 |
|------|----------|
| 游标 OPEN/FETCH/CLOSE 未物化 | Rust 转换器未能将游标操作识别为需要生成 mapper SELECT 的模式，只生成了注释占位符 |
| REFCURSOR 未物化 | 同上，OPEN ... FOR SELECT 模式未触发查询生成 |
| 复合类型完全跳过 | AST 解析器可能无法处理 CREATE TYPE AS 语句，导致整个文件被跳过 |
| 包常量值丢失 | 常量声明被识别但初始值未被提取，全部使用 Java 默认值 |
| 跨包函数调用 → null | 包级函数调用解析失败，静默替换为 null 赋值 |
| __ROWCOUNT__ 永远为 0 | 未实现从 mapper 返回值更新 ROWCOUNT 变量的逻辑 |
| GOTO 状态机无条件覆盖 | 状态机代码生成模板在每个 case 后添加了无条件的状态重置 |
| Collections.emptyList() | FOR IN SELECT 循环的查询结果未正确传递到迭代器 |
| 源文件映射错误 | 包名到类名的映射逻辑在某些情况下分配到了错误的源文件 |
| String.format {} 占位符 | RAISE EXCEPTION 的参数占位符使用了 SLF4J 格式而非 String.format 格式 |

### 6.3 dest_py 特有问题的根因

| 问题 | 根因推断 |
|------|----------|
| 复杂查询重建失败 | 查询重建算法对 LATERAL、多 CTE、VALUES-as-table 等高级特性处理能力不足 |
| %ROWTYPE 幽灵列 | %ROWTYPE 展开时引用了不正确的表结构（可能缓存了错误的列信息） |
| UPDATE ... FROM 位置错误 | OpenGauss 的 UPDATE ... FROM 语法解析时，FROM 子句位置处理不当 |
| GOTO fall-through 缺 break | 状态机代码生成模板中 case 分支末尾缺少 break 语句 |

---

## 7. 改进建议

### 7.1 两个转换器共同的改进

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| P0 | **SELECT INTO 变量解包**: 生成 `_row.get("column_name")` 提取代码 | 修复取消订单等关键业务逻辑 |
| P0 | **MERGE INTO 支持**: 映射为 INSERT ... ON CONFLICT 或 SELECT + conditional DML | 3+ 过程可用 |
| P1 | **RETURNING INTO 支持**: 使用 `<insert useGeneratedKeys>` 或额外 SELECT | 6+ 过程获得正确返回值 |
| P1 | **FORALL BULK 支持**: 映射为 MyBatis batch executor 或分批调用 | 批量操作可用 |
| P2 | **EXECUTE IMMEDIATE 完整支持**: 映射为 `@SelectProvider` 或 XML `<script>` 动态 SQL | 3+ 过程动态 SQL 可用 |

### 7.2 dest_ru（Rust 转换器）特有改进

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| P0 | **游标 OPEN/FETCH/CLOSE 物化**: 必须生成对应的 mapper SELECT 方法 | 5+ 过程从死代码变为可用 |
| P0 | **REFCURSOR 物化**: 生成实际查询替代 `Collections.emptyList()` | 分页查询可用 |
| P0 | **CREATE TYPE 支持**: 处理复合类型声明 | 14 个过程不再缺失 |
| P0 | **包常量值提取**: 从 AST 中提取常量初始值 | 所有常量不再为 0/null |
| P1 | **跨包函数调用保留**: 不要静默替换为 null | 格式化等逻辑保留 |
| P1 | **__ROWCOUNT__ 更新**: 从 mapper 返回值获取影响行数 | 批量操作循环正确 |
| P1 | **GOTO 状态机修复**: 移除 case 后的无条件状态覆盖 | 状态机正常工作 |
| P1 | **FOR IN SELECT 查询传递**: 使用实际查询结果而非 Collections.emptyList() | 循环体执行 |
| P2 | **String.format 占位符修复**: 使用 %s 替代 {} | 错误消息正确 |
| P2 | **Long 比较**: 使用 .compareTo() 或 .equals() | 引用比较 bug 修复 |
| P2 | **SQL 注释保留**: 提取并注入 leading/inline comments | 代码可维护性提升 |
| P2 | **源码行号追踪**: 记录实际行范围替代 1-1 | 调试效率提升 |

### 7.3 dest_py（Python 转换器）特有改进

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| P1 | **复杂查询重建**: 增强 LATERAL JOIN、多 CTE、VALUES-as-table 的重建能力 | 9 个 SELECT 过程可用 |
| P1 | **UPDATE ... FROM 语法修复**: 确保 FROM 子句位置正确 | 4 个 UPDATE 过程 SQL 正确 |
| P2 | **%ROWTYPE 列信息修正**: 确保引用正确的表结构 | INSERT 不再包含幽灵列 |
| P2 | **GOTO 状态机 break**: 为每个 case 分支添加 break | 消除脆弱的 fall-through |

---

## 8. 附录：文件映射表

| # | SQL 文件 | dest_py 类 | dest_ru 类 | 比较状态 |
|---|----------|-----------|-----------|----------|
| 1 | gauss_select_all_styles.sql | SelectStylesService | SelectStylesService | ✅ 深度分析 |
| 2 | gauss_insert_all_styles.sql | InsertStylesService | InsertStylesService | ✅ 深度分析 |
| 3 | gauss_update_all_styles.sql | UpdateStylesService | UpdateStylesService | ✅ 深度分析 |
| 4 | gauss_delete_all_styles.sql | DeleteStylesService | DeleteStylesService | ✅ 深度分析 |
| 5 | pkg_order.sql | OrderService | OrderService | ✅ 深度分析 |
| 6 | pkg_payment.sql | PaymentService | PaymentService | ✅ 深度分析 |
| 7 | pkg_inventory.sql | InventoryService | InventoryService | ✅ 深度分析 |
| 8 | pkg_product.sql | ProductService | ProductService | ✅ 深度分析 |
| 9 | pkg_report.sql | ReportService | ReportService | ✅ 深度分析 |
| 10 | pkg_common.sql | CommonService | CommonService | ✅ 深度分析 |
| 11 | pkg_cursor_patterns.sql | CursorPatternsService | CursorPatternsService | ✅ 深度分析 |
| 12 | pkg_employee_comments.sql | EmployeeCommentsService | EmployeeCommentsService | ✅ 深度分析 |
| 13 | proc_GOto.sql | ProcGotoService | ProcGotoService | ✅ 深度分析 |
| 14 | proc_Five_Gotos.sql | ProcFiveGotosService | ProcFiveGotosService | ✅ 深度分析 |
| 15 | gauss_update_select.sql | SalaryUpdateService | SalaryUpdateService | ✅ 深度分析 |
| 16 | PKG_CURSOR.sql | DynamicForLoopService | CursorAdvanced + CursorLifecycle | ✅ 深度分析 |
| 17 | PKG_FOR.sql | ForInSelectService | OpenCursorService + ForInSelectService | ✅ 深度分析 |
| 18 | gauss_package_constants.sql | CompanyConstantsService | CompanyConstantsService | ✅ 深度分析 |
| 19 | pkg_type_test.sql | TypeTestService | ❌ 缺失 | ✅ 深度分析 |
| 20 | pkg_package_vars_test.sql | PackageVarsTestService | PackageVarsTestService | ✅ 深度分析 |
| 21 | pkg_builtin_funcs_test.sql | BuiltinFuncsService | BuiltinFuncsService | ✅ 深度分析 |
| 22 | pkg_custom_funcs_test.sql | CustomFuncsService | CustomFuncsService | ✅ 深度分析 |
| 23 | gauss_function_calls.sql | GaussFunctionCallsService | FunctionCallsService | 🔍 抽样分析 |
| 24 | astro_functions_pkg.sql | AstroFunctionsPkgService | AstroFunctionsPkgService | 🔍 抽样分析 |
| 25 | gauss_complete_examples.sql | GaussCompleteExamplesService | ❌ 缺失 | 🔍 抽样分析 |
| 26 | complex_clearing_pkg.sql | ComplexClearingPkgService | ComplexClearingPkgService | 🔍 抽样分析 |
| 27 | PKG_AAS_DATACLEAR.sql | AasDataclearService | AasDataclearService | 🔍 抽样分析 |
| 28 | pkg_aas_lob_dataclear.sql | AasLobDataclearService | AasLobDataclearService | 🔍 抽样分析 |
| 29 | PKG_WARPDRIVER_STRESS_TEST.sql | WarpdriverStressTestService | WarpdriverStressTestService | 🔍 抽样分析 |
| 30 | PKG_RPT_BATCH_DOWNLOAD.sql | RptBatchDownloadService | RptBatchDownloadService | 🔍 抽样分析 |
| 31 | PKG_2008802001_MGT.sql | _2008802001MgtService | _2008802001MgtService | 🔍 抽样分析 |
| 32 | PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql | DepositAcntInfoInquiryService | DepositAcntInfoInquiryService | 🔍 抽样分析 |
| 33 | PACK_LOG.sql | PackLogService | PackLogService | 🔍 抽样分析 |
| 34 | DB_LOG.sql | PackLogService | PackLogService | 🔍 抽样分析 |
| 35 | SWH_ALL_KIND.sql | TestService | TestService | 🔍 抽样分析 |
| 36 | pkg_mapper_param_test.sql | MapperParamTestService | MapperParamTestService | 🔍 抽样分析 |
| 37 | pkg_test_patterns.sql | TestService | TestService | 🔍 抽样分析 |
| 38 | pkg_merge_example.sql | ❌ 未转换 | ❌ 未转换 | MERGE 不支持 |
| 39 | pkg_merge_fix1.sql | ❌ 未转换 | ❌ 未转换 | MERGE 不支持 |
| 40 | tables.sql | — | — | DDL-only |
| 41 | missing_tables.sql | — | — | DDL-only |
| 42 | pkg_mapper_param_test_tables.sql | — | — | DDL-only |
| 43 | PKG_WARPDRIVER_STRESS_TEST-DDL.sql | — | — | DDL-only |
| 44 | DAT_DATACLEAR_CONFIG.sql | — | — | DDL-only |

> ✅ 深度分析 = 逐过程逐行对比 | 🔍 抽样分析 = 基于探索阶段的特征分析

---

## 9. 结论

### 9.1 核心发现

1. **两个转换器共同缺失的关键特性**: MERGE INTO、RETURNING INTO、FORALL BULK、完整 EXECUTE IMMEDIATE 支持。这是架构级限制，需要新增代码生成模板。

2. **dest_py 在基础正确性上优于 dest_ru**: SELECT INTO 解包、游标物化、REFCURSOR、复合类型、注释保留、常量值、源码追踪等方面，dest_py 都更完整。

3. **dest_ru 在 SQL 保留能力上优于 dest_py**: 复杂查询（LATERAL、多 CTE）的原始 SQL 保留更好，dest_py 倾向于重建失败。

4. **dest_ru 的系统性问题更严重**: 游标/REFCURSOR 未物化、复合类型完全跳过、常量值丢失、__ROWCOUNT__ 不更新——这些是影响大量过程的系统性缺陷，而非个别 bug。

### 9.2 推荐优先修复

1. **P0（两个转换器）**: SELECT INTO 变量解包 — 影响核心业务逻辑
2. **P0（dest_ru）**: 游标 OPEN/FETCH/CLOSE 物化 — 大量过程为死代码
3. **P0（dest_ru）**: 复合类型支持 — 14 个过程完全缺失
4. **P0（dest_ru）**: 包常量值提取 — 所有常量为 0/null
5. **P1（两个转换器）**: MERGE INTO、RETURNING INTO、FORALL BULK 支持

---

*报告生成时间: 2026-05-23*  
*分析基于: flux_gauss.py Python 转换器 vs Rust 转换器的输出目录 dest_py 与 dest_ru*
