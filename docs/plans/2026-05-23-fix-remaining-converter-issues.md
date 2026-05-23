# Fix Remaining Converter Issues — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 8 remaining issues from the sql-java-comparison-report.md: C-PY-03, C-PY-04 (partial), C-PY-07, C-PY-08, C-PY-09, M-PY-02, M-PY-04, M-PY-05.

**Architecture:** All fixes target `converter/flux_gauss.py` (single-file converter). Each fix modifies specific functions. Tasks are ordered by dependency: simpler bug fixes first, then structural additions. Tasks 1-4 are independent and can be parallelized. Tasks 5-7 depend on no prior tasks. Task 8 (C-PY-07) is deferred.

**Tech Stack:** Python 3.9+, ogsql binary for AST parsing, Maven for verification.

**Build Command:**
```bash
cd ~/Projects/Desktop_Projects/DB/sp2java/ && rm -rf dest_py && OGSQL_BIN=../ogsql-parser/target/release/ogsql python3 converter/flux_gauss.py --config demo-project/fluxgauss_py.yaml --skip-validate && cd dest_py && mvn compile && mvn test && mvn verify -Pintegration
```

---

## Issue Summary

| Issue | Description | Difficulty | Lines Affected |
|-------|-------------|-----------|---------------|
| M-PY-04 | `pDeletedCount` never set | Easy | 2857 |
| M-PY-05 | Duplicate code block | Easy | 2951 |
| C-PY-08 | GOTO _gotoTarget not reset + wrong break | Easy | 3073-3131 |
| C-PY-09 | State machine missing break | Easy | 3276-3343 |
| M-PY-02 | TRUNCATE ignored | Medium | 2400+, 1769 |
| C-PY-03 | MERGE INTO empty stub | Medium | 3526+, new handler |
| C-PY-04 | TABLE OF → List (complete FORALL) | Medium | 1470, 3754 |
| C-PY-07 | Complex query reconstruction | Deferred | N/A (ogsql limitation) |

---

## Task 1: Fix M-PY-04 — spPurgeLogs OUT Parameter Not Set

**Files:**
- Modify: `converter/flux_gauss.py:2857`

**Root Cause:** In `_generate_loop_goto()` (line 2836), the loop body is `body_stmts[target_idx:source_idx + 1]`. The assignment `p_deleted_count := v_deleted` occurs at an index AFTER `source_idx` (the GOTO statement), so it is never processed.

**Step 1: Add post-loop statement processing**

In `_generate_loop_goto()`, after the `} while(...)` line, add processing for statements after the GOTO source:

```python
# After line 2874 (after "} while (true);" fallback):
# Process statements AFTER the do-while loop body (e.g., final OUT param assignments)
_post_stmts_start = source_idx + 1
for stmt in body_stmts[_post_stmts_start:]:
    if isinstance(stmt, dict):
        _process_statement(stmt, proc, all_packages, dml_counter)
```

**Important:** Skip any Goto statements in the post-loop section (they belong to the loop pattern, not the post-loop code).

**Step 2: Rebuild and verify**

Run build command. Expected: `spPurgeLogs()` now has `pDeletedCount.set(vDeleted)` at the end.

---

## Task 2: Fix M-PY-05 — spGenerateReport Duplicate Code Block

**Files:**
- Modify: `converter/flux_gauss.py:2947-2953`

**Root Cause:** In `_generate_skip_goto()` at lines 2947-2953, the inner `for st, sd in stmt.items()` loop has `continue` on line 2951. In Python, `continue` applies to the INNERMOST loop, so it skips to the next `st, sd` pair — but after the inner loop ends naturally (single-key dict), execution falls through to `_process_statement()` on line 2953, which processes the same Block AGAIN, causing duplication.

**Step 1: Fix the fall-through**

Change lines 2947-2953 to properly skip `_process_statement()` after handling a labeled Block:

