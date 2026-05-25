# SQL ↔ Java (dest_py) 转换等价性对比报告 V2

**日期**: 2026-05-24  
**对比方法**: 6 组并行深度分析（37 个 SQL 文件 × 8 个维度）  
**基线文档**: docs/sql-java-comparison-spec.md

---

## 1. 概览

| 指标 | 数值 |
|------|------|
| 输入 SQL 文件 | 44（37 含存储过程，7 DDL） |
| dest_py 输出 Service 文件 | 35 |
| 转换器报告 | 350 过程，339 成功，11 存根 |
| 过程级覆盖率 | **~97%**（339/350 有方法体） |
| 🔴 Critical | **~35** |
| 🟡 Major | **~45** |
| 🟢 Minor | **~55** |

---

## 2. 差异总览表（按类型 × 严重程度）

| 差异类型 | 🔴 Critical | 🟡 Major | 🟢 Minor |
|----------|------------|---------|---------|
| GOTO 状态机缺少 break | 5 | 2 | 1 |
| RETURNING INTO 丢失 | 4 | 1 | 0 |
| BULK COLLECT/FORALL 降级 | 3 | 1 | 0 |
| DBE_SCHEDULER 完全存根 | 3 | 0 | 0 |
| 游标管道断裂 | 5 | 2 | 1 |
| 动态 SQL 处理不完整 | 3 | 3 | 1 |
| MERGE INTO 部分失效 | 2 | 1 | 0 |
| 日期计算缺失 | 2 | 0 | 0 |
| 函数不必要存根 | 0 | 11 | 0 |
| SQL%ROWCOUNT 未捕获 | 0 | 5 | 0 |
| FETCH FIRST/LIMIT 丢失 | 0 | 4 | 0 |
| DECODE 未转换 | 0 | 3 | 0 |
| FOR UPDATE 丢失 | 0 | 2 | 0 |
| 内层异常块丢失 | 2 | 2 | 0 |
| 编码乱码 | 0 | 0 | 5 |
| 注释/日志丢失 | 0 | 2 | 8 |

---

## 3. Critical 差异

### C-PY-01: GOTO 状态机系统性错误 — 缺少 break

**影响范围**: `ProcFiveGotosService`, `WarpdriverStressTestService`, `ComplexClearingPkgService`

转换器将 GOTO 转为 switch-case 状态机，但 case 分支之间缺少 break，导致 fall-through。

| 过程 → Java 方法 | 问题 |
|-----------------|------|
| `sp_order_state_machine` → `ProcFiveGotosService.spOrderStateMachine()` | StatePending → StatePaid 掉落 |
| `sp_goto_state_machine` → `WarpdriverStressTestService.spGotoStateMachine()` | StateInit → StatePending 掉落 |
| `handle_trade_change` → `ComplexClearingPkgService.handleTradeChange()` | HandleInsert → HandleUpdate → HandleDelete 全部执行 |
| `search_target` → `ProcGotoService.searchTarget()` | _gotoTarget 每次外循环重置，found_it 永远不到达 |
| `sp_main_orchestrator` → `WarpdriverStressTestService.spMainOrchestrator()` | 状态机仅 3 状态，所有业务逻辑丢失，无限循环 |

**根因**: `_process_statement()` 中 GOTO→状态机生成逻辑不检测顺序标签是否需要显式 break。

### C-PY-02: RETURNING INTO 完全丢失

| 过程 → Java 方法 | 影响 |
|-----------------|------|
| `demo_07_insert_returning` → `InsertStylesService.demo07InsertReturning()` | INSERT ... RETURNING 子句缺失，变量 null/0 |
| `demo_13_returning_into` → `UpdateStylesService.demo13ReturningInto()` | UPDATE ... RETURNING 缺失，old/new 值逻辑错误 |
| `demo_10_delete_returning` → `DeleteStylesService.demo10DeleteReturning()` | DELETE ... RETURNING 缺失 |
| `demo_25_plsql_returning` → `GaussFunctionCallsService.demo25PlsqlReturning()` | XML 不含 RETURNING，Java 读 _row 为空 |

**根因**: `_process_statement()` 处理 INSERT/UPDATE/DELETE 时未检测 RETURNING INTO 子句。

### C-PY-03: BULK COLLECT / FORALL 批量语义丢失

