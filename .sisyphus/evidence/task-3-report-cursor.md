# Task 3: Report, Common, Cursor Patterns, Employee Comments — Comparison Evidence

**Date**: 2026-05-23
**SQL Sources**: `demo-project/sql/pkg_{report,common,cursor_patterns,employee_comments}.sql`
**Converters**: dest_py (Python/flux_gauss.py) vs dest_ru (Rust converter)

---

## Summary Table

| SQL Package | Procedures in SQL | dest_py Methods | dest_ru Methods | Verdict |
|---|---|---|---|---|
| pkg_report | 4 | 4 | 4 | ✅ Both complete |
| pkg_common | 4 (2 func + 2 proc) | 4 | 4 | ✅ Both complete |
| pkg_cursor_patterns | 3 | 3 | 3 | ⚠️ Cursor lifecycle broken in dest_ru |
| pkg_employee_comments | 5 | 5 | 5 | ⚠️ REFCURSOR data lost in dest_ru |

---

## 1. pkg_report.sql

**SQL Procedures**: `generate_daily_report`, `generate_sales_report`, `export_report_to_file`, `cleanup_old_reports`

### Procedure-by-Procedure Comparison

#### generate_daily_report(p_date VARCHAR)
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| Method name | `generateDailyReport(String pDate)` | `generateDailyReport(String pDate)` | ✅ Same |
| CALL pkg_order.get_order_detail(0) | `orderService.getOrderDetail(0)` | `orderService.getOrderDetail(0)` | ✅ Both correct |
| PERFORM pkg_payment.query_payment_status(0) | `paymentService.queryPaymentStatus(0)` | `paymentService.queryPaymentStatus(0)` | ✅ Both correct |
| INSERT into t_reports | `reportMapper.insertGenerateDailyReport(pDate)` with CURRENT_TIMESTAMP via XML | `reportMapper.insertGenerateDailyReport(pDate)` with CURRENT_TIMESTAMP via XML | ✅ Both correct |
| PERFORM pkg_common.log_operation | `commonService.logOperation("REPORT","DAILY",0)` | `commonService.logOperation("REPORT","DAILY",0)` | ✅ Both correct |
| @Transactional | ✅ Present | ✅ Present | ✅ Same |

#### generate_sales_report(p_start_date, p_end_date)
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| Content concat `p_start_date \|\| '~' \|\| p_end_date` | XML: `#{pStartDate} \|\| '~' \|\| #{pEndDate}` | XML: `#{pStartDate} \|\| '~' \|\| #{pEndDate}` | ✅ Both correct |

#### export_report_to_file(p_report_id BIGINT)
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| Just calls log_operation | ✅ Correct | ✅ Correct | ✅ Same |
| @Transactional | ❌ Absent (correct — single PERFORM) | ❌ Absent | ✅ Same |

#### cleanup_old_reports(p_days INT)
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| DELETE WHERE generated_at < CURRENT_DATE - p_days | XML correct with `<` escaped | XML correct with `&lt;` escaped | ✅ Both correct |

### Mapper Comparison
| Aspect | dest_py | dest_ru |
|---|---|---|
| Mapper methods | 3 (insert×2, delete×1) | 3 (insert×2, delete×1) |
| XML formatting | Multi-line, indented | Single-line, compact |
| SQL correctness | ✅ | ✅ |

**pkg_report Verdict: ✅ EQUIVALENT — Both converters produce functionally identical output.**

---

## 2. pkg_common.sql

**SQL Routines**: `get_sys_date()` (FUNCTION), `format_amount(p_amount)` (FUNCTION), `log_operation(p_module, p_action, p_target_id)` (PROCEDURE), `send_notification(p_channel, p_message)` (PROCEDURE)

### get_sys_date() → getSysDate()
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| Return type | `java.sql.Timestamp` | `java.sql.Timestamp` | TIMESTAMP |
| Implementation | `new java.sql.Timestamp(System.currentTimeMillis())` | `new java.sql.Timestamp(System.currentTimeMillis())` | `RETURN CURRENT_TIMESTAMP` |
| Correctness | ✅ Semantically correct | ✅ Semantically correct | ✅ |

