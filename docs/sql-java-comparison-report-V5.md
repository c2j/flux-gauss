# SQL ↔ Java (dest_ru) 转换等价性对比报告 V5

**日期**: 2026-05-25  
**对比方法**: 4 组并行深度分析 + 编译/测试验证（37 个 SQL 文件 × 8 个维度）  
**基线文档**: docs/sql-java-comparison-report-V4.md (dest_py, 2026-05-25)  
**对比基准**: docs/sql-java-comparison-spec.md  
**转换器版本**: Rust 版转换器（dest_ru/）

---

## 1. 概览

| 指标 | V4 基线 (dest_py) | V5 当前 (dest_ru) | 说明 |
|------|-------------------|-------------------|------|
| 输入 SQL 文件 | 44（37 含存储过程） | 44（37 含存储过程） | — |
| dest_ru Service 文件 | — | **37** | PKG_CURSOR 拆为 3 个，PKG_FOR 拆为 2 个 |
| dest_ru Mapper 文件 | — | **37** | 与 Service 一一对应 |
| dest_ru Mapper XML | — | **37** | 与 Service 一一对应 |
| 单元测试文件 | — | **37** | 每个服务对应一个 |
| 集成测试文件 | — | **37**（含 AbstractIntegrationTest） | 每个服务对应一个 |
| 编译 | — | ✅ BUILD SUCCESS | — |
| 单元测试通过 | 357 通过 / 32 跳过 | **297 通过 / 20 跳过 / 0 失败** | dest_ru 方法数较少 |
| 🔴 Critical | ~4 | **~8** | ↑ 详见第 3 节 |
| 🟡 Major | ~12 | **~14** | ↑ 详见第 4 节 |
| 🟢 Minor | ~20 | **~12** | ↓ |
| 综合评级 | A- | **B** | ↓ |

---

## 2. 差异总览表（按类型 × 严重程度）

| 差异类型 | 🔴 Critical | 🟡 Major | 🟢 Minor |
|----------|------------|---------|---------|
| 包常量值全为 null/0 | 1 | 0 | 0 |
| GOTO 状态机无条件覆盖 | 1 | 0 | 0 |
| pkg_type_test 整文件缺失 | 1 | 0 | 0 |
| 游标 OPEN 未生成 SELECT | 1 | 1 | 0 |
| REFCURSOR/分页游标返回空列表 | 1 | 0 | 0 |
| 多文件合并失败（DB_LOG/SWH） | 0 | 2 | 0 |
| DBE_SCHEDULER 完全存根 | 0 | 2 | 0 |
| 动态 SQL 占位符不完整 | 0 | 2 | 1 |
| FORALL 批量语义降级 | 0 | 1 | 0 |
| EXECUTE IMMEDIATE 存根 | 0 | 1 | 0 |
| SQL%ROWCOUNT 未捕获 | 0 | 1 | 0 |
| 内层异常块丢失 | 0 | 1 | 0 |
| 单元测试零断言 | 0 | 1 | 0 |
| 集成测试几乎零断言 | 0 | 1 | 0 |
| 注释丢失 / 编码问题 | 0 | 0 | 5 |
| 不必要的 String.valueOf 嵌套 | 0 | 0 | 4 |
| OpenCursorService 疑似 DynamicForLoopService 副本 | 0 | 0 | 2 |
| **合计** | **~5** | **~14** | **~12** |

---

## 3. Critical 差异详情

### C-RU-01: 包常量值全为 null/0

**文件**: gauss_package_constants.sql  
**过程名**: 包级常量 → CompanyConstantsService 静态字段  
**问题描述**: SQL 中定义了精确实际值（如 `COMPANY_NAME := '华夏科技有限公司'`、`DEPT_SALES := 10`、`MIN_SALARY := 3000.00`、`DEFAULT_BONUS_RATE := 0.10`），但 Java 代码中所有 String 常量 = `null`，所有 Integer 常量 = `0`，所有 BigDecimal 常量 = `BigDecimal.ZERO`。

