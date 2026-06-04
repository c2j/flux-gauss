# Task 5: PKG_CURSOR / PKG_FOR / Package Constants — Comparison Evidence

## 1. PKG_CURSOR.sql — Procedure Accounting

### SQL Source: 3 packages, 11 procedures/functions total

| # | Package | Procedure/Function | Type | SQL Lines |
|---|---------|-------------------|------|-----------|
| 1 | pkg_dynamic_for_loop | proc_dynamic_for_processing | PROCEDURE | 38-152 |
| 2 | pkg_dynamic_for_loop | func_for_dynamic_to_json | FUNCTION | 157-196 |
| 3 | pkg_cursor_advanced | proc_cursor_dynamic_using | PROCEDURE | 282-367 |
| 4 | pkg_cursor_advanced | func_get_order_cursor | FUNCTION | 372-402 |
| 5 | pkg_cursor_advanced | proc_multi_cursor_return | PROCEDURE | 407-436 |
| 6 | pkg_cursor_advanced | proc_cursor_transform | PROCEDURE | 441-475 |
| 7 | pkg_cursor_advanced | proc_paginate_with_using | PROCEDURE | 480-511 |
| 8 | pkg_cursor_lifecycle | proc_get_raw_cursor | PROCEDURE | 551-562 |
| 9 | pkg_cursor_lifecycle | proc_enhance_cursor | PROCEDURE | 564-605 |
| 10 | pkg_cursor_lifecycle | proc_consume_cursor | PROCEDURE | 607-636 |
| 11 | pkg_cursor_lifecycle | proc_full_pipeline | PROCEDURE | 639-649 |

### dest_py: DynamicForLoopService.java — ALL 11 accounted for

| Method | Source Package | Quality |
|--------|---------------|---------|
| procDynamicForProcessing | pkg_dynamic_for_loop | Attempted conversion; ERROR in FOR IN EXECUTE IMMEDIATE loop body |
| funcForDynamicToJson | pkg_dynamic_for_loop | Stub — JSON ops TODO |
| procCursorDynamicUsing | pkg_cursor_advanced | Full conversion with CASE branching for status counts |
| funcGetOrderCursor | pkg_cursor_advanced | Returns Map, SQL inlined |
| procMultiCursorReturn | pkg_cursor_advanced | Full conversion with dual cursor (AtomicReference) |
| procCursorTransform | pkg_cursor_advanced | Full conversion with FETCH loop + temp table |
| procPaginateWithUsing | pkg_cursor_advanced | Full conversion with dual cursor |
| procGetRawCursor | pkg_cursor_lifecycle | Converted — cursor open via mapper |
| procEnhanceCursor | pkg_cursor_lifecycle | Full conversion with temp table + FETCH loop |
| procConsumeCursor | pkg_cursor_lifecycle | Full conversion with FETCH loop + summary string |
| procFullPipeline | pkg_cursor_lifecycle | Partial — calls 3 procs but no PRINT_LINE |

**NOTE**: dest_py also has `CursorPatternsService.java` but it maps to `pkg_cursor_patterns.sql` (a DIFFERENT file), NOT to PKG_CURSOR.sql.

### dest_ru: Split into CursorAdvancedService + CursorLifecycleService — 6 of 11 accounted for

**CursorAdvancedService.java** (maps to PKG_CURSOR.sql):
| Method | Source Package | Quality |
|--------|---------------|---------|
| procDynamicForProcessing | pkg_dynamic_for_loop | STUB — "complex PL/pgSQL pattern" |
| funcForDynamicToJson | pkg_dynamic_for_loop | STUB — returns null |

**CursorLifecycleService.java** (maps to PKG_CURSOR.sql):
| Method | Source Package | Quality |
|--------|---------------|---------|
| procGetRawCursor | pkg_cursor_lifecycle | Minimal — just cursor comment |
| procEnhanceCursor | pkg_cursor_lifecycle | Partial — FETCH skeleton + TODO dynamic SQL |
| procConsumeCursor | pkg_cursor_lifecycle | Full FETCH loop with summary string |
| procFullPipeline | pkg_cursor_lifecycle | Full — calls self methods correctly |