### format_amount(p_amount NUMERIC) → formatAmount(BigDecimal)
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| SQL: `TO_CHAR(p_amount, 'FM999,999,999.00')` | Returns formatted string with `String.format("%.2f", ...)` pattern | Returns `String.valueOf(pAmount)` — **NO formatting** | **❌ dest_ru loses formatting** |
| Severity | — | **Minor** — Function stub approximates | — |

**Note**: Neither converter faithfully reproduces the `TO_CHAR` format mask. dest_py attempts a close approximation; dest_ru just does toString.

### log_operation / send_notification
| Aspect | dest_py | dest_ru | SQL Source |
|---|---|---|---|
| Mapper methods | 2 (insertLogOperation, insertSendNotification) | 2 (same names) | ✅ Same |
| SQL in XML | `CURRENT_TIMESTAMP` for created_at/sent_at | `CURRENT_TIMESTAMP` for created_at/sent_at | ✅ Both correct |
| @Transactional | ✅ On both | ✅ On both | ✅ Same |

**pkg_common Verdict: ✅ MOSTLY EQUIVALENT — Minor: dest_ru formatAmount() is a bare toString stub vs dest_py's formatted approach.**

---

## 3. pkg_cursor_patterns.sql ⚠️ CRITICAL ANALYSIS

**SQL Procedures**: `prc_for_select`, `prc_cursor_walk`, `prc_cursor_conditional`

### Case 1: FOR IN SELECT loop → prc_for_select
**SQL Pattern**:
```sql
FOR v_rec IN (SELECT id, name, status FROM t_users WHERE status = p_status ORDER BY id) LOOP
    v_total := v_total + 1;
    INSERT INTO t_audit(user_id, action) VALUES(v_rec.id, 'processed');
END LOOP;
```

| Aspect | dest_py | dest_ru | Verdict |
|---|---|---|---|
| Cursor → Java | `List<Map<String, Object>> vRecList = mapper.selectPrcForSelect(pStatus)` | `List<Map<String, Object>> vRecList = mapper.selectPrcForSelect(pStatus)` | ✅ Both correct |
| FOR loop | `for (Map<String, Object> vRec : vRecList)` | `for (Map<String, Object> vRec : vRecList)` | ✅ Both correct |
| v_rec.id access in INSERT | XML: `#{vRec.id}` | XML: `#{vRec.id}` | ✅ Both correct |
| Null safety | `if (vRecList == null) vRecList = new ArrayList<>()` | `if (vRecList == null) vRecList = new ArrayList<>()` | ✅ Both correct |
| SELECT SQL in XML | Multi-line formatted, columns listed | Wrapped in parens: `( select id, name, status ... )` | **Minor formatting diff** |

**Case 1 Verdict: ✅ EQUIVALENT**

### Case 2: Explicit cursor OPEN/FETCH/CLOSE → prc_cursor_walk ⚠️ CRITICAL
**SQL Pattern**:
```sql
OPEN v_cur FOR SELECT id, name FROM t_users WHERE id > p_min_id ORDER BY id;
LOOP
    FETCH v_cur INTO v_id, v_name;
    EXIT WHEN NOT FOUND;
    v_cnt := v_cnt + 1;
    UPDATE t_users SET processed = 1 WHERE id = v_id;
END LOOP;
CLOSE v_cur;
```

| Aspect | dest_py | dest_ru | Severity |
|---|---|---|---|
| OPEN cursor → SELECT | `vCurResult = mapper.selectPrcCursorWalk(pMinId)` — **fetches all rows into List** | `// OPEN cursor;` comment only — **NO mapper call, NO data fetched** | **🔴 CRITICAL** |
| FETCH INTO variables | `_row.get("v_id")`, `_row.get("v_name")` with null-safe casts | `// FETCH cursor;` comment only — **NO row extraction** | **🔴 CRITICAL** |
| CLOSE cursor | `// cursor v_cur closed` comment after loop | `// CLOSE vCur;` comment | Minor |
| Loop mechanism | `while(true)` with `found = vCurIdx < vCurResult.size()` check | `while(true)` with `if (!found) break` | **🟡 Major** — dest_ru loop never fetches, found is always false |
| Actual loop execution | ✅ Iterates over rows, processes each | ❌ Loop body **NEVER executes** — found starts false, never set true | **🔴 CRITICAL** |
| UPDATE inside loop | `mapper.updatePrcCursorWalk(pMinId, vId)` — called per row | `mapper.updatePrcCursorWalk(pMinId, vId)` — **NEVER called** (dead code in unreachable loop body) | **🔴 CRITICAL** |
| INSERT after loop | `mapper.insertPrcCursorWalk(pMinId, vCnt)` — records count | `mapper.insertPrcCursorWalk(pMinId, vCnt)` — records 0 (vCnt never incremented) | **🔴 CRITICAL** |