**SQL 原始值 vs Java 值**:

| 常量 | SQL 值 | Java 值 |
|------|--------|---------|
| COMPANY_NAME | '华夏科技有限公司' | null |
| COMPANY_CODE | 'HXKJ' | null |
| FOUNDING_YEAR | 2015 | 0 |
| MIN_SALARY | 3000.00 | BigDecimal.ZERO |
| MAX_SALARY | 500000.00 | BigDecimal.ZERO |
| DEFAULT_BONUS_RATE | 0.10 | BigDecimal.ZERO |
| OVERTIME_RATE | 1.50 | BigDecimal.ZERO |
| DEPT_SALES | 10 | 0 |
| DEPT_TECH | 20 | 0 |
| DEPT_FINANCE | 30 | 0 |
| DEPT_HR | 40 | 0 |
| STATUS_ACTIVE | 'ACTIVE' | null |
| STATUS_PENDING | 'PENDING' | null |
| STATUS_INACTIVE | 'INACTIVE' | null |

**影响**: `funcCalcBonus()` 使用 `DEFAULTBONUSRATE`（应为 0.10 但为 0）计算奖金，结果恒为 0；`funcValidateSalary()` 使用 `MINSALARY`（应为 3000）和 `MAXSALARY`（应为 500000）验证薪资范围，结果恒为"OK"（任何值与 0 比较）；`funcGetDeptName()` 中所有部门 ID 匹配恒失败（DEPT_SALES=0 等）。

**根因**: Rust 版转换器未解析 `CONSTANT ... := value` 语法中的赋值表达式，仅生成了类型默认值。

---

### C-RU-02: GOTO 状态机无条件覆盖（switch-case 后无条件 `currentState = StateDone`）

**文件**: proc_Five_Gotos.sql, PKG_WARPDRIVER_STRESS_TEST.sql  
**过程名**: `sp_order_state_machine` → ProcFiveGotosService.spOrderStateMachine(), `sp_goto_state_machine` → WarpdriverStressTestService.spGotoStateMachine()  
**问题描述**: 每个 switch-case 分支中，条件语句正确设置了 `currentState`，但在 `break` 之前又**无条件地**执行 `currentState = SpXxxStateMachineState.StateDone`，覆盖了条件中设置的目标状态。

**示例**（ProcFiveGotosService L139-146）:
```java
case StateInit:
    if (java.util.Objects.equals(pEvent, "SUBMIT")) {
        procFiveGotosMapper.updateSpOrderStateMachine(pOrderId, pEvent);
        vCurrent = "PENDING";
        currentState = SpOrderStateMachineState.StatePending;  // ← 正确设置
    }
    currentState = SpOrderStateMachineState.StateDone;  // ← 无条件覆盖！
    break;
```

**正确行为**: 条件内设置 `currentState = StatePending` 后应 `break`，不再执行 `currentState = StateDone`。参考 SQL 的 `GOTO state_pending` 语义。

**影响**: 状态机**永远无法从初始状态转移**，无论传入什么事件，都直接进入 StateDone。这使整个状态机逻辑失效，所有状态转移结果恒为初始值。

**根因**: Rust 版转换器在生成 GOTO 状态机时，在每个 case 分支末尾无条件添加了"fall-through 到 Done"的代码，未能正确处理 if 分支内的 `break` 与 fall-through 的区分。

---

### C-RU-03: pkg_type_test 整文件缺失

**文件**: pkg_type_test.sql（15 个存储过程/函数）  
**问题描述**: SQL 文件包含 15 个过程（TYPE/RECORD/%TYPE/%ROWTYPE 测试），但 dest_ru 中**不存在** TypeTestService.java 或任何对应的文件。

**影响**: 15 个过程完全丢失，无法使用。对比 dest_py 中 TypeTestService 正常存在。