| 过程 → Java 方法 | 问题 | 影响 |
|-----------------|------|------|
| `demo_47_select_into` → `SelectStylesService.demo47SelectInto()` | BULK COLLECT INTO 加 LIMIT 1 | 循环上界 0，体不执行 |
| `demo_11_insert_bulk_collect` → `InsertStylesService.demo11InsertBulkCollect()` | FORALL 降级为逐行 INSERT | N 次 DB 往返 |
| `demo_15_bulk_collect` → `UpdateStylesService.demo15BulkCollect()` | BULK COLLECT 加 LIMIT 1 | 只更新 1 行 |

**根因**: 转换器将集合 SELECT 误判为标量查询；FORALL 未转为 MyBatis `<foreach>`。

### C-PY-04: DBE_SCHEDULER 全部存根

| 过程 → Java 方法 | 影响 |
|-----------------|------|
| `proc_aas_dataclear_zongkong` → `AasDataclearService` | 调度作业从未创建 |
| `proc_aas_lob_clear_zongkong` → `AasLobDataclearService` | LOB 清理作业从未调度 |
| `sp_dynamic_scheduler_dispatch` → `WarpdriverStressTestService` | 动态调度仅 TODO 注释 |

**根因**: DBE_SCHEDULER 调用无 Spring/Quartz 替代实现。

### C-PY-05: 游标管道完全断裂

| 过程 → Java 方法 | 问题 |
|-----------------|------|
| `proc_enhance_cursor` → `DynamicForLoopService` | 输入游标 Object pInCursor 从未读取 |
| `proc_full_pipeline` → `DynamicForLoopService` | 方法体为空 |
| `func_get_order_cursor` → `DynamicForLoopService` | 返回空 HashMap |
| `func_for_dynamic_to_json` → `DynamicForLoopService` | 空存根 |
| `proc_dynamic_for_processing` → `DynamicForLoopService` | FOR EXECUTE 循环体完全丢失 |

**根因**: 游标作为 IN 参数传递时，未将实参（List<Map>）赋值给局部 result 变量。

### C-PY-06: 其他 Critical

| 编号 | 过程 → Java 方法 | 问题 |
|------|-----------------|------|
| C-PY-06a | `proc_aas_dataclear_type2/3` → `AasDataclearService` | v_cleardate = null（日期计算缺失） |
| C-PY-06b | `sp_goto_cleanup_master` → `WarpdriverStressTestService` | found 变量从未 true，始终 ORDER_NOT_FOUND |
| C-PY-06c | `func_lookup_dimensions` → `MergeSalesService` | 存根返回空 Map，ETL 失效 |
| C-PY-06d | `proc_merge_sales_data` → `MergeSalesService` | tmp_merge_staging 未填充 |
| C-PY-06e | `fn_array_jsonb_processor` → `WarpdriverStressTestService` | JSONB 元素访问断裂 |
| C-PY-06f | `sp_savepoint_hell` → `WarpdriverStressTestService` | SAVEPOINT 仅注释 |
| C-PY-06g | `proc_list` → `_2008802001MgtService` | OUT SYS_REFCURSOR 未填充 |
| C-PY-06h | `prc_acnt_info_exp` → `DepositAcntInfoInquiryService` | SQL 注入 + 编码乱码 |

---

## 4. Major 差异

### M-PY-01: 11 个函数不必要存根

"函数缺少顶层 return 语句"——实际逻辑简单，不应存根：

| 函数 | 文件 | 原因 |
|------|------|------|
| `fn_get_emp_details` | GaussFunctionCallsService | NO_DATA_FOUND 异常路径未处理 |
| `fn_factorial` | GaussFunctionCallsService | 递归函数 |
| `fn_get_tax_rate` | GaussFunctionCallsService | IF/ELSIF 阶梯逻辑 |
| `fn_pipe_emp_list` | GaussFunctionCallsService | RETURN QUERY 管道 |
| `func_calc_bonus` | CompanyConstantsService | SELECT + 简单计算 |
| `func_validate_salary` | CompanyConstantsService | IF/ELSIF + 常量 |
| `func_get_dept_name` | CompanyConstantsService | CASE + 常量 |
| `process_observation_batch` | AstroFunctionsPkgService | 复杂过程 |
| `analyze_spectrum_features` | AstroFunctionsPkgService | 数组类型推断冲突 |
| `calc_fee` (3参数版) | ComplexClearingPkgService | 所有 return 在条件内 |
| `func_lookup_dimensions` | MergeSalesService | 返回复合类型 |