```python
for stmt in body_stmts[target_idx:]:
    if isinstance(stmt, dict):
        _handled = False
        for st, sd in stmt.items():
            if st == "Block" and sd.get("label") == label_name:
                _stmt_list_to_java(sd.get("body", []), proc, all_packages, dml_counter, indent=1)
                _handled = True
                break
        if not _handled:
            _process_statement(stmt, proc, all_packages, dml_counter)
```

**Step 2: Rebuild and verify**

Run build command. Expected: `spGenerateReport()` has the IF/ELSE block only ONCE, not twice.

---

## Task 3: Fix C-PY-08 — GOTO _gotoTarget Not Reset Between Loop Iterations

**Files:**
- Modify: `converter/flux_gauss.py:3073-3131`

**Root Cause:** Two bugs in `_process_loop_with_goto_replace()`:

1. `_gotoTarget` is never reset to `null` at the start of each loop iteration. Once set, it persists across ALL iterations.
2. The outer-loop check `if (_gotoTarget != null) break;` exits the outer loop entirely, instead of `continue` to the next iteration.

**Step 1: Add reset after each loop opening**

After lines 3073, 3105, and 3124 (the for/while loop opening lines), add:

```python
if _needs_dispatch:
    proc.java_logic_lines.append(f"    {_goto_target_var} = null;")
```

**Step 2: Change break to continue for outer loop**

At lines 3080, 3115, and 3131, change:

```python
# BEFORE:
proc.java_logic_lines.append(f"if ({_goto_target_var} != null) break;")
# AFTER:
proc.java_logic_lines.append(f"if ({_goto_target_var} != null) continue;")
```

**Important:** The `continue` is correct because the outer for-each loop should skip to the next iteration (next order), not exit entirely. The `_gotoTarget = null` reset at the top ensures the next iteration starts fresh.

**Step 3: Verify the generated code**

Expected `spValidateOrders()` flow:
```java
for (Map<String, Object> orderRec : orderRecList) {
    _gotoTarget = null;  // RESET at start of each iteration
    for (Map<String, Object> creditRec : creditRecList) {
        if (credit < 60) {
            _gotoTarget = "next_order";
            break;
        }
        // ...
    }
    if (_gotoTarget != null) continue;  // SKIP to next order, not exit loop
    updateSpValidateOrders(...);
}
```

**Step 4: Rebuild and verify**

Run build command. Expected: `spValidateOrders()` resets `_gotoTarget` per iteration and uses `continue` for outer loop.

---

## Task 4: Fix C-PY-09 — GOTO State Machine Missing Break

**Files:**
- Modify: `converter/flux_gauss.py:3276-3343`

**Root Cause:** `_case_is_fully_terminated()` (line 3276) only checks the LAST block in a case for terminal statements. If the case has multiple blocks or conditional branches, earlier blocks may not have terminal statements, leading to fall-through.

Additionally, the logic at line 3433 is too conservative — it may skip adding `break;` when `_case_is_fully_terminated()` incorrectly returns `True`.

**Step 1: Simplify the break logic**

The safest fix: ALWAYS add `break;` at the end of each case, and rely on the state machine's `running = false` / `currentState = ...` for control flow. A redundant `break;` is harmless; a missing `break;` causes bugs.

Change lines 3429-3434:

```python
# BEFORE:
already_terminal = last_stripped.startswith("throw ") or last_stripped.startswith("return") or last_stripped == "break;"
if not has_transition and not has_running_false and not already_terminal:
    proc.java_logic_lines.append("            running = false;")
if not already_terminal and not _case_is_fully_terminated(proc, len(proc.java_logic_lines)):
    proc.java_logic_lines.append("            break;")

# AFTER:
already_terminal = last_stripped == "break;"
if not has_transition and not has_running_false and not already_terminal:
    proc.java_logic_lines.append("            running = false;")
# Always add break — redundant break is safe, missing break causes fall-through bugs
if not already_terminal:
    proc.java_logic_lines.append("            break;")
```

This removes the dependency on `_case_is_fully_terminated()` entirely for break insertion. The function can remain as-is for other potential uses.

