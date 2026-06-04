# SQL ↔ Java 转换等价性比较报告生成计划

## TL;DR

> **目标**: 将 demo-project/sql/ 下全部 44 个 SQL 存储过程文件与 dest_py（Python 转换器）和 dest_ru（Rust 转换器）的 Java+MyBatis 输出逐一比较，识别不等价之处，产出一份结构化比较报告。
> 
> **交付物**:
> - `docs/sql-java-comparison-report.md` — 完整比较报告（含差异、原因分析、建议）
> - `.sisyphus/evidence/` — 每个任务的比较证据
> 
> **预估工作量**: Large
> **并行执行**: YES - 2 waves
> **关键路径**: Wave 1 比较任务(全部并行) → Wave 2 汇总报告

---

## Context

### Original Request
用户要求暂时放下转换过程，专注于比较转换后的代码与原始 SQL 是否等价。若不等价，差异在哪里。需要一份包含差异、原因分析和建议的比较报告，保存在 docs 目录下。

### Interview Summary
**关键讨论**:
- dest_py = Python 转换器（flux_gauss.py）输出
- dest_ru = Rust 转换器输出
- 全部 44 个 SQL 文件逐一比较（不抽样）
- 独立比较：SQL→dest_py 和 SQL→dest_ru，不互相对比两个转换器

**映射发现**:
- 44 个 SQL 文件中，5 个为纯 DDL/基础设施（无需比较）
- 2 个 MERGE 示例文件未生成 Java 输出（可能不支持）
- 37 个文件有对应的 Java 输出需比较
- dest_py 有 2 个独有文件（TypeTestService, GaussCompleteExamplesService）
- dest_ru 有 3 个额外类（从 PKG_CURSOR.sql 拆分出的 CursorAdvanced/Lifecycle, FunctionCallsService）
- 存在命名偏差：gauss_function_calls → GaussFunctionCalls(py) vs FunctionCalls(ru)

### Metis Review
**已识别并处理的 Gap**:
- 需处理 SQL 文件中无对应 Java 的情况 → 在报告中标注"未转换"
- 需处理同一 SQL 文件在两个输出目录中命名不同的情况 → 比较时跟踪实际类名
- 需处理 Java 文件中一个 Service 对应多个 SQL 文件的情况（如 PackLog ← DB_LOG.sql + PACK_LOG.sql）→ 比较时交叉验证

---

## Work Objectives

### Core Objective
逐过程（procedure）比较每个 SQL 存储过程与对应的 Java+MyBatis 实现，识别语义不等价之处。

### Concrete Deliverables
- `docs/sql-java-comparison-report.md` — 完整比较报告

### Definition of Done
- [x] 所有 37 个可比较的 SQL 文件均已逐一比较
- [x] 5 个 DDL 文件和 2 个 MERGE 文件已在报告中说明
- [x] 报告包含：差异清单、原因分析、改进建议
- [x] 报告按严重程度分级（Critical/Major/Minor）

### Must Have
- 每个差异必须注明：SQL 源文件、过程名、具体差异描述
- 参数列表对比（名称、类型、IN/OUT/INOUT 模式）
- 返回值对比（REFCURSOR → List<Map>、OUT 参数等）
- 控制流对比（IF/ELSIF → if/else if, FOR → for, WHILE → while, LOOP → while(true), GOTO → 标签模拟）
- DML 语句对比（SELECT/INSERT/UPDATE/DELETE → Mapper XML 中的 SQL）
- 异常处理对比（EXCEPTION block → try/catch）
- 游标操作对比（OPEN/FETCH/CLOSE → Mapper select）
- 动态 SQL 对比（EXECUTE → @SelectProvider 或动态 XML）
- 函数调用对比（内置函数 → Java 等价实现）

### Must NOT Have (Guardrails)
- 不修改任何源代码或生成代码
- 不评估转换器本身的代码质量（只关注转换结果的等价性）
- 不做性能对比
- 不涉及"哪个转换器更好"的价值判断
- 不引入新的代码文件

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: N/A（分析任务，非代码任务）
- **Automated tests**: None
- **Framework**: N/A

### QA Policy
每个比较任务必须：
1. 读取 SQL 源文件，提取所有 procedure/function
2. 读取对应的 Java Service.java，提取所有方法
3. 读取对应的 Mapper.xml，提取所有 SQL 映射
4. 逐过程比较并记录差异
5. 将比较结果作为证据保存到 `.sisyphus/evidence/`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - 12 parallel comparison tasks):
├── Task 1:  基础 CRUD 样式比较 (4 files) [deep]
├── Task 2:  订单/支付/库存/产品业务比较 (4 files) [deep]
├── Task 3:  报表/通用服务/游标模式比较 (4 files) [deep]
├── Task 4:  控制流(GOTO/LOOP)比较 (3 files) [deep]
├── Task 5:  游标/FOR循环比较 (3 files) [deep]
├── Task 6:  函数调用/自定义函数比较 (3 files) [deep]
├── Task 7:  内置函数/类型测试/包变量比较 (3 files) [deep]
├── Task 8:  复杂业务清算比较 (3 files) [deep]
├── Task 9:  大型压力测试/批量下载比较 (3 files) [deep]
├── Task 10: 金融查询/日志/Astro函数比较 (4 files) [deep]
├── Task 11: Mapper参数测试/注释/合并缺失分析 (3 files) [deep]
├── Task 12: 未转换文件 & DDL清单 + 全量映射验证 (7 files) [quick]

Wave 2 (After Wave 1 - aggregation):
├── Task 13: 汇总所有比较结果，生成最终报告 [writing]

Wave FINAL:
├── Task F1: 报告完整性审计 [oracle]
├── Task F2: 覆盖度验证（所有37个文件均已覆盖） [unspecified-high]
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1-12 | None | 13 | 1 |
| 13 | 1-12 | F1, F2 | 2 |
| F1 | 13 | User OK | Final |
| F2 | 13 | User OK | Final |

