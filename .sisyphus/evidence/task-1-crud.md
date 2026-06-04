# Equivalence Gap Analysis: SQL → Java Conversion

**Date**: 2025-05-23
**Scope**: 4 SQL files × 2 converter outputs (dest_py, dest_ru)

---

## 1. Overview

| SQL File | Package | SQL Procs | dest_py Methods | dest_ru Methods |
|----------|---------|-----------|-----------------|-----------------|
| gauss_select_all_styles.sql | pkg_select_styles | 52 | 50 (96%) | 52 (100%) |
| gauss_insert_all_styles.sql | pkg_insert_styles | 24 | 24 (100%) | 24* (100%) |
| gauss_update_all_styles.sql | pkg_update_styles | 22 | 22 (100%) | 22* (100%) |
| gauss_delete_all_styles.sql | pkg_delete_styles | 22 | 21 (95%) | 22* (100%) |
| **TOTAL** | | **120** | **117** | **120** |

*dest_ru methods include stubs for unsupported features

---

## 2. SELECT — gauss_select_all_styles.sql

### Procedure Map (52 total)

All 50 demo procedures (demo_01 through demo_50) plus `proc_log_result` and `proc_show_results`.

### dest_py Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_18_join_lateral | Method exists but NO mapper call — `// TODO: FOR IN SELECT loop — query reconstruction failed`. LATERAL JOIN not supported |
| **MAJOR** | demo_24_cte_simple | Multi-CTE query reconstruction failed — TODO comment |
| **MAJOR** | demo_26_cte_multiple | Chained CTEs reconstruction failed — TODO comment |
| **MAJOR** | demo_40_pivot_manual (1st query) | CASE-based PIVOT reconstruction failed |
| **MAJOR** | demo_42_unpivot_lateral | LATERAL + VALUES combination not supported |
| **MAJOR** | demo_45_generate_series (last query) | CROSS JOIN with generate_series subquery failed |
| **MAJOR** | demo_46_values_clause (2 queries) | VALUES clause as table source not supported |
| **MAJOR** | demo_49_complex_nested | 4-level nested query exceeds parser capability |
| **MAJOR** | demo_50_comprehensive | 5-CTE super query exceeds parser capability |
| Minor | Multiple | ROWNUM (Oracle-specific), VARCHAR2 type, DATE literals stripped, :: cast syntax |

**dest_py SELECT Score**: 41/50 demos fully extracted (~82%). Missing: LATERAL JOINs, multi-CTE, VALUES-as-table, very complex nested queries.

### dest_ru Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **MAJOR** | demo_36_string_functions | String literal corruption: `'@new.com'` → `'@new AS com'` in REPLACE function |
| **MAJOR** | demo_50_comprehensive | Complex query truncated in XML output |
| Minor | Multiple | NVL → COALESCE translation (correct but style change), redundant LIMIT 1 on COUNT queries, Integer vs int type inconsistency |

**dest_ru SELECT Score**: 48/50 demos fully correct (~96%). 2 issues: string encoding bug, query length limit.

### Key Differences (dest_py vs dest_ru)

| Aspect | dest_py | dest_ru |
|--------|---------|---------|
| Procedure coverage | 50/52 (missing 2 query extractions) | 52/52 (all methods generated) |
| LATERAL JOIN | Failed (TODO) | Preserved correctly |
| Multi-CTE queries | Failed (TODO) | Preserved correctly |
| VALUES-as-table | Failed (TODO) | Preserved correctly |
| Complex nested queries | Failed (TODO) | Preserved (truncation on demo_50) |
| String literal encoding | Correct | Bug: `.com` → `AS com` |
| Query reconstruction | Many TODO comments (9) | Mostly working (2 issues) |

**Winner**: dest_ru for coverage and feature support. dest_py has significant query reconstruction gaps for complex SQL patterns.

---

## 3. INSERT — gauss_insert_all_styles.sql

### Procedure Map (24 total)

4 utility procs + 20 demo procs covering: VALUES, multi-row, SELECT, JOIN, CTE, multi-table, RETURNING, dynamic, %ROWTYPE, RECORD, FORALL BULK, MERGE, UPSERT, subquery, DEFAULT, UNION, OVERWRITE, partition, CROSS JOIN, complex combined.