**Step 2: Rebuild and verify**

Run build command. Expected: `spOrderStateMachine()` has `break;` at the end of every case.

---

## Task 5: Fix M-PY-02 — TRUNCATE Statements Ignored

**Files:**
- Modify: `converter/flux_gauss.py:1769` (add Truncate to DML types)
- Modify: `converter/flux_gauss.py:2400-2429` (add dispatch case)
- New function: `_process_truncate()`

**Root Cause:** TRUNCATE is not in `_DML_TYPES` and has no dispatch case in `_process_statement()`. It falls through to the unhandled TODO path.

**Step 1: Add TRUNCATE to statement dispatch**

In `_process_statement()`, add a case for Truncate between the Delete and Block cases:

```python
elif stmt_type == "Truncate":
    _process_truncate(stmt_data, proc, dml_counter)
```

**Step 2: Implement `_process_truncate()`**

```python
def _process_truncate(trunc_data: dict, proc: ProcedureInfo, dml_counter: dict):
    """Handle TRUNCATE TABLE statements inside procedure bodies."""
    table_name = ""
    if isinstance(trunc_data, dict):
        # AST may have: {"Table": {"name": ["table_name"]}}
        table_info = trunc_data.get("Table", trunc_data)
        if isinstance(table_info, dict) and "name" in table_info:
            parts = table_info["name"]
            if isinstance(parts, list):
                table_name = ".".join(parts)
            else:
                table_name = str(parts)
    if not table_name:
        # Try to extract from raw SQL text if available
        sql_text = trunc_data.get("sql_text", "") if isinstance(trunc_data, dict) else str(trunc_data)
        import re
        m = re.search(r'TRUNCATE\s+TABLE\s+(\w+)', sql_text, re.IGNORECASE)
        if m:
            table_name = m.group(1)
    if table_name:
        mapper_method = _dml_method_name("truncate", proc.proc_name, dml_counter)
        proc.dml_statements.append(DmlStatement(
            sql_type="update",  # TRUNCATE is DDL, use update for mapper method naming
            method_id=mapper_method,
            sql_text=f"TRUNCATE TABLE {table_name}",
        ))
        proc.java_logic_lines.append(f"mapper.{mapper_method}();")
    else:
        proc.java_logic_lines.append(f"// TODO: TRUNCATE statement — table name could not be determined")
        _record_todo("TRUNCATE", proc, "table name extraction failed")
```

**Step 3: Update `_build_mapper_statement()` for TRUNCATE**

In the mapper XML generation, handle TRUNCATE SQL by using `<update>` tag:

The existing logic should handle this since `sql_type="update"` — the SQL text `TRUNCATE TABLE xxx` will be placed in an `<update>` tag which MyBatis can execute.

**Step 4: Rebuild and verify**

Run build command. Expected: `demo17InsertOverwrite()` has a `mapper.truncateDemo17InsertOverwrite()` call BEFORE the INSERT.

---

## Task 6: Fix C-PY-03 — MERGE INTO Empty Stubs

**Files:**
- Modify: `converter/flux_gauss.py:3526+` (add Merge dispatch to `_process_sql_statement()`)
- No new function needed — reuse existing `_process_sql_statement()` flow

**Root Cause:** MERGE statements are classified as "update" by `_detect_sql_type()` but have no handler in `_process_statement()` dispatch. The AST structure is fully parsed by ogsql, and `sql_text` field preserves the original MERGE SQL.

**Key Insight:** MERGE INTO is native OpenGauss/PostgreSQL syntax. MyBatis XML supports any SQL dialect — just put the MERGE SQL in an `<update>` tag. No decomposition needed.

**Approach:** Reconstruct MERGE SQL from AST (via `_reconstruct_sql_from_ast()` or `sql_text` fallback) → parameter conversion → mapper XML `<update>` tag → Java mapper call. Same flow as INSERT/UPDATE/DELETE.

**Step 1: Add Merge dispatch case in `_process_statement()`**