**根因**: Rust 版转换器可能不支持 PL/pgSQL TYPE/RECORD 声明的解析，导致整个包无法生成。

---

### C-RU-04: 游标 OPEN 未生成 mapper SELECT（死代码）

**文件**: pkg_cursor_patterns.sql  
**过程名**: `prc_cursor_walk`, `prc_cursor_conditional`  
**问题描述**: 这两个过程使用显式游标（OPEN/FETCH/CLOSE），但 Java 中游标循环体变成 `while(true) { ... if (!found) break; }`，`found` 初始为 `false`，因此循环体**永远不会执行**。Mapper XML 中缺少对应的 SELECT 语句。

**CursorPatternsService L49-54**:
```java
public void prcCursorWalk(int pMinId) {
    // ...
    boolean found = false;
    while (true) {
    // FETCH cursor;
    if (!found) { break; }  // ← 恒为 true，立即退出
    // ... 以下为死代码
    }
}
```

**影响**: 游标循环中的 update/insert 操作永远不会执行，功能完全失效。

**根因**: Rust 版转换器生成了 OPEN/FETCH/CLOSE 注释，但未将 FETCH 关联到实际的 mapper.select() 调用。

---

### C-RU-05: REFCURSOR/分页游标返回空列表

**文件**: pkg_employee_comments.sql  
**过程名**: `list_by_dept` → EmployeeCommentsService.listByDept()  
**问题描述**: 该过程使用 REFCURSOR 返回分页查询结果。Java 中虽然有 `selectListByDept()` 调用获取总数，但数据返回部分恒返回 `Collections.emptyList()`。

**EmployeeCommentsService L35-43**:
```java
if (vTotal <= 0) {
    return java.util.Collections.emptyList();  // ← 无数据时正确
}
vOffset = (pPage - 1) * pPageSize;
// OPEN cursor;
return java.util.Collections.emptyList();  // ← 有数据时也返回空！
```

**影响**: 分页查询永远返回空列表，无法获取实际数据。

**根因**: REFCURSOR 物化为 mapper SELECT 查询的逻辑未实现。

---

## 4. Major 差异详情

### M-RU-01: PackLogService 缺少 DB_LOG.sql 方法

**文件**: PACK_LOG.sql + DB_LOG.sql → PackLogService  
**问题描述**: 按 spec 预期，PackLogService 应包含两个 SQL 文件的方法。但实际仅包含 PACK_LOG.sql 的方法（7 个重载 log），**缺少** DB_LOG.sql 的方法。  
**影响**: DB_LOG.sql 中的日志方法丢失。  
**根因**: Rust 版转换器可能不支持多文件合并到同一个 Service。

---

### M-RU-02: TestService 缺少 SWH_ALL_KIND.sql 方法

**文件**: SWH_ALL_KIND.sql + pkg_test_patterns.sql → TestService  
**问题描述**: 按 spec 预期，TestService 应包含两个 SQL 文件的方法。但实际仅包含 pkg_test_patterns.sql 的方法（5 个），SWH_ALL_KIND.sql 的方法**完全缺失**。  
**影响**: SWH_ALL_KIND.sql 中的测试模式方法丢失。  
**根因**: 同 M-RU-01。

---

### M-RU-03: DBE_SCHEDULER 完全存根（7 处）

**文件**: PKG_AAS_DATACLEAR.sql, pkg_aas_lob_dataclear.sql  
**问题描述**: 所有 `DBE_SCHEDULER.CREATE_JOB`/`SET_JOB_ARGUMENT_VALUE`/`ENABLE` 调用均生成为注释，未转为 `@Scheduled` 或任何替代实现。  
**影响**: 7 处定时任务调度功能不可用。  
**根因**: Rust 版转换器未实现 DBE_SCHEDULER → Spring @Scheduled 的转换。

---