### Agent Dispatch Summary

- **Wave 1**: 12 tasks — all `deep` category except Task 12 which is `quick`
- **Wave 2**: 1 task — `writing` category
- **Final**: 2 tasks — F1 `oracle`, F2 `unspecified-high`

---

## TODOs

- [x] 1. 基础 CRUD 样式比较（4 个文件）

  **What to do**:
  逐过程比较以下 SQL 文件与 dest_py 和 dest_ru 中对应的 Java 输出：
  - `gauss_select_all_styles.sql` → `SelectStylesService.java` + `SelectStylesMapper.xml`
  - `gauss_insert_all_styles.sql` → `InsertStylesService.java` + `InsertStylesMapper.xml`
  - `gauss_update_all_styles.sql` → `UpdateStylesService.java` + `UpdateStylesMapper.xml`
  - `gauss_delete_all_styles.sql` → `DeleteStylesService.java` + `DeleteStylesMapper.xml`

  比较维度：
  1. 每个 SQL procedure 是否都有对应的 Java 方法
  2. 参数列表：名称、SQL 类型 vs Java 类型、IN/OUT/INOUT 模式是否正确
  3. Mapper XML 中的 SQL 语句是否与原始 DML 等价（表名、字段、WHERE 条件）
  4. SELECT INTO → mapper.selectOne / INSERT → mapper.insert 等映射是否正确
  5. RETURN NEXT / RETURN QUERY 是否正确转换为集合返回

  **Must NOT do**:
  - 不修改任何文件
  - 不做代码风格评价，只关注语义等价性

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要逐一对比 SQL 语句与 Java 代码，理解转换语义
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2-12)
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/gauss_select_all_styles.sql` — 原始 SELECT 存储过程（各种 SELECT 风格）
  - `demo-project/sql/gauss_insert_all_styles.sql` — 原始 INSERT 存储过程
  - `demo-project/sql/gauss_update_all_styles.sql` — 原始 UPDATE 存储过程
  - `demo-project/sql/gauss_delete_all_styles.sql` — 原始 DELETE 存储过程

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/SelectStylesService.java` — Python 转换器输出的 Service
  - `dest_py/src/main/java/ced/mapper/SelectStylesMapper.java` — Python 转换器输出的 Mapper 接口
  - `dest_py/src/main/resources/mapper/SelectStylesMapper.xml` — Python 转换器输出的 XML 映射
  - 同理 dest_ru 目录下对应文件
  - 所有 4 个样式文件均需检查 Service + Mapper.java + Mapper.xml 三件套

  **External References**: None

  **Acceptance Criteria**:
  - [ ] 每个 SQL 文件中的所有 procedure 都已与 Java 方法逐一比对
  - [ ] 每个差异已记录：SQL 文件名、过程名、差异类型、具体描述
  - [ ] 比较结果保存为结构化 Markdown

  **QA Scenarios**:
  ```
  Scenario: CRUD 过程完整覆盖验证
    Tool: Bash (grep + read)
    Preconditions: SQL 文件和 Java 文件均存在
    Steps:
      1. 从 SQL 文件中提取所有 CREATE PROCEDURE/FUNCTION 名称
      2. 从 Java Service 文件中提取所有 public 方法名
      3. 对比两份列表，确认 1:1 对应关系
      4. 对缺失的方法记录差异
    Expected Result: 每个 SQL procedure 都有对应的 Java 方法（或有明确的差异记录）
    Evidence: .sisyphus/evidence/task-1-crud-coverage.md

  Scenario: Mapper XML SQL 等价性验证
    Tool: Bash (read + compare)
    Preconditions: Mapper.xml 文件存在
    Steps:
      1. 从 Mapper.xml 中提取所有 <select>/<insert>/<update>/<delete> 标签的 SQL
      2. 从原始 SQL 中提取对应的 DML 语句
      3. 比较：表名、字段列表、WHERE 条件、参数占位符
    Expected Result: Mapper XML 中的 SQL 与原始 DML 在语义上一致
    Evidence: .sisyphus/evidence/task-1-crud-sql-equivalence.md
  ```

  **Commit**: NO

- [x] 2. 订单/支付/库存/产品业务比较（4 个文件）

  **What to do**:
  逐过程比较以下核心业务 SQL 文件与两个转换输出：
  - `pkg_order.sql` → `OrderService.java` + `OrderMapper.xml`
  - `pkg_payment.sql` → `PaymentService.java` + `PaymentMapper.xml`
  - `pkg_inventory.sql` → `InventoryService.java` + `InventoryMapper.xml`
  - `pkg_product.sql` → `ProductService.java` + `ProductMapper.xml`

  比较维度：
  1. 业务逻辑完整性：IF/ELSE 分支、FOR/WHILE 循环、嵌套调用是否完整转换
  2. 跨服务调用：SQL 中调用其他 package procedure 的地方是否转换为 Java Service 间调用
  3. 事务处理：SAVEPOINT/COMMIT/ROLLBACK 相关逻辑
  4. 异常处理：EXCEPTION WHEN OTHERS → try/catch 转换是否完整
  5. OUT/INOUT 参数是否正确通过 Map 或 DTO 传递

  **Must NOT do**:
  - 不修改任何文件
  - 不做性能评价

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 核心业务逻辑复杂，需要深入理解 SQL→Java 语义映射
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3-12)
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/pkg_order.sql` — 订单管理存储过程
  - `demo-project/sql/pkg_payment.sql` — 支付处理存储过程
  - `demo-project/sql/pkg_inventory.sql` — 库存管理存储过程
  - `demo-project/sql/pkg_product.sql` — 产品管理存储过程

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/OrderService.java` — Python 版订单服务
  - `dest_ru/src/main/java/ced/service/OrderService.java` — Rust 版订单服务
  - `dest_py/src/main/resources/mapper/OrderMapper.xml` — Python 版 Mapper XML
  - `dest_ru/src/main/resources/mapper/OrderMapper.xml` — Rust 版 Mapper XML
  - 同理 Payment/Inventory/Product 三套

  **External References**: None

  **Acceptance Criteria**:
  - [ ] 4 个 SQL 文件中所有 procedure 逐一比对完毕
  - [ ] 跨服务调用链路已验证
  - [ ] 事务处理逻辑已对比
  - [ ] 异常处理完整性已对比

  **QA Scenarios**:
  ```
  Scenario: 业务逻辑完整性验证
    Tool: Bash (read + grep)
    Preconditions: SQL 和 Java 文件均存在
    Steps:
      1. 从 SQL 文件中提取所有 IF/ELSIF/ELSE 块及其条件
      2. 从 Java 文件中提取所有 if/else if/else 块及其条件
      3. 逐一对比条件逻辑是否等价
      4. 检查 FOR/WHILE/LOOP 循环是否完整转换
    Expected Result: 所有控制流分支在 Java 中都有对应实现
    Evidence: .sisyphus/evidence/task-2-business-logic.md

  Scenario: 跨服务调用链验证
    Tool: Bash (grep)
    Steps:
      1. 在 SQL 中搜索 pkg_xxx.procedure_name() 形式的调用
      2. 在 Java Service 中搜索 xxxService.methodName() 形式的调用
      3. 对比两份列表确认 1:1 对应
    Expected Result: SQL 中的跨包调用在 Java 中都有对应的 Service 间调用
    Evidence: .sisyphus/evidence/task-2-cross-service-calls.md
  ```

  **Commit**: NO