**NOTE**: dest_ru also has `CursorPatternsService.java` but it maps to `pkg_cursor_patterns.sql` (DIFFERENT file).

### CRITICAL FINDING: dest_ru MISSING 5 procedures from pkg_cursor_advanced

| # | Missing Procedure | Status |
|---|-------------------|--------|
| 1 | proc_cursor_dynamic_using | NOT FOUND in any dest_ru service |
| 2 | func_get_order_cursor | NOT FOUND in any dest_ru service |
| 3 | proc_multi_cursor_return | NOT FOUND in any dest_ru service |
| 4 | proc_cursor_transform | NOT FOUND in any dest_ru service |
| 5 | proc_paginate_with_using | NOT FOUND in any dest_ru service |

These 5 procedures from `pkg_cursor_advanced` are completely absent from dest_ru output. The `CursorAdvancedService.java` in dest_ru only contains methods from `pkg_dynamic_for_loop`, not from `pkg_cursor_advanced` despite the class name suggesting otherwise.

### Naming Confusion in dest_ru
- `CursorAdvancedService.java` contains `pkg_dynamic_for_loop` methods (not `pkg_cursor_advanced`)
- `pkg_cursor_advanced` methods are entirely missing
- This is a naming/classification bug in the Rust converter

---

## 2. PKG_FOR.sql — FOR Loop Patterns

### SQL Source: 2 packages, 5 procedures/functions

| # | Package | Procedure | Type | Pattern |
|---|---------|-----------|------|---------|
| 1 | pkg_for_in_select | func_get_bonus_rate | FUNCTION | Simple CASE return |
| 2 | pkg_for_in_select | proc_sync_employee_bonus | PROCEDURE | FOR v_emp IN (SELECT ...) LOOP |
| 3 | pkg_open_cursor | proc_get_employee_cursor | PROCEDURE | OPEN p_result FOR SELECT (SYS_REFCURSOR OUT) |
| 4 | pkg_open_cursor | proc_process_dynamic_query | PROCEDURE | OPEN v_cursor FOR v_sql + FETCH/CLOSE |
| 5 | pkg_open_cursor | proc_paginated_query | PROCEDURE | OPEN cursor FOR SELECT (dual cursor OUT) |

### dest_py: ForInSelectService.java — ALL 5 accounted for

| Method | Pattern Converted | Quality |
|--------|-------------------|---------|
| funcGetBonusRate | CASE → if/else if chain | GOOD |
| procSyncEmployeeBonus | FOR IN SELECT → TODO stub, query reconstruction failed | PARTIAL — loop body missing |
| procGetEmployeeCursor | OPEN cursor FOR SELECT → AtomicReference + mapper | Minimal — cursor opened via mapper |
| procProcessDynamicQuery | OPEN cursor FOR v_sql + FETCH → List + while loop | GOOD — full FETCH/CASE/COMMIT logic |
| procPaginatedQuery | Dual cursor OPEN FOR → dual List + AtomicReference | GOOD |

**FOR IN SELECT pattern verification**:
- `procSyncEmployeeBonus`: Comment says `// TODO: FOR IN SELECT loop — query reconstruction failed` — the FOR IN SELECT loop was NOT converted to `for(Map row : mapper.select())`. The surrounding DML is present but the loop body is missing.
- `procProcessDynamicQuery`: Explicit cursor (OPEN FOR dynamic SQL) correctly converted to `while(true)` with index-based FETCH pattern. ✅

**Mapper XML** (dest_py/ForInSelectMapper.xml): Contains 11 SQL statements covering all DML operations. Well-structured with source comments.

### dest_ru: Split into ForInSelectService + OpenCursorService — CRITICAL ISSUES

**ForInSelectService.java** (sources `gauss_complete_examples.sql` — WRONG SOURCE FILE):
| Method | Source | Quality |
|--------|--------|---------|
| stringSplit | gauss_complete_examples.sql | UTILITY — not from PKG_FOR.sql |
| arrayLength | gauss_complete_examples.sql | UTILITY — not from PKG_FOR.sql |