### M-RU-04: 游标 OPEN/FETCH/CLOSE 缺少 mapper SELECT（CursorLifecycleService 全空）

**文件**: PKG_CURSOR.sql → CursorLifecycleService  
**问题描述**: CursorLifecycleMapper **完全为空**（无任何方法），CursorLifecycleService 中的 4 个方法全部依赖注释中的 OPEN/FETCH/CLOSE，无实际 DB 操作。  
**影响**: 整个 CursorLifecycleService 不可用。

---

### M-RU-05: 动态 SQL（EXECUTE IMMEDIATE）占位符不完整

**文件**: 多个文件（WarpdriverStressTestService, ComplexClearingPkgService, AasDataclearService 等）  
**问题描述**: 所有 EXECUTE IMMEDIATE 语句生成为 `// TODO: EXECUTE ... — could not resolve SQL string` 注释。部分 TODO 包含 SQL 文本和建议实现方式，部分仅简单注释。  
**影响**: 约 15+ 处动态 SQL 需人工补全参数绑定。

---

### M-RU-06: FORALL 批量语义降级

**文件**: gauss_insert_all_styles.sql, gauss_update_all_styles.sql  
**过程名**: `demo_11_insert_bulk_collect`, `demo_15_bulk_collect`  
**问题描述**: SQL `FORALL i IN 1..v_count EXECUTE ...` 转为逐条 for 循环调用 mapper。  
**影响**: 性能差异（非功能差异），高并发场景效率低。

---

### M-RU-07: EXECUTE IMMEDIATE 方法存根

**文件**: 多个文件  
**过程名**: `procProcessDynamicQuery`, `demo08InsertDynamic`, `demo14DynamicSql`, `demo19DeleteDynamic`, `fnDynamicExecutor`, `spMainOrchestrator`, `spGotoLoopPurge`, `spDynamicSchedulerDispatch`, `searchTarget`  
**问题描述**: 共约 9 个方法为空壳或 TODO 存根。  
**影响**: 这些方法需人工审查和实现。

---

### M-RU-08: SQL%ROWCOUNT 未捕获

**文件**: proc_Five_Gotos.sql → ProcFiveGotosService  
**过程名**: `sp_purge_logs`  
**问题描述**: `v_rowcount := SQL%ROWCOUNT` 转为 `v_rowcount = __ROWCOUNT__`，但 `__ROWCOUNT__` 是硬编码的 `0`，从未被 MyBatis 返回值更新。  
**影响**: 循环条件 `vRowcount == vBatch` 恒为 `0 == 1000` = false，循环只执行一次。

---

### M-RU-09: 内层异常块丢失

**文件**: ComplexClearingPkgService  
**过程名**: `logAuditAutonomous`  
**问题描述**: SQL 中 `PRAGMA AUTONOMOUS_TRANSACTION` 正确转为 `@Transactional(propagation=REQUIRES_NEW)`，但方法体中的异常处理简化为空 catch 块。  
**影响**: 异常信息丢失。

---

### M-RU-10: 单元测试零断言/verify

**文件**: 全部 37 个 *Test.java  
**问题描述**: **所有 37 个单元测试文件中 `verify()`/`assertNotNull()`/`assertEquals()` 出现次数均为 0**。测试仅 mock mapper 调用后执行 service 方法，不验证任何行为。  
**影响**: 单元测试仅验证"方法不抛异常"，无法检测业务逻辑错误。对比 dest_py V4 中每个测试有 `verify(mapper, atLeast(0)).methodName()` 调用。

---

### M-RU-11: 集成测试几乎零断言

**文件**: 全部 37 个 *IntegrationTest.java  
**问题描述**: 37 个集成测试中仅 **6 个** 包含 `assertNotNull` 断言（AstroFunctionsPkg 2个, Common 2个, CompanyConstants 3个, ComplexClearingPkg 7个, CustomFuncs 2个, ForInSelect 2个, Payment 1个, WarpdriverStressTest 4个），其余 31 个集成测试文件断言数为 0。  
**影响**: 大部分集成测试仅验证"方法不抛异常"，无法检测数据正确性。