### M-PY-02: SQL%ROWCOUNT 未捕获

几乎所有 INSERT/UPDATE/DELETE 过程：MyBatis 返回 int（影响行数），但生成代码忽略返回值。

### M-PY-03 ~ M-PY-10 其他 Major

| 编号 | 问题 | 影响范围 |
|------|------|----------|
| M-PY-03 | MERGE INTO 临时表未正确创建 | MergeSalesService |
| M-PY-04 | EXECUTE IMMEDIATE 部分转 // TODO | AasDataclear TYPE4, 多个动态 SQL |
| M-PY-05 | COMMIT 分段提交丢失 | proc_sync_employee_bonus, sp_purge_logs |
| M-PY-06 | 内层异常块丢失 | proc_sync_employee_bonus, proc_process_dynamic_query |
| M-PY-07 | DECODE 未转 CASE | 多个 XML 文件 |
| M-PY-08 | FETCH FIRST/LIMIT 丢失 | demo_08_order_by, demo_09_limit_offset 等 |
| M-PY-09 | FOR UPDATE/FOR SHARE 丢失 | demo_48_for_update |
| M-PY-10 | TO_CHAR 格式不等价 | fnFormatSalary, formatAmount |

---

## 5. Minor 差异精选

| 问题 | 影响 |
|------|------|
| DBE_OUTPUT.PRINT_LINE 全部注释 | 输出行为丢失 |
| 中文注释乱码 | PackLogService, AasDataclearService 等 |
| SELECT INTO 变量初始化差异 | SQL null → Java 0 |
| COMMIT → @Transactional 注释 | 等价但粒度不同 |
| ROWNUM 保留为 rownum | GaussDB 可用，PostgreSQL 不可 |
| NVL 保留为 nvl() | GaussDB 可用，PostgreSQL 不可 |
| SYSTIMESTAMP → System.currentTimeMillis() | 分布式时间可能偏差 |

---

## 6. 正确转换亮点

| 维度 | 评估 | 说明 |
|------|------|------|
| 参数映射 | ✅ 优秀 | 类型、方向（IN/OUT/INOUT）正确 |
| 包常量值 | ✅ 完美 | 所有常量精确保留（不是 0/null） |
| 跨包服务调用 | ✅ 正确 | 自动注入依赖 |
| PERFORM 处理 | ✅ 正确 | 转为 void 方法调用 |
| PRAGMA AUTONOMOUS_TRANSACTION | ✅ 正确 | → @Transactional(propagation=REQUIRES_NEW) |
| 触发器保留 | ✅ 正确 | 以注释形式保留 |
| 60+ 内置函数 | ✅ 大部分正确 | substr/nvl/coalesce/upper/trim 等 |
| 核心业务 4 包 | ✅ 优秀 | 18/18 过程正确 |

---

## 7. 改进建议

| 优先级 | 建议 | 预计消除 |
|--------|------|----------|
| **P0** | 修复 GOTO 状态机：switch case 后加 break | 5 Critical |
| **P0** | 实现 RETURNING INTO：select + RETURNING 或 useGeneratedKeys | 4 Critical |
| **P0** | 修复 BULK COLLECT：移除 LIMIT 1，用 List<Map> | 3 Critical |
| **P1** | 修复游标参数传递：Object pCursor → List<Map> 强转 | 5 Critical |
| **P1** | 改进函数存根：为条件分支生成默认 return | 11 Major |
| **P1** | 捕获 SQL%ROWCOUNT：存储 MyBatis 返回值 | 5 Major |
| **P1** | 保留 FETCH FIRST/LIMIT/FOR UPDATE | 6 Major |
| **P2** | DBE_SCHEDULER → Spring @Scheduled | 3 Critical → 功能 |
| **P2** | 修复动态 SQL：bind 替代 ${} | 安全 + 正确 |
| **P2** | DECODE → CASE 转换 | 兼容性 |

---

## 8. 结论

**综合评级: B-**（核心业务 A，复杂特性 C-）

核心业务逻辑（order/payment/inventory/product + CRUD 120 个过程）转换质量优秀。差距集中在 GOTO 状态机、RETURNING INTO、BULK COLLECT、游标管道、DBE_SCHEDULER 五个领域。建议按 P0→P1→P2 顺序修复，每个修复后运行编译+测试验证。