**Mapper XML Comparison — prc_cursor_walk SELECT**:

| dest_py | dest_ru |
|---|---|
| ✅ Has `<select id="selectPrcCursorWalk">` with `SELECT id, name FROM t_users WHERE id > #{pMinId}` | ❌ **MISSING** — no selectPrcCursorWalk in XML at all! |
| Has insert and update statements | Has insert and update statements only |

**Mapper.java Comparison — prc_cursor_walk**:

| dest_py | dest_ru |
|---|---|
| `selectPrcCursorWalk(@Param("pMinId") Integer pMinId)` → `List<Map<String,Object>>` | ❌ **MISSING** — no select method in Mapper interface |

**Case 2 Verdict: 🔴 CRITICAL FAILURE in dest_ru — Cursor OPEN/FETCH/CLOSE pattern produces dead code. The SELECT is never emitted, no data is fetched, the loop body never executes, and all per-row operations are skipped. dest_py handles this correctly.**

### Case 3: Cursor with IF inside loop → prc_cursor_conditional ⚠️ CRITICAL
**SQL Pattern**: OPEN/FETCH/CLOSE with ELSIF/ELSE branching

| Aspect | dest_py | dest_ru | Severity |
|---|---|---|---|
| OPEN cursor → SELECT | `mapper.selectPrcCursorConditional(pStatus)` — fetches rows | `// OPEN cursor;` — comment only, NO data fetch | **🔴 CRITICAL** |
| FETCH INTO variables | Extracts v_id, v_name, v_balance from _row Map | `// FETCH cursor;` — comment only | **🔴 CRITICAL** |
| IF/ELSIF/ELSE logic | `vBalance.compareTo(BigDecimal.valueOf(10000)) > 0` etc. — **works on fetched data** | Same comparison logic — but **vBalance is always ZERO** | **🔴 CRITICAL** |
| INSERT/UPDATE inside IFs | Called conditionally based on fetched values | **Dead code** — conditions never met because values never fetched | **🔴 CRITICAL** |
| selectPrcCursorConditional in Mapper.java | ✅ Present | ❌ **MISSING** | **🔴 CRITICAL** |
| selectPrcCursorConditional in Mapper.xml | ✅ Present (`SELECT id, name, balance FROM t_accounts WHERE status = #{pStatus}`) | ❌ **MISSING** | **🔴 CRITICAL** |

**Case 3 Verdict: 🔴 CRITICAL FAILURE in dest_ru — Same pattern as Case 2. Cursor operations reduced to comments. No data flows through the method.**

---

## 4. pkg_employee_comments.sql

**SQL Procedures**: `list_by_dept`, `add_employee`, `transfer_dept`, `resign`, `batch_import`

### Comment Preservation Comparison

#### Leading Comments (package header block)
**SQL Source** (lines 1-7):
```
-- 员工管理模块 (pkg_employee_comments)
-- 业务领域: 人力资源
-- 创建日期: 2024-06-15
-- 作者: zhangsan
-- 最后修改: lisi, 2024-08-20 — 增加了批量导入功能
```

| dest_py | dest_ru |
|---|---|
| ✅ Preserved as `// ============================================================================` block comments in Service.java before `listByDept` | ❌ **NOT preserved** — no leading_comments anywhere |
| ✅ In Mapper.java comments | ❌ Not present |
| ✅ In Mapper.xml as `<!-- -->` comments | ❌ Not present |

#### Inline Comments

**Example — "先查总数" (line 26)**:
| dest_py | dest_ru |
|---|---|
| `// 先查总数` ✅ | ❌ Missing |

**Example — "校验邮箱唯一性" (line 70)**:
| dest_py | dest_ru |
|---|---|
| `// 校验邮箱唯一性` ✅ | ❌ Missing |