**OpenCursorService.java** (sources PKG_FOR.sql):
| Method | Source Package | Quality |
|--------|---------------|---------|
| procGetEmployeeCursor | pkg_open_cursor | Minimal — just cursor comment |
| procProcessDynamicQuery | pkg_open_cursor | STUB — "complex PL/pgSQL pattern" |
| procPaginatedQuery | pkg_open_cursor | Minimal — just offset calc + cursor comments |

### CRITICAL FINDING: dest_ru MISSING pkg_for_in_select procedures

| # | Missing Procedure | Status |
|---|-------------------|--------|
| 1 | func_get_bonus_rate | NOT FOUND in any dest_ru service |
| 2 | proc_sync_employee_bonus | NOT FOUND in any dest_ru service |

`pkg_for_in_select` is entirely absent from dest_ru. The `ForInSelectService.java` in dest_ru maps to `gauss_complete_examples.sql` instead of `PKG_FOR.sql`, and contains only utility functions `stringSplit` and `arrayLength`.

**Mapper XML** (dest_ru/ForInSelectMapper.xml): EMPTY — contains no SQL statements at all.

**FOR loop patterns in dest_ru**:
- `procProcessDynamicQuery` is a stub — the explicit cursor (OPEN/FETCH/CLOSE) pattern was NOT converted
- `procPaginatedQuery` — minimal skeleton, cursor opens are just comments

---

## 3. gauss_package_constants.sql — Package Constants

### SQL Source: 1 package, 7 procedures + constants/variables

**Public Constants (20)**:
- COMPANY_NAME, COMPANY_CODE, FOUNDING_YEAR
- MIN_SALARY, MAX_SALARY, DEFAULT_BONUS_RATE, OVERTIME_RATE
- DEPT_SALES, DEPT_TECH, DEPT_FINANCE, DEPT_HR
- STATUS_ACTIVE, STATUS_INACTIVE, STATUS_PENDING
- FMT_DATE, FMT_DATETIME, FMT_MONTH

**Public Variables (8)**:
- g_current_user, g_session_id, g_operation_time
- g_current_dept_id, g_bonus_adjustment
- g_total_processed, g_total_bonus_paid
- g_audit_enabled, g_debug_mode

**Private Constants (2)**: PRIVATE_LOG_TABLE, PERF_THRESHOLD_MS
**Private Variables (2)**: v_internal_call_count, v_last_error_time

**Procedures (7)**:
- proc_init_session, func_calc_bonus, func_validate_salary
- func_get_dept_name, proc_log_operation, proc_batch_calc_bonus, proc_show_globals

### dest_py: CompanyConstantsService.java — ALL 7 procedures present

**Constants conversion**:
- ✅ All 20 public constants → `private static final` fields with correct values
- ✅ Public variables → instance fields with defaults
- ✅ Private constants → `private static final`
- ✅ Private variables → instance fields

**Constant values** (dest_py):
```java
private static final String companyName = "华夏科技有限公司";
private static final Integer foundingYear = 2015;
private static final java.math.BigDecimal minSalary = new java.math.BigDecimal("3000.00");
private static final Integer deptSales = 10;
// ... all correct values
```

**Procedure quality**:
| Method | Quality |
|--------|---------|
| procInitSession | GOOD — assigns variables correctly |
| funcCalcBonus | STUB — "complex PL/pgSQL pattern requires manual implementation" |
| funcValidateSalary | STUB — "complex PL/pgSQL pattern requires manual implementation" |
| funcGetDeptName | STUB — "complex PL/pgSQL pattern requires manual implementation" |
| procLogOperation | PARTIAL — increment counter + audit check |
| procBatchCalcBonus | GOOD — has FOR loop with mapper query |
| procShowGlobals | MINIMAL — just PRINT_LINE stubs |

**Mapper XML** (dest_py): 2 SQL statements — selectFuncCalcBonus, selectProcBatchCalcBonus

### dest_ru: CompanyConstantsService.java — ALL 7 procedures present