- [x] 3. 报表/通用/游标模式比较（4 个文件）

  **What to do**:
  逐过程比较以下文件：
  - `pkg_report.sql` → `ReportService.java` + `ReportMapper.xml`
  - `pkg_common.sql` → `CommonService.java` + `CommonMapper.xml`
  - `pkg_cursor_patterns.sql` → `CursorPatternsService.java` + `CursorPatternsMapper.xml`
  - `pkg_employee_comments.sql` → `EmployeeCommentsService.java` + `EmployeeCommentsMapper.xml`

  比较维度：
  1. 游标操作：OPEN/FETCH/CLOSE → mapper.select() 的转换是否完整
  2. FOR cursor IN SELECT 循环 → for(Map row : mapper.select()) 的转换
  3. 游标变量（REFCURSOR）返回 → List<Map<String,Object>> 或 List<DTO> 的映射
  4. SQL 注释/注释保留情况（leading_comments, inline_comments）
  5. 公共工具方法的等价性

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 游标模式转换复杂，需仔细比对 OPEN/FETCH/CLOSE 生命周期
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1-2, 4-12)
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/pkg_cursor_patterns.sql` — 各种游标使用模式
  - `demo-project/sql/pkg_report.sql` — 报表生成存储过程
  - `demo-project/sql/pkg_common.sql` — 公共工具方法
  - `demo-project/sql/pkg_employee_comments.sql` — 带注释的员工管理

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/CursorPatternsService.java`
  - `dest_ru/src/main/java/ced/service/CursorPatternsService.java`
  - 对应 Mapper.xml 和 Mapper.java
  - 其他 3 个文件同理

  **Acceptance Criteria**:
  - [ ] 游标 OPEN/FETCH/CLOSE 操作逐一比对
  - [ ] FOR cursor IN SELECT 循环转换验证
  - [ ] REFCURSOR 返回值映射验证
  - [ ] 公共方法参数和返回值验证

  **QA Scenarios**:
  ```
  Scenario: 游标生命周期验证
    Tool: Bash (grep + read)
    Steps:
      1. 在 SQL 中统计 OPEN/FETCH/CLOSE cursor 的次数和目标
      2. 在 Java 中统计对应的 mapper.select() 调用
      3. 验证每次 OPEN 都有对应的查询，每次 FETCH 都有对应的结果处理
    Expected Result: 游标操作在 Java 中完整映射
    Evidence: .sisyphus/evidence/task-3-cursor-lifecycle.md
  ```

  **Commit**: NO