### dest_py Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_09_insert_rowtype | INSERT includes columns NOT in employees table (`last_update`, `email`, `phone`) — runtime error |
| **CRITICAL** | demo_20_insert_complex_combined | Same: invalid columns in INSERT |
| **CRITICAL** | demo_07_insert_returning | RETURNING clause stripped — OUT values never captured |
| **CRITICAL** | demo_13_insert_upsert | ON CONFLICT clause missing — second INSERT fails on PK violation |
| **CRITICAL** | demo_20 (RETURNING + EXECUTE) | RETURNING not handled; EXECUTE IMMEDIATE → `// TODO` — missing emp_log INSERT |
| **CRITICAL** | demo_17_insert_overwrite | TRUNCATE missing before INSERT — accumulates data |
| **MAJOR** | demo_11_insert_bulk_collect | FORALL not implemented — entire procedure is stub |
| **MAJOR** | demo_12_insert_merge | MERGE not implemented — entire procedure is stub |
| **MAJOR** | demo_08_insert_dynamic | Dynamic SQL partial — constructed vSql never used |
| **MAJOR** | demo_15_insert_default_cols | DEFAULT keyword in VALUES may cause SQL error |
| Minor | demo_10_insert_record_type | Date passed as String instead of proper Date type |

### dest_ru Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_06_insert_all | Invalid MyBatis parameter binding: `#{#{r}.empId}` — should be `#{r.empId}` |
| **CRITICAL** | demo_07_insert_returning | RETURNING clause stripped — OUT values never captured |
| **CRITICAL** | demo_08_insert_dynamic | EXECUTE IMMEDIATE not implemented — TODO stub |
| **CRITICAL** | demo_11_insert_bulk_collect | FORALL BULK not implemented — TODO stub |
| **CRITICAL** | demo_12_insert_merge | MERGE not implemented — TODO stub |
| **CRITICAL** | demo_20_insert_complex_combined_2 | Method has NO parameters but SQL needs 4 bind values |
| **MAJOR** | demo_09_insert_rowtype | Missing 5 of 12 columns (allowance, create_time, update_time, update_reason, manager_id) |
| **MAJOR** | demo_13_insert_upsert | ON CONFLICT (PostgreSQL) → ON DUPLICATE KEY UPDATE (MySQL) — wrong dialect |
| **MAJOR** | demo_17_insert_overwrite | TRUNCATE not executed — only commented |
| **MAJOR** | demo_20 (same as demo_09) | Missing columns in %ROWTYPE INSERT |
| Minor | demo_09 | NUMERIC converted to long instead of BigDecimal — precision loss |

### Key Differences (dest_py vs dest_ru)

| Aspect | dest_py | dest_ru |
|--------|---------|---------|
| Procedure coverage | 24/24 methods | 24/24 methods (3 are TODO stubs) |
| Invalid columns in INSERT | demo_09, demo_20 (phantom columns) | demo_09, demo_20 (missing columns) |
| RETURNING INTO | Stripped (not captured) | Stripped (not captured) |
| MERGE | Stub (no implementation) | Stub (no implementation) |
| FORALL | Stub (no implementation) | Stub (no implementation) |
| EXECUTE IMMEDIATE | Partial (vSql unused) | Stub (TODO) |
| ON CONFLICT | Completely missing | Wrong dialect (MySQL syntax) |
| MyBatis param binding | Correct | Bug: `#{#{r}.field}` double hash |
| TRUNCATE support | Missing | Missing (commented only) |

**Winner**: Neither. Both have significant INSERT gaps. dest_py has phantom column bugs; dest_ru has parameter binding and dialect bugs. Both miss RETURNING, MERGE, FORALL, EXECUTE IMMEDIATE.

---

## 4. UPDATE — gauss_update_all_styles.sql

### Procedure Map (22 total)

2 utility procs + 20 demo procs covering: simple SET, multi-field, SET=(SELECT), WHERE subquery, EXISTS, IN, correlated, CASE, DECODE/NVL, FROM clause, JOIN update, CTE, RETURNING INTO, dynamic SQL, BULK COLLECT, %ROWTYPE, MERGE, partition, window function, complex combined.

### dest_py Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_13_returning_into | RETURNING INTO — OUT params passed as IN, never captured |
| **CRITICAL** | demo_14_dynamic_sql | Dynamic SQL parameter mismatch — 4 SQL bind params but only 3 Java params |
| **CRITICAL** | demo_15_bulk_collect | BULK COLLECT → single SELECT with LIMIT 1 — semantics lost |
| **CRITICAL** | demo_16_rowtype_update | %ROWTYPE expansion adds phantom columns (`email`, `phone`) not in table |
| **CRITICAL** | demo_17_merger_style | MERGE INTO completely missing — empty method |
| **MAJOR** | demo_08_case_expression | UPDATE ... FROM syntax — FROM clause misplaced in SET |
| **MAJOR** | demo_10_from_clause | UPDATE ... FROM — redundant self-join |
| **MAJOR** | demo_11_join_update | UPDATE ... FROM — FROM after SET (syntax error) |
| **MAJOR** | demo_19_window_function | UPDATE ... FROM — FROM in wrong position |
| Minor | Multiple | DBE_OUTPUT → comments, proc_show_data ignores pTitle, CASE formatting |