---

### M-RU-12: CursorAdvancedService 100% 存根

**文件**: PKG_CURSOR.sql → CursorAdvancedService  
**问题描述**: 2 个公开方法全部为空壳 stub（`procDynamicForProcessing`, `funcForDynamicToJson`），虽然对应 Mapper 有 SELECT 方法。  
**影响**: 动态游标处理功能完全不可用。

---

### M-RU-13: FunctionCallsService 存在未解析表达式

**文件**: gauss_function_calls.sql → FunctionCallsService  
**过程名**: `demo20PlsqlLoop`, `demo28PlsqlRecursion`  
**问题描述**: 多处出现 `/* unresolved */ 0` 表达式，阶乘函数结果恒为 0，循环上限恒为 0。  
**影响**: 递归/循环类演示方法的计算结果不正确。

---

### M-RU-14: DynamicForLoopService 与 OpenCursorService 内容几乎相同

**文件**: PKG_FOR.sql → DynamicForLoopService, OpenCursorService  
**问题描述**: 两个 Service 的方法签名和实现**完全相同**（仅类名和 Mapper 注入不同）。  
**影响**: 可能是 PKG_FOR.sql 中两个包（`pkg_dynamic_for_loop` 和 `pkg_open_cursor`）被分别转换，但方法逻辑完全重复。功能上不算错误，但造成代码冗余。

---

## 5. Minor 差异汇总

| # | 差异 | 文件 | 说明 |
|---|------|------|------|
| 1 | 不必要的 String.valueOf 嵌套 | 多个 Service | 如 `String.valueOf(String.valueOf(String.valueOf(...)))`，最深达 16 层 |
| 2 | DBE_OUTPUT.PRINT_LINE 转为注释 | 全局 | 所有 PRINT_LINE 调用转为 `// CALL DBE_OUTPUT.PRINT_LINE(...)` |
| 3 | 中文注释编码异常 | 部分 Service | UTF-8 常量值正常显示，但部分注释中文乱码 |
| 4 | 源码行号标注为 1-1 | 多个 Service | 多处 `Source: file.sql:1-1`，未记录实际行号范围 |
| 5 | DBE_SCHEDULER PERFORM 注释保留 | AasDataclearService | `// PERFORM: DBE_SCHEDULER.CREATE_JOB(...)` 作为注释保留 |
| 6 | jsonb 工具方法内嵌 Service | WarpdriverStressTestService | jsonbArrayLength/jsonbBuildObject 等工具方法内嵌在 Service 中 |
| 7 | Collections.emptyList() 用于 FOR 循环 | ProcFiveGotosService | `for (Map<String,Object> _orderRec : java.util.Collections.<Map<String,Object>>emptyList())` 循环体永不执行 |
| 8 | 事务注解 @Transactional 覆盖 | ~60% 方法 | 大部分写操作有 @Transactional，但部分缺失 |
| 9 | Commit 注释 | 多个 Service | `// COMMIT;` 作为注释保留 |
| 10 | SAVEPOINT 注释 | WarpdriverStressTestService | `// SAVEPOINT sp_notify` 作为注释保留 |
| 11 | 方法定名不一致 | gauss_function_calls.sql | dest_py 为 `GaussFunctionCallsService`，dest_ru 为 `FunctionCallsService` |
| 12 | SalaryUpdateService 标记 TODO | gauss_update_select.sql | 3 个方法体正常但注释标记 TODO |

---

## 6. 测试覆盖率分析

### 单元测试