- [x] 4. 控制流（GOTO/LOOP/条件）比较（3 个文件）

  **What to do**:
  逐过程比较以下控制流密集的文件：
  - `proc_GOto.sql` → `ProcGotoService.java` + `ProcGotoMapper.xml`
  - `proc_Five_Gotos.sql` → `ProcFiveGotosService.java` + `ProcFiveGotosMapper.xml`
  - `gauss_update_select.sql` → `SalaryUpdateService.java` + `SalaryUpdateMapper.xml`

  比较维度：
  1. GOTO 语句转换：SQL GOTO label → Java 中的标签/标志变量/while-break 模拟是否正确
  2. 复杂嵌套 GOTO（Five_Gotos 有 5 个 GOTO 跳转）的 Java 实现是否保留了所有跳转路径
  3. UPDATE...RETURNING → mapper.update + 返回值的转换
  4. SELECT...FOR UPDATE 的锁语义是否保留
  5. 条件表达式（AND/OR/NOT、NULL 判断、EXISTS）的 Java 等价性

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: GOTO 转换是最复杂的控制流映射，需要仔细验证每条跳转路径
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/proc_GOto.sql` — 包含 GOTO 的存储过程
  - `demo-project/sql/proc_Five_Gotos.sql` — 包含 5 个 GOTO 跳转的复杂存储过程
  - `demo-project/sql/gauss_update_select.sql` — UPDATE + SELECT 组合样式

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/ProcGotoService.java`
  - `dest_ru/src/main/java/ced/service/ProcGotoService.java`
  - `dest_py/src/main/java/ced/service/ProcFiveGotosService.java`
  - `dest_ru/src/main/java/ced/service/ProcFiveGotosService.java`
  - `dest_py/src/main/java/ced/service/SalaryUpdateService.java`
  - `dest_ru/src/main/java/ced/service/SalaryUpdateService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] 每个 GOTO 跳转路径在 Java 中都有对应实现
  - [ ] UPDATE...RETURNING 语义正确转换
  - [ ] 条件表达式等价性验证完毕

  **QA Scenarios**:
  ```
  Scenario: GOTO 跳转路径完整性
    Tool: Bash (grep + read)
    Steps:
      1. 在 SQL 中提取所有 GOTO target 和 <<label>> 定义
      2. 在 Java 中找到对应的跳转实现（标签变量 + break/continue/while 模式）
      3. 画出 SQL 的跳转图和 Java 的跳转图，对比等价性
    Expected Result: 所有 GOTO 跳转路径在 Java 中都被正确模拟
    Evidence: .sisyphus/evidence/task-4-goto-paths.md
  ```

  **Commit**: NO

- [x] 5. 游标/FOR 循环/包常量比较（3 个文件）

  **What to do**:
  逐过程比较以下文件，注意 dest_ru 中 PKG_CURSOR.sql 可能拆分为多个类：
  - `PKG_CURSOR.sql` → dest_py: `CursorPatternsService.java` / dest_ru: `CursorPatternsService.java` + `CursorAdvancedService.java` + `CursorLifecycleService.java`
  - `PKG_FOR.sql` → `ForInSelectService.java` + `ForInSelectMapper.xml`
  - `gauss_package_constants.sql` → `CompanyConstantsService.java` + `CompanyConstantsMapper.xml`

  比较维度：
  1. PKG_CURSOR 拆分：dest_ru 是否完整包含了所有过程？拆分后的类之间是否有遗漏？
  2. FOR IN SELECT 循环 → for(Map row : mapper.select()) 转换
  3. FOR i IN 1..N 循环 → for(int i=1; i<=N; i++) 转换
  4. 包常量（constant 声明）是否转换为 Java 常量或配置
  5. 游标参数化（OPEN cursor FOR SELECT ... USING ...）的转换

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: PKG_CURSOR 拆分情况复杂，需要交叉验证多个文件
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/PKG_CURSOR.sql` — 游标操作大全（OPEN/FETCH/CLOSE/参数化游标）
  - `demo-project/sql/PKG_FOR.sql` — FOR 循环各种模式
  - `demo-project/sql/gauss_package_constants.sql` — 包常量定义

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/CursorPatternsService.java`
  - `dest_ru/src/main/java/ced/service/CursorPatternsService.java`
  - `dest_ru/src/main/java/ced/service/CursorAdvancedService.java` — dest_ru 独有
  - `dest_ru/src/main/java/ced/service/CursorLifecycleService.java` — dest_ru 独有
  - `dest_py/src/main/java/ced/service/ForInSelectService.java`
  - `dest_ru/src/main/java/ced/service/ForInSelectService.java`
  - `dest_py/src/main/java/ced/service/CompanyConstantsService.java`
  - `dest_ru/src/main/java/ced/service/CompanyConstantsService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] PKG_CURSOR 所有过程在两个版本中均有对应（含 dest_ru 的拆分验证）
  - [ ] FOR 循环所有模式逐一比对
  - [ ] 包常量转换验证完毕

  **QA Scenarios**:
  ```
  Scenario: PKG_CURSOR 拆分完整性验证
    Tool: Bash (grep + read)
    Steps:
      1. 从 PKG_CURSOR.sql 提取所有 procedure 名称
      2. 在 dest_py 的 CursorPatternsService.java 中查找所有方法
      3. 在 dest_ru 的 CursorPatterns + CursorAdvanced + CursorLifecycle 三个 Service 中查找所有方法
      4. 对比三个列表，确认无遗漏
    Expected Result: dest_ru 拆分后的 3 个类联合覆盖所有 SQL procedures
    Evidence: .sisyphus/evidence/task-5-cursor-split.md
  ```

  **Commit**: NO

- [x] 6. 函数调用/天文函数/完整示例比较（3 个文件）

  **What to do**:
  逐过程比较以下文件，注意命名差异和缺失情况：
  - `gauss_function_calls.sql` → dest_py: `GaussFunctionCallsService.java` / dest_ru: `FunctionCallsService.java`（⚠️ 命名不同）
  - `astro_functions_pkg.sql` → `AstroFunctionsPkgService.java` + `AstroFunctionsPkgMapper.xml`
  - `gauss_complete_examples.sql` → dest_py: `GaussCompleteExamplesService.java` / dest_ru: ❌ 缺失

  比较维度：
  1. 命名差异：gauss_function_calls 在两个转换器中类名不同，内容是否等价？
  2. SQL 内置函数调用（COALESCE, NVL, CAST, TO_CHAR 等）→ Java 等价实现的正确性
  3. astro_functions_pkg 中的数学/天文函数转换准确性
  4. gauss_complete_examples 在 dest_ru 中缺失——报告中标注为"未转换"
  5. 返回值类型转换（NUMBER → BigDecimal, VARCHAR → String, DATE → LocalDate）

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 函数调用转换准确性需要逐一验证 SQL 函数 vs Java 等价实现
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/gauss_function_calls.sql` — 各种 SQL 函数调用
  - `demo-project/sql/astro_functions_pkg.sql` — 天文/数学函数包
  - `demo-project/sql/gauss_complete_examples.sql` — 完整示例

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/GaussFunctionCallsService.java`
  - `dest_ru/src/main/java/ced/service/FunctionCallsService.java` — 注意类名不同
  - `dest_py/src/main/java/ced/service/AstroFunctionsPkgService.java`
  - `dest_ru/src/main/java/ced/service/AstroFunctionsPkgService.java`
  - `dest_py/src/main/java/ced/service/GaussCompleteExamplesService.java` — dest_ru 中无对应

  **Acceptance Criteria**:
  - [ ] 函数调用转换逐一验证
  - [ ] gauss_complete_examples 在 dest_ru 缺失已记录
  - [ ] 命名差异已记录

  **QA Scenarios**:
  ```
  Scenario: 函数调用转换准确性验证
    Tool: Bash (grep + read)
    Steps:
      1. 从 SQL 中提取所有内置函数调用（COALESCE, NVL, TO_CHAR, SUBSTR 等）
      2. 从 Java 中查找对应的转换实现
      3. 逐一验证语义等价性
    Expected Result: 每个SQL函数都有正确的Java等价实现
    Evidence: .sisyphus/evidence/task-6-function-calls.md
  ```

  **Commit**: NO