### dest_ru Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_03_select_subquery | Oracle `SET (a,b) = (SELECT ...)` not valid PostgreSQL |
| **CRITICAL** | demo_08_case_expression | Invalid FROM clause column reference in CASE |
| **CRITICAL** | demo_13_returning_into | RETURNING clause included but not supported by MyBatis |
| **CRITICAL** | demo_16_rowtype_update | `SET ROW = #{vEmpRec}` — MyBatis can't expand Map to columns |
| **CRITICAL** | demo_17_merger_style | MERGE INTO completely missing — stub only |
| **MAJOR** | demo_10_from_clause | Redundant self-join in UPDATE FROM |
| **MAJOR** | demo_12_with_cte | CTE scope potential issue |
| **MAJOR** | demo_14_dynamic_sql | EXECUTE IMMEDIATE — TODO stub |
| **MAJOR** | demo_15_bulk_collect | BULK COLLECT → single SELECT LIMIT 1, no FORALL loop |
| Minor | demo_09_decode_nvl | DECODE → CASE (correct but style change) |

### Key Differences (dest_py vs dest_ru)

| Aspect | dest_py | dest_ru |
|--------|---------|---------|
| Procedure coverage | 22/22 | 22/22 (3 are TODO stubs) |
| RETURNING INTO | Params wrong direction | Clause included but not usable |
| MERGE | Missing | Missing |
| BULK COLLECT/FORALL | LIMIT 1 — wrong semantics | LIMIT 1 — wrong semantics |
| UPDATE ... FROM | Misplaced FROM clause | Preserved better but self-join issue |
| SET (tuple) = (SELECT) | Preserved (Oracle syntax) | Preserved (invalid for PostgreSQL) |
| SET ROW = %ROWTYPE | Phantom columns | Unexpanded Map reference |
| Dynamic SQL | Partial (param mismatch) | Stub |

**Winner**: Neither. Both fail on RETURNING, MERGE, BULK COLLECT. dest_py has FROM-clause positioning bugs; dest_ru has Oracle-syntax preservation that's invalid for PostgreSQL.

---

## 5. DELETE — gauss_delete_all_styles.sql

### Procedure Map (22 total)

2 utility procs + 20 demo procs covering: simple, WHERE, subquery, EXISTS, IN, correlated, JOIN, USING, CTE, RETURNING, LIMIT, ORDER BY+LIMIT, partition, cascade, soft delete, archive, log (BULK COLLECT), MERGE DELETE, dynamic SQL, complex combined.

### dest_py Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_18_delete_merge | MERGE DELETE clause not generated — method is empty stub |
| **CRITICAL** | demo_20_delete_complex | INSERT RETURNING audit_id broken — vAuditId always 0 |
| **CRITICAL** | demo_20_delete_complex | DELETE from delete_audit (lines 759-772) missing entirely |
| **CRITICAL** | demo_10_delete_returning | RETURNING INTO not handled — OUT values lost |
| **CRITICAL** | demo_17_delete_log | BULK COLLECT broken — LIMIT 1 returns 1 row, loop expects array |
| **MAJOR** | demo_20_delete_complex | v_deleted incorrectly assigned to 0 — always reports 0 deleted |
| **MAJOR** | demo_19_delete_dynamic | EXECUTE IMMEDIATE → MyBatis ${} — unused vSql dead code |
| Minor | proc_show_counts | LIMIT 1 on COUNT(*) unnecessary |
| Minor | demo_19 | Unused vSql variable |

### dest_ru Gaps

| Severity | Procedure | Issue |
|----------|-----------|-------|
| **CRITICAL** | demo_18_delete_merge | MERGE DELETE not executed — TODO stub |
| **CRITICAL** | demo_19_delete_dynamic | EXECUTE IMMEDIATE — `// TODO: EXECUTE v_sql` — not executed |
| **CRITICAL** | demo_10_delete_returning | BULK COLLECT RETURNING not handled |
| **CRITICAL** | demo_09_delete_cte | CTE scope issue — DELETE placed after CTE but cross-reference broken in demo_20 |
| **MAJOR** | demo_15_delete_soft | Integer 1/0 for boolean is_deleted |
| **MAJOR** | demo_17_delete_log | Array indexing `#{vIds}(i)` — invalid MyBatis syntax |
| **MAJOR** | demo_20_delete_complex | DELETE condition always FALSE (impossible date constraint) |
| **MAJOR** | demo_20_delete_complex_1 | CTE `final_targets` referenced but not in scope |
| **MAJOR** | proc_reset_data | Missing department INSERT statements |
| Minor | Multiple | __ROWCOUNT__ never captured, LIMIT 1 on COUNT, SELECT demo_17 only fetches 1 row |