Find the dispatch block (~line 2400-2429) that routes `Select`/`Insert`/`Update`/`Delete`. Add:

```python
elif stmt_type == "Merge":
    _process_sql_statement(stmt_data, proc, all_packages, dml_counter)
```

This routes MERGE through the same `_process_sql_statement()` function that handles other DML. The function already:
1. Calls `_reconstruct_sql_from_ast()` or uses `sql_text` for SQL reconstruction
2. Converts parameters to MyBatis `#{}` syntax
3. Creates `DmlStatement` with `sql_type="update"` (MERGE is classified as update)
4. Generates mapper method call in Java

**Step 2: Verify `_format_sql()` handles MERGE correctly**

Check if `_format_sql()` (SQL pretty-printer) and `_find_top_level_keyword()` already handle MERGE syntax. If MERGE SQL contains FROM clauses, ensure the existing `_find_top_level_keyword()` fix doesn't incorrectly relocate them.

**Step 3: Handle mapper method return type**

MERGE returns affected row count like UPDATE, so the mapper should return `int` (the default for `<update>` tags). No special handling needed.

**Step 4: Rebuild and verify**

Run build command. Expected:
- `demo12InsertMerge()`: has `mapper.updateDemo12InsertMerge(...)` with MERGE SQL in XML
- `demo17MergerStyle()`: has mapper call with full UPSERT MERGE SQL
- `demo18DeleteMerge()`: has mapper call with MERGE DELETE SQL
- Mapper XML contains `<update>` tags with original MERGE INTO syntax

---

## Task 7: Fix C-PY-04 (Complete) — TABLE OF Types → Java List

**Files:**
- Modify: `converter/flux_gauss.py:1470-1547` (add TABLE OF extraction to `extract_procedures()`)
- Modify: `converter/flux_gauss.py:602-665` (add TABLE OF lookup in `sql_type_to_java()`)
- Modify: `converter/flux_gauss.py:2130-2163` (variable type resolution for TABLE OF)

**Root Cause:** Custom type extraction only handles "Record" and "VarrayOf" kinds. TABLE OF declarations (e.g., `TYPE t_emp_ids IS TABLE OF employees.emp_id%TYPE`) are not extracted. Variables of TABLE OF types default to `Map<String, Object>`, making FORALL generation impossible.

**Step 1: Add TABLE OF extraction to `extract_procedures()`**

After the existing "VarrayOf" handling (~line 1547), add:

```python
elif type_kind == "TableOf" or type_kind == "TableType":
    # TABLE OF type: TYPE t_name IS TABLE OF elem_type [INDEX BY ...]
    elem_type_raw = type_data.get("elem_type", {})
    elem_java_type = sql_type_to_java(elem_type_raw)
    # Check if elem_type is a %TYPE reference
    elem_sql_type = type_data.get("elem_sql_type", "")
    if elem_sql_type:
        elem_java_type = sql_type_to_java(elem_sql_type)
    idx_by = type_data.get("index_by", "")
    custom_types[type_name] = {
        "kind": "table",
        "elem_type": elem_java_type,
        "elem_sql_type": elem_sql_type,
        "index_by": idx_by,
    }
```

**Important:** Check what the ogsql parser actually produces for TABLE OF declarations. The AST key may be "TableOf", "TableType", or something else. Read the cached AST JSON at `dest_py/.fluxgauss/ast/demo_project_sql_gauss_insert_all_styles_json.json` to find the exact structure.

If the ogsql parser does NOT produce structured AST for TABLE OF declarations, implement a fallback: scan the SQL source text (like `_recover_constant_declarations()` does) using regex:

```python
# Regex fallback for TABLE OF declarations
re.search(r'TYPE\s+(\w+)\s+IS\s+TABLE\s+OF\s+([\w\.]+)(?:%TYPE)?', sql_source)
```

**Step 2: Resolve TABLE OF variables to `List<ElemType>`**

In variable declaration processing (~line 2135), check if the variable's type name matches a TABLE OF custom type:

```python
# After line 2135: java_type = sql_type_to_java(raw_type)
if raw_type in proc.custom_types:
    ct = proc.custom_types[raw_type]
    if ct.get("kind") == "table":
        elem_type = ct.get("elem_type", "Object")
        java_type = f"java.util.List<{elem_type}>"
```

**Step 3: Update `_process_forall()` for List types**

In `_process_forall()` (~line 3797), handle `List<...>` array types:

```python
# When arr_type starts with "java.util.List<":
if arr_type.startswith("java.util.List<"):
    elem_type = arr_type[len("java.util.List<"):-1]  # Extract inner type
    # Use .get(index_var - 1) for List access
    param_args.append(f'{arr_java}.get({index_var} - 1)')
elif arr_type == "Map<String, Object>":
    _has_map_array = True
else:
    param_args.append(f'{arr_java}.get({index_var} - 1)')
```

Also handle array initialization for TABLE OF constructor calls (e.g., `t_emp_ids(1060, 1061, ...)` → `java.util.Arrays.asList(1060, 1061, ...)`).

**Step 4: Rebuild and verify**

Run build command. Expected: `demo11InsertBulkCollect()` has proper `List<Integer>` arrays and a for-loop with mapper calls.

---

## Task 8: C-PY-07 — Complex Query Reconstruction (DEFERRED)

**Status:** Deferred. Root cause is in the ogsql Rust parser's `json2sql` command, not the Python converter.

**19 TODOs** across these categories:
- CTE (3): demo_24, demo_25, demo_26
- Multi-table JOIN (3): demo_13, demo_14, demo_15
- VALUES clause (3): demo_46
- LATERAL JOIN (2): demo_18, demo_42
- Nested subqueries (2): demo_40, demo_49
- CROSS JOIN (1): demo_16
- generate_series (1): demo_45
- Comprehensive (1): demo_50

**Potential future approaches:**
1. **Fix ogsql parser:** Update the Rust parser's AST-to-SQL reconstruction to handle LATERAL, CTE, VALUES, etc. This is the ideal fix but requires Rust expertise.
2. **Raw SQL fallback:** When `json2sql` fails, extract the original SQL text from the AST's `sql_text` field (if available) and use it directly with parameter conversion. This is a pragmatic workaround.
3. **Python-side reconstruction:** Implement AST-to-SQL reconstruction in Python for specific constructs (CTE, LATERAL, VALUES). High effort, moderate reliability.

**Recommendation:** Defer until ogsql parser is updated, or implement the raw SQL fallback as a short-term improvement.

---

## Verification Checklist

After all tasks, run the full build:

```bash
cd ~/Projects/Desktop_Projects/DB/sp2java/ && \
rm -rf dest_py && \
OGSQL_BIN=../ogsql-parser/target/release/ogsql python3 converter/flux_gauss.py --config demo-project/fluxgauss_py.yaml --skip-validate && \
cd dest_py && \
mvn compile && \
mvn test && \
mvn verify -Pintegration
```

**Expected results:**
- `mvn compile`: BUILD SUCCESS
- `mvn test`: 350+ tests, 0 failures, 0 errors
- `mvn verify -Pintegration`: 343+ tests, 0 failures

**Spot-check generated code:**
- `ProcFiveGotosService.java`: `spPurgeLogs()` has `pDeletedCount.set(vDeleted)`
- `ProcFiveGotosService.java`: `spGenerateReport()` has IF/ELSE only once
- `ProcFiveGotosService.java`: `spValidateOrders()` resets `_gotoTarget = null` per iteration, uses `continue`
- `ProcFiveGotosService.java`: `spOrderStateMachine()` has `break;` in every case
- `InsertStylesService.java`: `demo17InsertOverwrite()` has TRUNCATE + INSERT
- `InsertStylesService.java`: `demo12InsertMerge()` has actual mapper call
- `InsertStylesService.java`: `demo11InsertBulkCollect()` has `List<...>` arrays and for-loop