- [x] 7. 类型测试/包变量/内置函数/自定义函数比较（4 个文件）

  **What to do**:
  逐过程比较以下文件，注意 dest_ru 中可能缺失某些输出：
  - `pkg_type_test.sql` → dest_py: `TypeTestService.java` / dest_ru: ❌ 缺失
  - `pkg_package_vars_test.sql` → `PackageVarsTestService.java` + `PackageVarsTestMapper.xml`（两个版本都有）
  - `pkg_builtin_funcs_test.sql` → `BuiltinFuncsService.java` + `BuiltinFuncsMapper.xml`（1 个 procedure，60+ 内置函数调用）
  - `pkg_custom_funcs_test.sql` → `CustomFuncsService.java` + `CustomFuncsMapper.xml`

  比较维度：
  1. 自定义 TYPE（RECORD/TABLE）→ Java 类/DTO 的转换是否完整
  2. %TYPE / %ROWTYPE 引用是否正确处理
  3. 包级变量（package variables）→ Java 成员变量或方法内局部变量的映射
  4. 60+ 内置函数逐一验证：substr→substring, upper→toUpperCase, nvl→Optional, coalesce→Optional, trim→trim, 数学函数等
  5. 自定义函数调用 → Java 方法调用的映射
  6. pkg_type_test 在 dest_ru 中缺失——标注为"未转换"

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 内置函数映射需要逐一验证 60+ 函数，工作量大且需要精确
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/pkg_type_test.sql` — 自定义 TYPE、%TYPE、%ROWTYPE 测试
  - `demo-project/sql/pkg_package_vars_test.sql` — 包级变量测试（3 procedures）
  - `demo-project/sql/pkg_builtin_funcs_test.sql` — 60+ 内置函数调用（1 procedure）
  - `demo-project/sql/pkg_custom_funcs_test.sql` — 自定义函数调用（2 functions + 1 procedure）

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/TypeTestService.java` — dest_ru 中无对应
  - `dest_py/src/main/java/ced/service/PackageVarsTestService.java`
  - `dest_ru/src/main/java/ced/service/PackageVarsTestService.java`
  - `dest_py/src/main/java/ced/service/BuiltinFuncsService.java`
  - `dest_ru/src/main/java/ced/service/BuiltinFuncsService.java`
  - `dest_py/src/main/java/ced/service/CustomFuncsService.java`
  - `dest_ru/src/main/java/ced/service/CustomFuncsService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] 自定义 TYPE 转换验证完毕
  - [ ] 包变量映射验证完毕
  - [ ] 60+ 内置函数逐一比对，记录转换缺失/错误
  - [ ] pkg_type_test 在 dest_ru 缺失已记录

  **QA Scenarios**:
  ```
  Scenario: 内置函数映射覆盖率验证
    Tool: Bash (grep + read)
    Steps:
      1. 从 pkg_builtin_funcs_test.sql 中提取所有内置函数调用（substr, upper, lower, trim, nvl, coalesce, to_char, to_number, abs, ceil, floor, round, mod, greatest, least, length, replace, concat, instr, lpad, rpad, nullif 等）
      2. 从 BuiltinFuncsService.java 中找到对应的 Java 实现
      3. 逐一验证语义等价性，标记缺失或错误转换
    Expected Result: 每个SQL内置函数都有对应的Java实现或等效转换
    Evidence: .sisyphus/evidence/task-7-builtin-funcs.md
  ```

  **Commit**: NO

- [x] 8. 复杂业务清算比较（3 个文件）

  **What to do**:
  逐过程比较以下复杂清算业务文件：
  - `complex_clearing_pkg.sql` → `ComplexClearingPkgService.java` + `ComplexClearingPkgMapper.xml`
  - `PKG_AAS_DATACLEAR.sql` → `AasDataclearService.java` + `AasDataclearMapper.xml`
  - `pkg_aas_lob_dataclear.sql` → `AasLobDataclearService.java` + `AasLobDataclearMapper.xml`

  比较维度：
  1. 复杂业务逻辑完整性：多层 IF/ELSIF 嵌套、FOR 循环中的 UPDATE/DELETE
  2. 异常处理：EXCEPTION WHEN OTHERS THEN → try/catch 是否完整保留
  3. DBE_SCHEDULER 调度器（pkg_aas_lob_dataclear）→ Java 中如何处理
  4. LOB 数据操作（CLOB/BLOB 处理）→ Java 中的对应实现
  5. 大量 DML 操作的批量处理逻辑

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 复杂清算业务逻辑密集，嵌套深，需要仔细验证每层控制流
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/complex_clearing_pkg.sql` — 复杂清算包，高级特性
  - `demo-project/sql/PKG_AAS_DATACLEAR.sql` — 数据清理包（6 procedures）
  - `demo-project/sql/pkg_aas_lob_dataclear.sql` — LOB 数据清理（3 procedures, DBE_SCHEDULER）

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/ComplexClearingPkgService.java`
  - `dest_ru/src/main/java/ced/service/ComplexClearingPkgService.java`
  - `dest_py/src/main/java/ced/service/AasDataclearService.java`
  - `dest_ru/src/main/java/ced/service/AasDataclearService.java`
  - `dest_py/src/main/java/ced/service/AasLobDataclearService.java`
  - `dest_ru/src/main/java/ced/service/AasLobDataclearService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] 所有 procedures 的控制流逐一比对
  - [ ] 异常处理链完整性验证
  - [ ] DBE_SCHEDULER 转换处理记录
  - [ ] LOB 操作转换验证

  **QA Scenarios**:
  ```
  Scenario: 异常处理完整性验证
    Tool: Bash (grep + read)
    Steps:
      1. 在 SQL 中搜索所有 EXCEPTION 块及其 WHEN 条件
      2. 在 Java 中搜索所有 try/catch 块及其异常类型
      3. 对比：每个 EXCEPTION WHEN 是否都有对应 catch
    Expected Result: SQL 中的所有异常处理路径在 Java 中都有对应
    Evidence: .sisyphus/evidence/task-8-exception-handling.md
  ```

  **Commit**: NO