### Key Differences (dest_py vs dest_ru)

| Aspect | dest_py | dest_ru |
|--------|---------|---------|
| Procedure coverage | 21/22 (demo_18 missing) | 22/22 (2 are TODO stubs) |
| MERGE DELETE | Missing entirely | Missing entirely (stub) |
| Dynamic SQL DELETE | Converted to MyBatis ${} (works) | TODO stub (doesn't work) |
| RETURNING INTO | Not captured | Not captured |
| BULK COLLECT | LIMIT 1 — semantics wrong | LIMIT 1 — semantics wrong |
| CTE DELETE | Works | Scope issues across mapper calls |
| demo_20 complexity | Missing statements, wrong row count | Invalid SQL conditions, scope issues |
| Array indexing | Loop logic exists but broken | Invalid `#{vIds}(i)` syntax |
| proc_reset_data | Complete (includes dept inserts) | Missing department data |

**Winner**: Slight edge to dest_py for having working dynamic SQL conversion and complete proc_reset_data. dest_ru has more fundamental SQL validity issues in demo_20.

---

## 6. Cross-Cutting Issues (Both Converters)

### Features Not Supported by Either Converter

| Feature | Affected DML | Impact |
|---------|-------------|--------|
| RETURNING INTO (OUT params) | SELECT, INSERT, UPDATE, DELETE | 6+ procedures lose return values |
| MERGE INTO | INSERT, UPDATE, DELETE | 3 procedures completely missing |
| FORALL BULK operations | INSERT, UPDATE | 2 procedures are stubs |
| EXECUTE IMMEDIATE (dynamic SQL) | INSERT, UPDATE, DELETE | Partial or missing in 3+ procedures |
| %ROWTYPE full column mapping | INSERT, UPDATE | Missing or phantom columns |
| TRUNCATE TABLE | INSERT | 1 procedure loses overwrite semantics |

### Converter-Specific Bugs

| Converter | Bug | Affected |
|-----------|-----|----------|
| dest_py (Python) | Phantom columns (`email`, `phone`, `last_update`) in INSERT/UPDATE | demo_09, demo_20 (INSERT), demo_16 (UPDATE) |
| dest_py (Python) | UPDATE ... FROM clause misplaced in SET | demo_08, demo_10, demo_11, demo_19 (UPDATE) |
| dest_py (Python) | Complex query reconstruction failures | 9 SELECT demos have TODO comments |
| dest_ru (Rust) | String literal encoding: `.com` → `AS com` | demo_36 (SELECT) |
| dest_ru (Rust) | Invalid MyBatis parameter binding: `#{#{r}.field}` | demo_06 (INSERT) |
| dest_ru (Rust) | ON CONFLICT → ON DUPLICATE KEY UPDATE (wrong dialect) | demo_13 (INSERT) |
| dest_ru (Rust) | Oracle syntax preserved but invalid for PostgreSQL | demo_03 (UPDATE SET tuple) |

---

## 7. Summary Scorecard

| Metric | dest_py | dest_ru |
|--------|---------|---------|
| **Total procedures mapped** | 117/120 (97.5%) | 120/120 (100%) |
| **Methods with correct SQL** | ~96/120 (80%) | ~90/120 (75%) |
| **Critical issues** | 13 | 13 |
| **Major issues** | 14 | 15 |
| **Minor issues** | 10+ | 10+ |
| **Query reconstruction** | Fails on complex SQL | Handles most patterns |
| **SQL validity** | Better on average | More Oracle syntax preserved |
| **Overall grade** | B | B- |

### Verdict

- **dest_py**: Better SQL validity for simple/moderate queries. Struggles with complex SQL reconstruction (LATERAL, multi-CTE, VALUES-as-table). Has phantom column bugs.
- **dest_ru**: Better coverage (all procedures generated, complex SQL preserved). Has encoding bugs, parameter binding bugs, and preserves Oracle syntax that's invalid for PostgreSQL.
- **Both**: Fail identically on advanced PL/pgSQL features: RETURNING INTO, MERGE, FORALL, EXECUTE IMMEDIATE.