**Example — Block comment "分页查询主数据" (lines 39-41)**:
| dest_py | dest_ru |
|---|---|
| `// 分页查询主数据 使用 row_number() 实现服务端分页 注意: PostgreSQL 中 OFFSET 从 0 开始` ✅ | ❌ Missing |

**Example — "JUST FOR TEST COMMENTS" (line 171)**:
| dest_py | dest_ru |
|---|---|
| `// JUST FOR TEST COMMENTS` ✅ | ❌ Missing |

**Example — Block comment "离职处理" (lines 136-139)**:
| dest_py | dest_ru |
|---|---|
| `// 离职处理 - 将员工状态改为 INACTIVE - 保留历史记录，不物理删除 - 触发资产归还流程的通知` ✅ | ❌ Missing |

### Procedure-by-Procedure Comparison

#### list_by_dept (REFCURSOR pattern) ⚠️ CRITICAL
**SQL**: Returns `out_list OUT REFCURSOR` with paginated query

| Aspect | dest_py | dest_ru | Severity |
|---|---|---|---|
| OUT params | `AtomicReference<String> outCode, outMsg` + return `List<Map<String,Object>>` | Same signature | ✅ Same |
| REFCURSOR for empty result | `selectListByDept_1` → `SELECT NULL WHERE 1 = 0` | Returns `Collections.emptyList()` — no SQL | **🟡 Major** |
| REFCURSOR for data | `selectListByDept_2` → Full paginated SELECT with `row_number() OVER(...)` | Returns `Collections.emptyList()` — **ALL paginated data LOST** | **🔴 CRITICAL** |
| Mapper SQL count query | ✅ `selectListByDept` returns count | ✅ `selectListByDept` returns count | ✅ Both have |
| Mapper SQL empty query | ✅ `selectListByDept_1` → `SELECT NULL WHERE 1=0` | ❌ Missing | **Major** |
| Mapper SQL paginated query | ✅ `selectListByDept_2` → full SELECT with LIMIT/OFFSET | ❌ Missing — **data never retrieved** | **🔴 CRITICAL** |
| Return value | Returns `outListResult` (the fetched list) | Always returns `Collections.emptyList()` | **🔴 CRITICAL** |

**list_by_dept Verdict: 🔴 CRITICAL — dest_ru loses all REFCURSOR data. The paginated employee query is never generated. Method always returns empty.**

#### add_employee
| Aspect | dest_py | dest_ru | Severity |
|---|---|---|---|
| Email check SELECT | ✅ `selectAddEmployee` count query | ✅ Same | ✅ |
| INSERT employee | ✅ `insertAddEmployee` | ✅ Same | ✅ |
| Get new ID | ✅ `selectAddEmployee_1` → `SELECT max(id)` | ✅ Same | ✅ |
| Cross-service calls | `commonService.logOperation` + `commonService.sendNotification` | Same | ✅ |
| String concat "邮箱已存在: " | `"邮箱已存在: " + pEmail` | `String.valueOf("邮箱已存在: ").concat(String.valueOf(pEmail))` — verbose but correct | Minor |
| String concat "欢迎新员工: " | `"欢迎新员工: " + pName` | `String.valueOf("欢迎新员工: ").concat(...)` | Minor |
| Mapper extra params | 3 params (pName, pEmail, pDeptId, pHireDate) | 5 params (adds vCount, vEmpId as pass-through) | **🟡 Minor** — unnecessary extra params |

#### transfer_dept
| Aspect | dest_py | dest_ru | Severity |
|---|---|---|---|
| SELECT current dept | ✅ `selectTransferDept` | ✅ Same | ✅ |
| Comparison | `vOldDeptId.compareTo(pNewDeptId) == 0` (null-safe) | `vOldDeptId == pNewDeptId` (Long reference equality!) | **🔴 Critical** — `==` on Long objects may fail for values outside -128..127 |
| UPDATE dept | ✅ | ✅ | ✅ |
| Mapper extra params | 2 (pEmpId, pNewDeptId) | 3 (adds vOldDeptId as pass-through) | Minor |

#### resign
| Aspect | dest_py | dest_ru |
|---|---|---|
| UPDATE status → INACTIVE | ✅ | ✅ |
| logOperation + 2× sendNotification | ✅ | ✅ |