- [x] 9. 大型压力测试/批量下载/管理模块比较（3 个文件）

  **What to do**:
  逐过程比较以下大型复杂文件：
  - `PKG_WARPDRIVER_STRESS_TEST.sql` → `WarpdriverStressTestService.java` + `WarpdriverStressTestMapper.xml`
  - `PKG_RPT_BATCH_DOWNLOAD.sql` → `RptBatchDownloadService.java` + `RptBatchDownloadMapper.xml`
  - `PKG_2008802001_MGT.sql` → `_2008802001MgtService.java` + `_2008802001MgtMapper.xml`

  比较维度：
  1. 大规模 GOTO 模式（WARPDRIVER ~15 procedures，大量 GOTO 跳转）→ Java 模拟的正确性
  2. CLOB 处理（RPT_BATCH_DOWNLOAD 的 CLOB 拼接和下载逻辑）
  3. 动态 SQL 构建（长字符串拼接 → Java StringBuilder）
  4. REF CURSOR 返回 → List<Map<String,Object>> 的映射
  5. 超长方法体（可能超过 500 行）的完整性
  6. 类名 `_2008802001Mgt` 以数字开头（Java 不允许）的处理方式

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 这些是最大的文件，GOTO 密集，逻辑最复杂
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/PKG_WARPDRIVER_STRESS_TEST.sql` — ~15 procedures, 大量 GOTO
  - `demo-project/sql/PKG_RPT_BATCH_DOWNLOAD.sql` — 1 个复杂 procedure, CLOB 处理
  - `demo-project/sql/PKG_2008802001_MGT.sql` — 多 procedures, REF CURSOR

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/WarpdriverStressTestService.java`
  - `dest_ru/src/main/java/ced/service/WarpdriverStressTestService.java`
  - `dest_py/src/main/java/ced/service/RptBatchDownloadService.java`
  - `dest_ru/src/main/java/ced/service/RptBatchDownloadService.java`
  - `dest_py/src/main/java/ced/service/_2008802001MgtService.java`
  - `dest_ru/src/main/java/ced/service/_2008802001MgtService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] ~15 个 GOTO procedures 的 Java 模拟逐一验证
  - [ ] CLOB 处理转换验证
  - [ ] REF CURSOR 返回值映射验证
  - [ ] 类名合法性处理验证

  **QA Scenarios**:
  ```
  Scenario: WARPDRIVER 全量 GOTO 路径验证
    Tool: Bash (grep + read)
    Steps:
      1. 从 SQL 提取所有 procedure 名称（~15 个）
      2. 从 Java Service 提取所有 public 方法名
      3. 逐一比较：每个 SQL procedure 的 GOTO 跳转图 vs Java 实现的控制流图
      4. 标记跳转路径缺失或逻辑不等价的情况
    Expected Result: 所有 ~15 个 procedure 的 GOTO 路径在 Java 中被正确模拟
    Evidence: .sisyphus/evidence/task-9-stress-test-goto.md
  ```

  **Commit**: NO

- [x] 10. 金融查询/日志包/全类型比较（5 个文件）

  **What to do**:
  逐过程比较以下文件，注意一些特殊映射关系：
  - `PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql` → `DepositAcntInfoInquiryService.java` + `DepositAcntInfoInquiryMapper.xml`
  - `PACK_LOG.sql` → `PackLogService.java` + `PackLogMapper.xml`
  - `DB_LOG.sql` → `PackLogService.java`（⚠️ 可能与 PACK_LOG 生成同一个类）
  - `SWH_ALL_KIND.sql` → `TestService.java` + `TestMapper.xml`（⚠️ 可能与 pkg_test_patterns 生成同一个类）
  - `gauss_update_select.sql` → `SalaryUpdateService.java`（如果 Task 4 未覆盖此项，此处补充）

  比较维度：
  1. PKG_DEPOSIT 深度子查询、EXCEPTION 处理、CLOB、分页逻辑
  2. PACK_LOG 的 PRAGMA AUTONOMOUS_TRANSACTION → Java 中的事务传播处理
  3. DB_LOG 与 PACK_LOG 是否合并到同一个 PackLogService？如果是，是否导致方法冲突？
  4. SWH_ALL_KIND 与 pkg_test_patterns 是否合并到同一个 TestService？
  5. 每个文件的所有 procedures 是否都包含在对应的 Java 类中

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要交叉验证多文件合并到同一 Java 类的情况
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql` — 2 个复杂 procedures，深度子查询
  - `demo-project/sql/PACK_LOG.sql` — 5 procedures, PRAGMA AUTONOMOUS_TRANSACTION
  - `demo-project/sql/DB_LOG.sql` — 可能与 PACK_LOG 合并
  - `demo-project/sql/SWH_ALL_KIND.sql` — 可能与 pkg_test_patterns 合并

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/DepositAcntInfoInquiryService.java`
  - `dest_ru/src/main/java/ced/service/DepositAcntInfoInquiryService.java`
  - `dest_py/src/main/java/ced/service/PackLogService.java`
  - `dest_ru/src/main/java/ced/service/PackLogService.java`
  - `dest_py/src/main/java/ced/service/TestService.java`
  - `dest_ru/src/main/java/ced/service/TestService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] PKG_DEPOSIT 的复杂查询逻辑验证
  - [ ] PACK_LOG/DB_LOG 合并情况已说明
  - [ ] SWH_ALL_KIND/pkg_test_patterns 合并情况已说明
  - [ ] PRAGMA AUTONOMOUS_TRANSACTION 转换已记录

  **QA Scenarios**:
  ```
  Scenario: 多文件合并验证
    Tool: Bash (grep + read)
    Steps:
      1. 分别提取 DB_LOG.sql 和 PACK_LOG.sql 中的所有 procedure 名称
      2. 在 PackLogService.java 中搜索所有 public 方法
      3. 验证两个 SQL 文件的 procedures 是否都在 PackLogService 中有对应方法
      4. 同理验证 SWH_ALL_KIND + pkg_test_patterns → TestService
    Expected Result: 所有 SQL procedures 都在 Java 中有对应，无冲突
    Evidence: .sisyphus/evidence/task-10-multi-file-merge.md
  ```

  **Commit**: NO