| 指标 | 数量 |
|------|------|
| 总测试方法 | 297 |
| 通过 | 297 |
| 失败 | 0 |
| 跳过 | 20 |
| 跳过原因 - while 循环无法终止 | ~10 |
| 跳过原因 - stub 过程 | ~5 |
| 跳过原因 - 复杂 mock | ~5 |
| **verify/assert 断言** | **0（全部 37 个测试文件）** |

### 集成测试

| 指标 | 数量 |
|------|------|
| 集成测试类 | 37（含 AbstractIntegrationTest） |
| 有 assertNotNull 断言的文件 | 6 / 37 |
| 零断言的文件 | 31 / 37 |
| fixture SQL 文件 | 有（@Sql 注解引用） |

### 测试质量评估

**dest_ru 单元测试的主要问题**:

1. **零断言**: 所有 37 个单元测试文件中 `verify()`/`assertNotNull()`/`assertEquals()` 出现次数均为 0。测试仅 mock 所有 mapper 调用后执行 service 方法，不验证任何行为。即使方法返回错误结果（如常量全为 0），测试也会通过。

2. **集成测试断言稀疏**: 31/37 个集成测试文件断言数为 0，仅执行 service 方法但不检查返回值或数据库状态。

3. **Mock 过度**: OrderServiceTest 中每个测试方法都 mock 了**所有** mapper 方法（包括不属于当前测试的方法），如 `test_cancelOrder_success()` 中 mock 了 `insertCreateOrder`（属于 createOrder）。

**对比 dest_py V4**: dest_py 有 `verify(mapper, atLeast(0))` 断言，集成测试有 `assertNotNull(oa.get())` 断言。

---

## 7. 良好转换领域

| 特性 | 评级 | 说明 |
|------|------|------|
| DML CRUD 等价性（120+ 过程） | ✅ 优秀 | 表名/字段/条件与 SQL 一致 |
| 跨包服务调用 | ✅ 正确 | 自动 @Autowired 注入 |
| @Transactional 注解 | ✅ 大部分正确 | 覆盖大部分写操作 |
| 内置函数映射 | ✅ 正确 | BuiltinFuncsService 中 substr/nvl/trim/ceil 等映射正确 |
| GOTO 状态机结构 | ⚠️ 有缺陷 | enum + switch-case + _smGuard 结构正确，但无条件覆盖 bug |
| Package 拆分 | ✅ 合理 | PKG_CURSOR 拆为 3 个 Service，更细粒度 |
| Mapper XML SQL 保持 | ✅ 优秀 | CTE、窗口函数、JOIN 均精确保留 |
| jsonb 工具方法 | ✅ 实用 | 内嵌 Jackson 实现 jsonb 操作 |

---

## 8. 文件映射完整性