**Constants conversion — CRITICAL BUG: All constants are null/zero**:
```java
private static final String STATUSPENDING = null;
private static final Integer DEPTHR = 0;
private static final String COMPANYNAME = null;
private static final Integer FOUNDINGYEAR = 0;
private static final java.math.BigDecimal MINSALARY = java.math.BigDecimal.ZERO;
private static final java.math.BigDecimal MAXSALARY = java.math.BigDecimal.ZERO;
// ... ALL values are default/zero/null
```

**Variable scoping issue**: dest_ru makes all variables `static` including mutable ones (`gTotalProcessed`, `gBonusAdjustment`, etc.), which is incorrect for a Spring singleton — concurrent requests will share mutable state.

**Procedure quality**:
| Method | Quality |
|--------|---------|
| procInitSession | GOOD — assigns variables correctly |
| funcCalcBonus | GOOD — actual SQL query + bonus calculation + exception handling |
| funcValidateSalary | GOOD — actual salary comparison logic |
| funcGetDeptName | GOOD — actual CASE logic with department names |
| procLogOperation | PARTIAL — increment counter + audit check |
| procBatchCalcBonus | GOOD — has FOR loop with mapper query |
| procShowGlobals | MINIMAL — just PRINT_LINE stubs |

**Mapper XML** (dest_ru): 2 SQL statements — same as dest_py but with different column name mapping (emp_id vs employee_id)

### Constants Conversion Comparison

| Aspect | dest_py | dest_ru |
|--------|---------|---------|
| All 20 constants declared | ✅ | ✅ |
| Constant VALUES correct | ✅ All values populated | ❌ All null/zero/default |
| Variable scoping | ✅ Instance fields | ❌ All static |
| Naming convention | camelCase (companyName) | UPPER_CASE (COMPANYNAME) |
| Private constants included | ✅ | ✅ |
| Private variables included | ✅ | ✅ |

---

## 4. Summary of Critical Divergences

### PKG_CURSOR.sql
- **dest_py**: Single `DynamicForLoopService.java` with all 11 procedures (some partial/stub)
- **dest_ru**: Split into `CursorAdvancedService` (2 stubs) + `CursorLifecycleService` (4 methods) = **6 of 11 procedures**
- **MISSING in dest_ru**: All 5 `pkg_cursor_advanced` procedures (proc_cursor_dynamic_using, func_get_order_cursor, proc_multi_cursor_return, proc_cursor_transform, proc_paginate_with_using)
- **Naming bug in dest_ru**: CursorAdvancedService contains pkg_dynamic_for_loop methods, not pkg_cursor_advanced

### PKG_FOR.sql
- **dest_py**: Single `ForInSelectService.java` with all 5 procedures
- **dest_ru**: Split into `OpenCursorService` (3 partial) + `ForInSelectService` (2 utility methods from WRONG file) = **3 of 5 PKG_FOR procedures**
- **MISSING in dest_ru**: Both `pkg_for_in_select` procedures (func_get_bonus_rate, proc_sync_employee_bonus)
- **Source mapping bug**: dest_ru's ForInSelectService maps to gauss_complete_examples.sql instead of PKG_FOR.sql
- **dest_ru ForInSelectMapper.xml is EMPTY**

### gauss_package_constants.sql
- **dest_py**: All 7 procedures, all constants with correct VALUES
- **dest_ru**: All 7 procedures, all constants declared but ALL VALUES are null/0/default
- **dest_ru advantage**: funcCalcBonus, funcValidateSalary, funcGetDeptName have actual implementations vs dest_py stubs
- **dest_ru bug**: All mutable variables are static → thread-safety issue

---

## 5. Procedure Count Summary

| SQL File | Total Procs | dest_py Found | dest_ru Found | dest_py Missing | dest_ru Missing |
|----------|-------------|---------------|---------------|-----------------|-----------------|
| PKG_CURSOR.sql | 11 | 11/11 | 6/11 | 0 | **5** (pkg_cursor_advanced) |
| PKG_FOR.sql | 5 | 5/5 | 3/5 | 0 | **2** (pkg_for_in_select) |
| gauss_package_constants.sql | 7 | 7/7 | 7/7 | 0 | 0 |
| **TOTAL** | **23** | **23/23** | **16/23** | **0** | **7** |