#### batch_import
| Aspect | dest_py | dest_ru |
|---|---|---|
| INSERT batch_user | ✅ | ✅ |
| logOperation | ✅ | ✅ |

---

## Severity Summary

### 🔴 Critical Issues (dest_ru only)

| # | Package | Issue | Impact |
|---|---|---|---|
| C1 | cursor_patterns | `prcCursorWalk`: OPEN cursor → no SELECT generated, no FETCH logic, loop body dead code | Entire procedure non-functional |
| C2 | cursor_patterns | `prcCursorConditional`: Same OPEN/FETCH/CLOSE failure | Entire procedure non-functional |
| C3 | employee_comments | `listByDept`: REFCURSOR paginated query never generated, always returns empty list | Data retrieval completely broken |
| C4 | employee_comments | `transferDept`: Uses `==` for Long comparison instead of `.equals()` or `.compareTo()` | Potential reference equality bug |

### 🟡 Major Issues (dest_ru only)

| # | Package | Issue | Impact |
|---|---|---|---|
| M1 | cursor_patterns | Missing `selectPrcCursorWalk` in Mapper.java and Mapper.xml | Cursor OPEN not materialized |
| M2 | cursor_patterns | Missing `selectPrcCursorConditional` in Mapper.java and Mapper.xml | Cursor OPEN not materialized |
| M3 | employee_comments | Missing `selectListByDept_1` and `selectListByDept_2` in Mapper.java and Mapper.xml | REFCURSOR queries not materialized |

### 🟢 Minor Issues

| # | Package | Converter | Issue |
|---|---|---|---|
| m1 | common | dest_ru | `formatAmount()` returns `String.valueOf(pAmount)` instead of formatted output |
| m2 | employee_comments | dest_ru | Extra unnecessary pass-through params in Mapper methods (vCount, vEmpId, vOldDeptId) |
| m3 | employee_comments | dest_ru | Verbose string concat patterns `String.valueOf(x).concat(...)` instead of `+` operator |
| m4 | cursor_patterns | dest_ru | XML SELECT wrapped in unnecessary parentheses |
| m5 | all | dest_ru | SQL in XML is single-line compact (readability) vs dest_py's multi-line formatted |
| m6 | all | dest_ru | Source line tracking shows `:1-1` instead of actual line ranges |

### dest_py-Specific Notes
- Inline comments preserved correctly from SQL source (both single-line `--` and block `/* */`)
- Leading comments preserved as `// ===` header blocks
- Cursor OPEN → `mapper.select*()` with full FETCH INTO row extraction
- REFCURSOR → multiple mapper SELECT methods with `_1`, `_2` suffixes
- Long comparison uses `.compareTo()` (null-safe)

### dest_ru-Specific Notes
- ❌ Zero inline comment preservation
- ❌ Zero leading comment preservation
- ❌ Cursor OPEN/FETCH/CLOSE → placeholder comments, no SQL generated
- ❌ REFCURSOR → returns `Collections.emptyList()`, no query materialization
- Source location tracking broken (always `:1-1`)

---

## Overall Assessment

| Package | dest_py | dest_ru |
|---|---|---|
| pkg_report | ✅ Complete & Correct | ✅ Complete & Correct |
| pkg_common | ✅ Complete (formatAmount approximated) | ✅ Complete (formatAmount less accurate) |
| pkg_cursor_patterns | ✅ Complete — all 3 cursor patterns work correctly | 🔴 BROKEN — 2 of 3 procedures non-functional |
| pkg_employee_comments | ✅ Complete — comments preserved, REFCURSOR works | 🔴 BROKEN — REFCURSOR lost, comment preservation absent, Long comparison bug |

**Root Cause Analysis (dest_ru)**:
1. **Cursor lifecycle**: The Rust converter does not materialize OPEN/FETCH/CLOSE operations into mapper SELECT calls. It emits placeholder comments (`// OPEN cursor;`, `// FETCH cursor;`) but never generates the corresponding mapper methods or SQL.
2. **REFCURSOR**: OPEN ... FOR SELECT patterns are not converted to actual MyBatis queries. The return path is hardcoded to `Collections.emptyList()`.
3. **Comment handling**: No extraction or injection of SQL comments (leading or inline) into generated Java code.
4. **Long comparison**: Primitive vs reference equality issue in generated code.