- [x] 11. Mapper 参数测试/测试模式/合并文件分析（4 个文件）

  **What to do**:
  逐过程比较以下文件，包含 2 个未转换的 MERGE 文件：
  - `pkg_mapper_param_test.sql` → `MapperParamTestService.java` + `MapperParamTestMapper.xml`
  - `pkg_test_patterns.sql` → `TestService.java`（可能合并到同一个 TestService）
  - `pkg_merge_example.sql` → ❌ 两个版本都未生成 Java 输出（MERGE 语句不支持？）
  - `pkg_merge_fix1.sql` → ❌ 两个版本都未生成 Java 输出

  比较维度：
  1. pkg_mapper_param_test 的 8 个 procedures 的参数传递机制验证
  2. pkg_test_patterns 的 FOR/WHILE/嵌套 IF → Java 控制流验证
  3. MERGE INTO 语句为什么没有生成 Java？是解析失败还是不支持？
  4. 读取 pkg_merge_example.sql 和 pkg_merge_fix1.sql，分析其中使用的 PL/pgSQL 特性

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要分析未转换原因，需要深入理解 MERGE 语法
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/pkg_mapper_param_test.sql` — 8 procedures，参数验证
  - `demo-project/sql/pkg_test_patterns.sql` — 5 procedures，FOR/WHILE/嵌套IF
  - `demo-project/sql/pkg_merge_example.sql` — MERGE INTO 示例（947 行）
  - `demo-project/sql/pkg_merge_fix1.sql` — MERGE 修复版

  **API/Type References**:
  - `dest_py/src/main/java/ced/service/MapperParamTestService.java`
  - `dest_ru/src/main/java/ced/service/MapperParamTestService.java`
  - 对应 Mapper.xml 文件

  **Acceptance Criteria**:
  - [ ] 8 个 mapper 参数 procedures 验证完毕
  - [ ] MERGE 未转换原因已分析
  - [ ] MERGE SQL 中使用的高级特性已列出

  **QA Scenarios**:
  ```
  Scenario: MERGE 未转换原因分析
    Tool: Bash (read)
    Steps:
      1. 读取 pkg_merge_example.sql，提取 MERGE INTO 语法和周围的 PL/pgSQL 特性
      2. 检查 .fluxgauss/ 目录中是否有转换错误日志
      3. 判断是解析失败（AST 阶段）还是生成阶段跳过
    Expected Result: 明确 MERGE 未转换的根本原因
    Evidence: .sisyphus/evidence/task-11-merge-analysis.md
  ```

  **Commit**: NO

- [x] 12. DDL 文件 & 全量映射验证（7 个文件 + 覆盖度检查）

  **What to do**:
  处理所有无需比较的文件，并验证全量覆盖度：
  
  **DDL 文件**（无需过程级比较，但需确认无遗漏的 procedures）：
  - `tables.sql` — DDL + 测试数据插入（包含 INSERT...SELECT generate_series）
  - `missing_tables.sql` — 纯 DDL
  - `pkg_mapper_param_test_tables.sql` — 纯 DDL（测试表结构）
  - `PKG_WARPDRIVER_STRESS_TEST-DDL.sql` — 纯 DDL（压力测试表）
  - `DAT_DATACLEAR_CONFIG.sql` — 纯 DDL（配置表）
  - `gauss_complete_examples.sql` — 已在 Task 6 覆盖
  
  **全量覆盖度验证**：
  1. 列出所有 44 个 SQL 文件
  2. 标记每个文件在 Tasks 1-11 中被哪个任务覆盖
  3. 确认无遗漏
  4. 确认 dest_py 和 dest_ru 的 Java 文件清单与 SQL 文件的映射关系

  **Must NOT do**:
  - 不修改任何文件

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 主要是验证和统计工作，不涉及深度比较
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 13
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `demo-project/sql/tables.sql` — DDL + 测试数据
  - `demo-project/sql/missing_tables.sql` — 纯 DDL
  - `demo-project/sql/pkg_mapper_param_test_tables.sql` — 纯 DDL
  - `demo-project/sql/PKG_WARPDRIVER_STRESS_TEST-DDL.sql` — 纯 DDL
  - `demo-project/sql/DAT_DATACLEAR_CONFIG.sql` — 纯 DDL

  **Acceptance Criteria**:
  - [ ] DDL 文件已确认无遗漏的 procedures
  - [ ] 全量覆盖度矩阵已完成（44 文件 × 2 输出目录）
  - [ ] 无遗漏文件

  **QA Scenarios**:
  ```
  Scenario: 全量覆盖度验证
    Tool: Bash (ls + read)
    Steps:
      1. 列出所有 44 个 SQL 文件
      2. 对每个文件，确认在 Tasks 1-11 中被覆盖
      3. 列出 dest_py 和 dest_ru 中所有 Service.java 文件
      4. 验证每个 Service.java 都能追溯到对应的 SQL 文件
    Expected Result: 44 个 SQL 文件全部有归属，无遗漏
    Evidence: .sisyphus/evidence/task-12-coverage-matrix.md
  ```

  **Commit**: NO

- [x] 13. 汇总所有比较结果，生成最终报告

  **What to do**:
  读取 Tasks 1-12 的所有比较证据（`.sisyphus/evidence/task-*`），汇总生成最终比较报告：
  
  1. **读取所有证据文件** — 汇总每个任务的比较结果
  2. **按差异类型分类**：
     - **Critical（语义不等价）**: 控制流缺失、逻辑错误、返回值类型错误
     - **Major（功能缺失）**: procedure 未转换、DML 语句遗漏、异常处理缺失
     - **Minor（风格差异）**: 命名偏差、注释丢失、变量作用域差异
  3. **按转换器统计**：
     - dest_py（Python 转换器）的差异清单
     - dest_ru（Rust 转换器）的差异清单
  4. **原因分析**：对每个差异给出根本原因（AST 解析限制、语义映射缺陷、不支持特性等）
  5. **改进建议**：针对每类差异给出具体可行的改进方案
  6. **写入报告**到 `docs/sql-java-comparison-report.md`

  **报告结构**：
  ```
  1. 概述（比较范围、方法、统计摘要）
  2. 差异总览表（按严重程度分类的汇总表）
  3. dest_py（Python 转换器）详细差异
     3.1 Critical 差异
     3.2 Major 差异
     3.3 Minor 差异
  4. dest_ru（Rust 转换器）详细差异
     4.1 Critical 差异
     4.2 Major 差异
     4.3 Minor 差异
  5. 未转换文件分析（MERGE 文件等）
  6. 原因分析汇总
  7. 改进建议
  8. 附录：完整文件映射表
  ```

  **Must NOT do**:
  - 不修改任何源代码或生成代码
  - 不做价值判断（哪个转换器"更好"）
  - 不引入新的代码文件

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: 核心工作是汇总数据、分析原因、撰写结构化报告
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential, depends on all Wave 1 tasks)
  - **Blocks**: F1, F2
  - **Blocked By**: Tasks 1-12

  **References**:
  **Pattern References**:
  - `.sisyphus/evidence/task-1-*.md` — CRUD 样式比较结果
  - `.sisyphus/evidence/task-2-*.md` — 业务逻辑比较结果
  - `.sisyphus/evidence/task-3-*.md` — 游标/报表比较结果
  - `.sisyphus/evidence/task-4-*.md` — GOTO 控制流比较结果
  - `.sisyphus/evidence/task-5-*.md` — 游标拆分验证结果
  - `.sisyphus/evidence/task-6-*.md` — 函数调用比较结果
  - `.sisyphus/evidence/task-7-*.md` — 类型/变量/函数比较结果
  - `.sisyphus/evidence/task-8-*.md` — 清算业务比较结果
  - `.sisyphus/evidence/task-9-*.md` — 压力测试比较结果
  - `.sisyphus/evidence/task-10-*.md` — 日志/金融比较结果
  - `.sisyphus/evidence/task-11-*.md` — MERGE 分析结果
  - `.sisyphus/evidence/task-12-coverage-matrix.md` — 全量覆盖度矩阵

  **Acceptance Criteria**:
  - [ ] 报告保存到 `docs/sql-java-comparison-report.md`
  - [ ] 报告包含所有 44 个 SQL 文件的处理结果
  - [ ] 差异按 Critical/Major/Minor 分级
  - [ ] 每个差异包含原因分析和改进建议
  - [ ] 报告结构清晰、可读性强

  **QA Scenarios**:
  ```
  Scenario: 报告完整性验证
    Tool: Bash (grep + read)
    Steps:
      1. 检查 docs/sql-java-comparison-report.md 是否存在
      2. 统计报告中提到的 SQL 文件数量（应为 44 或至少 37+5+2）
      3. 检查报告是否包含差异分级（Critical/Major/Minor）
      4. 检查报告是否包含原因分析章节
      5. 检查报告是否包含改进建议章节
    Expected Result: 报告文件存在，包含完整结构，覆盖所有文件
    Evidence: .sisyphus/evidence/task-13-report-completeness.md
  ```

  **Commit**: YES
  - Message: `docs: add SQL-Java conversion equivalence comparison report`
  - Files: `docs/sql-java-comparison-report.md`
  - Pre-commit: 无

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 2 review agents run in PARALLEL. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **报告完整性审计** — `oracle`
  读取生成的报告 end-to-end。验证：
  - 所有 37 个可比较文件均有比较结果
  - 每个差异条目包含：SQL 文件名、过程名、差异类型、具体描述
  - 原因分析有理有据（引用具体代码行）
  - 建议具体可操作（非泛泛而谈）
  - 报告结构清晰，分级合理
  Output: `Files [N/37 covered] | Differences [N total] | Critical [N] | Major [N] | Minor [N] | VERDICT: APPROVE/REJECT`

- [x] F2. **覆盖度验证** — `unspecified-high`
  对照文件清单，验证报告中每个 SQL 文件都被覆盖：
  - 37 个有输出的文件：逐一确认有比较结果
  - 5 个 DDL 文件：确认已标注为"无需比较"
  - 2 个 MERGE 文件：确认已标注为"未转换"并说明原因
  - 检查 Mapper.xml 文件是否也被纳入比较
  Output: `Coverage [N/44] | Missing [list] | VERDICT`

---

## Commit Strategy

- **Task 13 完成后**: 无需 commit（报告文件，非代码）
  - 如用户要求可 commit: `docs: add SQL-Java conversion equivalence comparison report`

---

## Success Criteria

### Final Checklist
- [x] 所有 37 个可比较 SQL 文件均已逐一与 dest_py 和 dest_ru 的 Java 输出比较
- [x] 5 个 DDL 文件和 2 个 MERGE 文件已在报告中说明
- [x] 差异按 Critical/Major/Minor 分级
- [x] 每个差异包含原因分析和改进建议
- [x] 报告保存到 `docs/sql-java-comparison-report.md`