| SQL 文件 | dest_ru Service | 方法数 | 状态 |
|----------|----------------|--------|------|
| gauss_select_all_styles.sql | SelectStylesService | 52 | ✅ |
| gauss_insert_all_styles.sql | InsertStylesService | 24 | ✅ 1 stub |
| gauss_update_all_styles.sql | UpdateStylesService | 22 | ✅ 1 stub |
| gauss_delete_all_styles.sql | DeleteStylesService | 22 | ✅ |
| gauss_function_calls.sql | FunctionCallsService | 35 | ⚠️ 有 unresolved |
| gauss_package_constants.sql | CompanyConstantsService | 7 | ❌ 常量值全错 |
| gauss_update_select.sql | SalaryUpdateService | 3 | ✅ |
| gauss_complete_examples.sql | → ForInSelectService | (2 func) | ✅ 合并 |
| pkg_order.sql | OrderService | 5 | ✅ |
| pkg_payment.sql | PaymentService | 4 | ✅ |
| pkg_inventory.sql | InventoryService | 4 | ✅ |
| pkg_product.sql | ProductService | 5 | ✅ |
| pkg_common.sql | CommonService | 4 | ✅ |
| pkg_report.sql | ReportService | 4 | ✅ |
| pkg_cursor_patterns.sql | CursorPatternsService | 3 | ⚠️ 2 方法游标死代码 |
| pkg_employee_comments.sql | EmployeeCommentsService | 5 | ⚠️ listByDept 返回空 |
| proc_GOto.sql | ProcGotoService | 3 | ⚠️ 1 stub |
| proc_Five_Gotos.sql | ProcFiveGotosService | 5 | ❌ 状态机 bug |
| PKG_CURSOR.sql | CursorAdvancedService + CursorLifecycleService + DynamicForLoopService | 2+4+3 | ❌ 2 stub + Mapper 全空 |
| PKG_FOR.sql | ForInSelectService + OpenCursorService | 2+3 | ⚠️ 可能重复 |
| complex_clearing_pkg.sql | ComplexClearingPkgService | 12 | ✅ 3 TODO |
| PKG_AAS_DATACLEAR.sql | AasDataclearService | 6 | ⚠️ DBE_SCHEDULER stub |
| pkg_aas_lob_dataclear.sql | AasLobDataclearService | 2 | ⚠️ DBE_SCHEDULER stub |
| PKG_2008802001_MGT.sql | _2008802001MgtService | 8 | ✅ |
| PKG_RPT_BATCH_DOWNLOAD.sql | RptBatchDownloadService | 1 | ✅ |
| PKG_WARPDRIVER_STRESS_TEST.sql | WarpdriverStressTestService | 15 | ❌ 状态机 bug + 2 stub |
| PKG_DEPOSIT_ACNT_INFO_INQUIRY.sql | DepositAcntInfoInquiryService | 2 | ✅ |
| PACK_LOG.sql + DB_LOG.sql | PackLogService | 7 | ❌ 缺 DB_LOG |
| SWH_ALL_KIND.sql + pkg_test_patterns.sql | TestService | 5 | ❌ 缺 SWH_ALL_KIND |
| astro_functions_pkg.sql | AstroFunctionsPkgService | 5 | ⚠️ 3 stub |
| pkg_builtin_funcs_test.sql | BuiltinFuncsService | 1 | ✅ |
| pkg_custom_funcs_test.sql | CustomFuncsService | 3 | ✅ |
| pkg_package_vars_test.sql | PackageVarsTestService | 3 | ✅ |
| pkg_mapper_param_test.sql | MapperParamTestService | 9 | ✅ |
| pkg_test_patterns.sql | → TestService | 5 | ✅ |
| **pkg_type_test.sql** | **❌ 不存在** | **0** | **❌ 整文件缺失** |

---

## 9. dest_py V4 vs dest_ru V5 对比

| 维度 | dest_py V4 | dest_ru V5 | 优势方 |
|------|-----------|-----------|--------|
| Service 文件数 | 36 | 37 | dest_ru（更细粒度拆分） |
| 方法总数 | ~350 | ~297 | dest_py（覆盖更全） |
| 编译 | ✅ | ✅ | 持平 |
| 单元测试通过 | 357 通过 / 32 跳过 | 297 通过 / 20 跳过 | dest_py（更多覆盖） |
| 单元测试 verify() | ✅ 有 | ❌ 无 | **dest_py** |
| 集成测试断言 | ✅ assertNotNull + DML 注释 | ❌ 31/37 零断言 | **dest_py** |
| 包常量值 | ✅ 精确保留 | ❌ 全为 null/0 | **dest_py** |
| GOTO 状态机 | ✅ enum + switch + break | ❌ 无条件覆盖 bug | **dest_py** |
| pkg_type_test | ✅ 存在 | ❌ 缺失 | **dest_py** |
| 游标 SELECT | ✅ 有对应 mapper | ⚠️ 多处缺失 | **dest_py** |
| REFCURSOR | ✅ 物化为查询 | ❌ 返回空列表 | **dest_py** |
| 多文件合并 | ✅ 正确 | ❌ 缺少目标文件方法 | **dest_py** |
| 内置函数 | ✅ 60+ 映射 | ✅ 映射正确但较少 | 持平 |
| DML CRUD | ✅ 优秀 | ✅ 优秀 | 持平 |
| Mapper XML SQL | ✅ 优秀 | ✅ 优秀 | 持平 |
| **综合评级** | **A-** | **B** | **dest_py** |

---

## 10. 改进建议

| 优先级 | 建议 | 预计消除 | 难度 |
|--------|------|----------|------|
| **P0** | 修复常量值解析：提取 `CONSTANT ... := value` 的赋值表达式 | 1 Critical | 中 |
| **P0** | 修复 GOTO 状态机：条件分支内设置 currentState 后应立即 break | 1 Critical | 低 |
| **P0** | 补充 pkg_type_test 支持：解析 TYPE/RECORD 声明 | 1 Critical | 高 |
| **P0** | 修复游标 OPEN→SELECT：显式游标需生成 mapper SELECT 方法 | 1 Critical | 中 |
| **P1** | 修复 REFCURSOR 物化：分页查询应返回实际数据 | 1 Critical | 中 |
| **P1** | 支持多文件合并：PackLogService + TestService | 2 Major | 中 |
| **P1** | DBE_SCHEDULER → Spring @Scheduled 替代 | 2 Major | 高 |
| **P1** | 捕获 SQL%ROWCOUNT：存储 MyBatis 返回值 | 1 Major | 低 |
| **P1** | 单元测试添加 verify() 调用 | 1 Major | 低 |
| **P1** | 集成测试添加 assertNotNull 断言 | 1 Major | 低 |
| **P2** | FORALL 批量优化：转 MyBatis batch executor | 1 Major | 中 |
| **P2** | 动态 SQL bind 变量追踪 | 多个 TODO | 高 |
| **P2** | 内层异常块恢复 | 1 Major | 中 |
| **P2** | 修复源码行号标注（1-1 → 实际行号） | Minor | 低 |
| **P2** | 消除 String.valueOf 不必要嵌套 | Minor | 低 |

---

## 11. 结论

**综合评级: B**（核心业务 B+，复杂特性 C+，测试 C）

### 核心发现

1. **dest_ru 整体可用但有 5 个 Critical 差异**，其中 3 个（常量值、状态机、游标 SELECT）直接影响业务逻辑正确性，2 个（pkg_type_test 缺失、REFCURSOR 空返回）影响功能完整性。

2. **测试质量是最大短板**: 全部 37 个单元测试零断言，31/37 个集成测试零断言。即使业务代码有明显的 bug（如常量全为 0），测试也无法发现。对比 dest_py V4 的 verify() + assertNotNull，差距显著。

3. **dest_ru 在以下方面优于 dest_py**: 无（当前版本在所有关键指标上均落后于 dest_py V4）。

4. **dest_ru 在以下方面可改进**: 常量值提取、GOTO 状态机 break 逻辑、游标物化、测试断言生成是最高优先级的 4 项改进。

### 与基线对比（spec §6）

| 指标 | 首次基线 | V5 当前 | 变化 |
|------|---------|---------|------|
| 有输出的 SQL 文件 | 35/37 | **36/37** | ↑ +1（仅 pkg_type_test 缺失） |
| 过程级覆盖率 | ~80% | **~85%** | ↑ +5% |
| Critical 差异 | ~35 | **~5** | ↓ -30（大幅改善） |
| Major 差异 | ~22 | **~14** | ↓ -8 |
| 综合评级 | C+ | **B** | ↑ |

相比首次基线，Critical 从 ~35 降至 ~5，改善显著。但与 dest_py V4（A-）仍有差距。

---

*报告由 Sisyphus AI 基于 4 组并行探索 + 编译测试验证自动生成。*
*覆盖 37 个 SQL 文件、37 个 Service 类、37 个 Mapper XML、37 个单元测试、37 个集成测试。*
