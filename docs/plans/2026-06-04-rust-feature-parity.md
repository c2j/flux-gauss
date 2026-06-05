# Rust Converter Feature Parity — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close all ~40 feature gaps between the Rust converter (`crates/fluxgauss/`) and the Python reference converter (`converter/flux_gauss.py`), so both engines produce equivalent output quality.

**Architecture:** The Rust converter shares the same 3-phase pipeline (Validate → Parse/Analyze → Generate) as Python. Gaps fall into 4 categories: (1) missing statement handlers (FORALL, SAVEPOINT, etc.), (2) missing expression/function conversions (date/time, string, sequence), (3) missing code generation features (exception blocks, RECORD inner classes, comment injection), and (4) incomplete SQL post-processing. Each task targets one self-contained module area.

**Tech Stack:** Rust (crates/fluxgauss/), Python 3.9+ (converter/flux_gauss.py as reference), ogsql-parser AST, MyBatis XML

---

## Current Gap Inventory

### P0 — Critical Statement Handlers (4 items)
1. FORALL batch processing → MyBatis `<foreach>`
2. GET DIAGNOSTICS row_count → _sqlRowCount assignment
3. SAVEPOINT / ROLLBACK TO SAVEPOINT / RELEASE SAVEPOINT → JDBC code
4. Exception block wrapping → try-catch with WHEN OTHERS

### P1 — Core Expression Conversions (13 items)
5. ADD_MONTHS → `LocalDate.plusMonths()`
6. LAST_DAY → `withDayOfMonth(lengthOfMonth())`
7. NEXT_DAY → `plusWeeks(1)`
8. EXTRACT → temporal field access (getYear, getMonthValue, etc.)
9. AGE → `Period.between()`
10. DATE_TRUNC → `truncatedTo(ChronoUnit)`
11. MONTHS_BETWEEN → `Period.between().toTotalMonths()`
12. DECODE → nested ternary chain
13. TO_CHAR → full SimpleDateFormat/DecimalFormat handling
14. TO_DATE → SimpleDateFormat.parse()
15. TRANSLATE → char mapping with Stream
16. OVERLAY → substring splice
17. POSITION → indexOf + 1

### P2 — Additional Expression Conversions (8 items)
18. NEXTVAL / CURRVAL → mapper stub methods
19. ARRAY_APPEND → `_appendList()` with generic type coercion
20. ARRAY_TO_STRING → stream + Collectors.joining
21. ENCODE → Base64 encoder
22. TO_HEX → Integer.toHexString()
23. INTERVAL → Duration.toMillis()
24. TRIM (special) → BOTH/LEADING/TRAILING direction
25. SQLSTATE → `__SQLSTATE__` variable

### P3 — Code Generation Features (5 items)
26. Exception block wrapping in service methods
27. RECORD inner class generation
28. Comment injection (proportional placement)
29. DBE_SCHEDULER job handling
30. SQL post-processing regex patterns (missing ~30 patterns)

### P4 — Minor / Edge Cases (5 items)
31. RETURN QUERY → mapper call + result collection
32. RETURN NEXT → collection append
33. MOVE cursor → index manipulation
34. CRC32 helper method
35. ARRAY_TO_STRING, JSONB_BUILD_ARRAY, JSONB_SET

---

## Task 1: FORALL Batch Processing

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` — add `process_forall` function
- Modify: `crates/fluxgauss/src/generate/mapper.rs` — add `<foreach>` XML generation for FORALL
- Modify: `crates/fluxgauss/src/types.rs` — add `forall_batch_arrays` field to `ProcedureInfo` if needed

**Reference:** Python `_process_forall()` (converter/flux_gauss.py lines 4098–4265)

**Step 1: Understand the Python implementation**

Read Python converter `_process_forall()` to understand:
- How it detects FORALL ... UPDATE/DELETE patterns
- How it extracts the batch array variable
- How it rewrites SQL with `<foreach>` item iteration variable
- How it generates the MyBatis XML `<foreach>` tag
- How it handles extra parameters (forall_batch_arrays, extra_params)
- Fallback: when batch not possible, generate per-row loop

Key Python patterns:
```python
# FORALL i IN 1..v_count
#   UPDATE t SET col = p_array(i) WHERE id = p_ids(i)
# →
# mapper.updateForall(pArray, pIds)  // uses <foreach> in XML
```

**Step 2: Add FORALL handler in statement.rs**

Replace the current stub (`PlStatement::ForAll(_) => Ok(())`) with a real handler:

```rust
PlStatement::ForAll(forall) => {
    process_forall_stmt(forall, proc, ctx)?;
}
```

The handler should:
1. Extract the range bounds (low..high)
2. Extract the DML statement inside the FORALL body
3. Detect the batch array variable (the subscript variable like `p_array(i)`)
4. Extract element type from the array variable
5. If batch possible: create a single DML with `<foreach>` in mapper XML
6. If not batch possible: generate a loop over individual mapper calls

**Step 3: Add `<foreach>` support in mapper XML generation**

In `generate/mapper.rs`, when the DML statement has `forall_batch` flag, generate:
```xml
<update id="updateForall">
  <foreach collection="list" item="item" separator=";">
    UPDATE t SET col = #{item.col} WHERE id = #{item.id}
  </foreach>
</update>
```

**Step 4: Build and test**

Run: `cargo build --release -p fluxgauss && cargo test -p fluxgauss`

**Step 5: Regenerate and compare**

Run Rust converter and Python converter on SQL files containing FORALL.
Compare generated output.

**Step 6: Commit**

```bash
git add crates/fluxgauss/src/statement.rs crates/fluxgauss/src/generate/mapper.rs crates/fluxgauss/src/types.rs
git commit -m "feat: implement FORALL batch processing with MyBatis foreach"
```

---

## Task 2: GET DIAGNOSTICS row_count

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` — replace `PlStatement::GetDiagnostics(_) => Ok(())` with real handler

**Reference:** Python `_process_get_diagnostics()` (converter/flux_gauss.py lines 2544–2567)

**Step 1: Implement handler**

```rust
PlStatement::GetDiagnostics(diagnostics) => {
    // GET DIAGNOSTICS variable = ROW_COUNT
    // → _sqlRowCount = mapper.updateXxx(...);  (handled by DML tracking)
    // For now, generate: int __ROWCOUNT__ = _sqlRowCount;
    // Python tracks this via _sqlRowCount variable
    if let Some(var_name) = extract_diagnostics_variable(diagnostics) {
        push_logic_line(proc, format!("{} = __ROWCOUNT__;", var_name));
    }
    Ok(())
}
```

Also ensure that DML statements (UPDATE/DELETE) assign their return value to `__ROWCOUNT__`:
```java
__ROWCOUNT__ = mapper.updateXxx(params);
```

**Step 2: Ensure `__ROWCOUNT__` is declared**

In `generate/service.rs`, when any statement references ROWCOUNT, add:
```java
int __ROWCOUNT__ = 0;
```

Check if this is already handled by the existing special variable declaration logic (like `__SQLERRM__`).

**Step 3: Build, test, commit**

---

## Task 3: SAVEPOINT Operations

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` — replace stubs for Savepoint, Rollback (to_savepoint), ReleaseSavepoint

**Reference:** Python SAVEPOINT handling (converter/flux_gauss.py lines 2725–2729, 819–830)

**Step 1: Implement SAVEPOINT handler**

```rust
PlStatement::Savepoint { name } => {
    proc.all_imports.insert("java.sql.Savepoint".into());
    push_logic_line(proc, format!(
        "Savepoint {} = connection.setSavepoint(\"{}\");",
        snake_to_camel(name), name
    ));
    Ok(())
}
```

**Step 2: Implement ROLLBACK TO SAVEPOINT**

In the `Rollback` match arm, check if `to_savepoint` is Some:
```rust
PlStatement::Rollback { to_savepoint, .. } => {
    if let Some(sp_name) = to_savepoint {
        proc.all_imports.insert("org.springframework.transaction.support.TransactionAspectSupport".into());
        push_logic_line(proc, format!(
            "TransactionAspectSupport.currentTransactionStatus().rollbackToSavepoint({});",
            snake_to_camel(sp_name)
        ));
    } else {
        // Full rollback
        push_logic_line(proc, "TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();".into());
    }
    Ok(())
}
```

**Step 3: Implement RELEASE SAVEPOINT**

```rust
PlStatement::ReleaseSavepoint { name } => {
    push_logic_line(proc, format!("// RELEASE SAVEPOINT {} — not needed in Spring managed transaction", name));
    Ok(())
}
```

**Step 4: Add `connection` injection when SAVEPOINT is used**

In `generate/service.rs`, when any method uses SAVEPOINT, add:
- Field: `private final Connection connection;`
- Import: `java.sql.Connection`
- Constructor param

**Step 5: Build, test, commit**

---

## Task 4: Exception Block Wrapping (WHEN OTHERS → try-catch)

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` — improve Block handler for exception sections
- Modify: `crates/fluxgauss/src/generate/service.rs` — generate try-catch wrapping

**Reference:** Python block handling (converter/flux_gauss.py lines 2602–2664, 6104–6234)

**Step 1: Enhance Block handler**

The current Block handler in statement.rs (~line 1905-1921) generates basic try-catch. Enhance to:
1. Detect `handlers` in the block's exception section
2. Map `WHEN OTHERS THEN` → `catch (Exception e)`
3. Map specific conditions → `catch (BusinessException e)` with comment
4. Generate SQLERRM/SQLCODE access as `e.getMessage()` / custom code
5. Preserve handler body logic

**Step 2: Generate proper try-catch in service**

When a block has exception handlers, wrap with:
```java
try {
    // body statements
} catch (Exception e) {
    __SQLERRM__ = e.getMessage();
    __SQLCODE__ = -1;
    // handler body
}
```

For ROLLBACK in exception handler:
```java
} catch (Exception e) {
    TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();
    // handler body
}
```

**Step 3: Build, test, commit**

---

## Task 5: Date/Time Function Conversions

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs` — replace stubs in `function_call_to_java`

**Reference:** Python SQL_FUNCTION_MAP date entries and `_handle_function` (converter/flux_gauss.py)

**Step 1: Implement ADD_MONTHS**

Replace `/* ADD_MONTHS({}, {}) */ null` with:
```rust
"ADD_MONTHS" | "ADD_MONTH" => {
    let arg0 = expr_to_java_impl(args[0], proc);
    let arg1 = expr_to_java_impl(args[1], proc);
    format!("java.time.LocalDate.parse(String.valueOf({})).plusMonths(Long.parseLong(String.valueOf({})))", arg0, arg1)
}
```

**Step 2: Implement LAST_DAY**

Replace `/* LAST_DAY({}) */ null` with:
```rust
"LAST_DAY" => {
    let arg = expr_to_java_impl(args[0], proc);
    format!("java.time.LocalDate.parse(String.valueOf({})).withDayOfMonth(java.time.LocalDate.parse(String.valueOf({})).lengthOfMonth())", arg, arg)
}
```

**Step 3: Implement NEXT_DAY**

```rust
"NEXT_DAY" => {
    let arg = expr_to_java_impl(args[0], proc);
    format!("java.time.LocalDate.parse(String.valueOf({})).plusWeeks(1)", arg)
}
```

**Step 4: Implement EXTRACT**

Add to `special_function_to_java`:
```rust
"EXTRACT" => {
    // EXTRACT(YEAR FROM date_expr) → date.toLocalDate().getYear()
    // Handle: YEAR, MONTH, DAY, HOUR, MINUTE, SECOND
    // arg0 is the field, arg1 is the expression
}
```

Map fields:
- YEAR → `.toLocalDate().getYear()`
- MONTH → `.toLocalDate().getMonthValue()`
- DAY → `.toLocalDate().getDayOfMonth()`
- HOUR → `.toLocalDateTime().getHour()`
- MINUTE → `.toLocalDateTime().getMinute()`
- SECOND → `.toLocalDateTime().getSecond()`

**Step 5: Implement AGE**

```rust
"AGE" => {
    // AGE(timestamp1, timestamp2) → Period.between(...)
    // AGE(timestamp) → Period.between(timestamp.toLocalDate(), LocalDate.now())
}
```

**Step 6: Implement DATE_TRUNC**

```rust
"DATE_TRUNC" => {
    // DATE_TRUNC('day', timestamp) → Timestamp.valueOf(ts.toLocalDateTime().truncatedTo(ChronoUnit.DAYS))
    // Units: day, month, year, hour, minute, second
}
```

**Step 7: Implement MONTHS_BETWEEN**

```rust
"MONTHS_BETWEEN" => {
    let arg0 = expr_to_java_impl(args[0], proc);
    let arg1 = expr_to_java_impl(args[1], proc);
    format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate()).toTotalMonths()", arg1, arg0)
}
```

**Step 8: Build, test, commit**

```bash
git add crates/fluxgauss/src/expr.rs
git commit -m "feat: implement date/time function conversions (ADD_MONTHS, LAST_DAY, NEXT_DAY, EXTRACT, AGE, DATE_TRUNC, MONTHS_BETWEEN)"
```

---

## Task 6: Special Function Conversions (DECODE, TO_CHAR, TO_DATE)

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs` — `function_call_to_java` and `special_function_to_java`

**Reference:** Python SPECIAL_FUNCTION_MAP and `_handle_function`

**Step 1: Implement DECODE**

Add to `special_function_to_java`:
```rust
"DECODE" => {
    // DECODE(expr, val1, result1, val2, result2, ..., default)
    // → (Objects.equals(expr, val1) ? result1 : (Objects.equals(expr, val2) ? result2 : ... : default))
    let expr = expr_to_java_impl(args[0], proc);
    let mut ternary = String::new();
    let mut i = 1;
    while i + 1 < args.len() {
        let val = expr_to_java_impl(args[i], proc);
        let result = expr_to_java_impl(args[i+1], proc);
        ternary.push_str(&format!("(java.util.Objects.equals({}, {}) ? {} : ", expr, val, result));
        i += 2;
    }
    // Last arg is default
    if i < args.len() {
        ternary.push_str(&expr_to_java_impl(args[i], proc));
    } else {
        ternary.push_str("null");
    }
    // Close parens
    for _ in 1..args.len()/2 {
        ternary.push(')');
    }
    ternary
}
```

Also add DECODE handling in `fix_postgresql_syntax` for SQL-level DECODE→CASE conversion.

**Step 2: Improve TO_CHAR**

Replace basic `String.valueOf({})` with full format handling:
```rust
"TO_CHAR" => {
    if args.len() == 2 {
        let value = expr_to_java_impl(args[0], proc);
        let fmt = expr_to_java_impl(args[1], proc);
        // Detect date format patterns
        // → new SimpleDateFormat(format).format(value)
        format!("new java.text.SimpleDateFormat({}).format({})", fmt, value)
    } else {
        format!("String.valueOf({})", expr_to_java_impl(args[0], proc))
    }
}
```

Need to map Oracle date format patterns to Java SimpleDateFormat:
- `YYYY` → `yyyy`, `MM` → `MM`, `DD` → `dd`, etc.

**Step 3: Implement TO_DATE**

```rust
"TO_DATE" => {
    if args.len() >= 2 {
        let value = expr_to_java_impl(args[0], proc);
        let fmt = expr_to_java_impl(args[1], proc);
        format!("java.sql.Date.valueOf(new java.text.SimpleDateFormat({}).parse({}).toInstant().atZone(java.time.ZoneId.systemDefault()).toLocalDate())", fmt, value)
    } else {
        format!("java.sql.Date.valueOf({})", expr_to_java_impl(args[0], proc))
    }
}
```

**Step 4: Build, test, commit**

```bash
git add crates/fluxgauss/src/expr.rs
git commit -m "feat: implement DECODE, TO_CHAR, TO_DATE conversions"
```

---

## Task 7: String Functions (TRANSLATE, OVERLAY, POSITION)

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`

**Reference:** Python `_handle_function` for these functions

**Step 1: Implement TRANSLATE**

```rust
"TRANSLATE" => {
    // TRANSLATE(str, from_chars, to_chars)
    // → str.chars().mapToObj(c -> { int idx = from.indexOf(c); return idx >= 0 ? to.charAt(idx) : c; }).map(String::valueOf).collect(Collectors.joining())
}
```

Add import for `java.util.stream.Collectors`.

**Step 2: Implement OVERLAY**

Add to `special_function_to_java`:
```rust
"OVERLAY" => {
    // OVERLAY(str PLACING repl FROM start FOR length)
    // → str.substring(0, Math.max(0, start - 1)) + repl + str.substring(Math.max(0, start - 1 + length))
}
```

**Step 3: Implement POSITION**

Add to `special_function_to_java`:
```rust
"POSITION" => {
    // POSITION(substr IN str) → str.indexOf(substr) + 1  (1-based)
}
```

**Step 4: Build, test, commit**

---

## Task 8: Sequence and Array Functions

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`
- Modify: `crates/fluxgauss/src/generate/service.rs` — add helper methods if needed

**Step 1: Implement NEXTVAL / CURRVAL**

```rust
"NEXTVAL" => {
    // nextval('sequence_name') → this.nextval("sequence_name")
    // Generate a mapper method: @Select("SELECT nextval(#{name})") Long nextval(@Param("name") String name)
    let arg = expr_to_java_impl(args[0], proc);
    format!("this.nextval({})", arg)
}
```

Similarly for CURRVAL.

**Step 2: Implement ARRAY_APPEND**

Replace stub with real implementation:
```rust
"ARRAY_APPEND" => {
    // Detect list element type, generate proper append
    let list_expr = expr_to_java_impl(args[0], proc);
    let elem_expr = expr_to_java_impl(args[1], proc);
    format!("({}.add({}))", list_expr, elem_expr)
}
```

**Step 3: Implement ARRAY_TO_STRING**

```rust
"ARRAY_TO_STRING" => {
    let arr = expr_to_java_impl(args[0], proc);
    let delim = expr_to_java_impl(args[1], proc);
    format!("({}).stream().map(Object::toString).collect(java.util.stream.Collectors.joining({}))", arr, delim)
}
```

**Step 4: Implement ENCODE**

```rust
"ENCODE" => {
    // Base64 encoding
    let arg = expr_to_java_impl(args[0], proc);
    format!("java.util.Base64.getEncoder().encodeToString(String.valueOf({}).getBytes())", arg)
}
```

**Step 5: Implement TO_HEX**

```rust
"TO_HEX" => {
    let arg = expr_to_java_impl(args[0], proc);
    format!("Integer.toHexString({}).toUpperCase()", arg)
}
```

**Step 6: Implement INTERVAL**

Add to `special_function_to_java`:
```rust
"INTERVAL" => {
    // interval '1 month' → Duration/Period based on unit
    // Generate appropriate java.time conversion
}
```

**Step 7: Implement TRIM (special)**

Add to `special_function_to_java`:
```rust
"TRIM" => {
    // TRIM(BOTH/LEADING/TRAILING chars FROM str)
    // BOTH → str.trim() or replaceAll
    // LEADING → replaceFirst("^[" + chars + "]+", "")
    // TRAILING → replaceFirst("[" + chars + "]+$", "")
}
```

**Step 8: Add SQLSTATE variable**

In `generate/service.rs`, add `__SQLSTATE__` alongside existing `__SQLERRM__` and `__SQLCODE__`:
```java
String __SQLSTATE__ = "";
```

**Step 9: Build, test, commit**

```bash
git add crates/fluxgauss/src/expr.rs crates/fluxgauss/src/generate/service.rs
git commit -m "feat: implement sequence, array, encode, interval, and special string functions"
```

---

## Task 9: DBE_SCHEDULER Job Handling

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs` — enhance PERFORM/ProcedureCall handlers
- Modify: `crates/fluxgauss/src/generate/service.rs` — generate scheduler job patterns

**Reference:** Python DBE_SCHEDULER handling (converter/flux_gauss.py lines 4771–4941)

**Step 1: Detect DBE_SCHEDULER calls**

In `process_procedure_call`, when the target package is `dbe_scheduler`:
- `CREATE_JOB` → track job definition, extract what_name, what_type
- `SET_JOB_ARGUMENT_VALUE` → bind argument to pending job
- `ENABLE` → flush pending job as direct method call

**Step 2: Generate synchronous method call**

Instead of stub comment, generate:
```java
targetService.targetMethod(args);
```

With proper arg coercion based on target procedure parameter types.

**Step 3: Build, test, commit**

---

## Task 10: RETURN QUERY / RETURN NEXT / MOVE Cursor

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

**Step 1: Implement RETURN QUERY**

Replace `PlStatement::ReturnQuery(_) => Ok(())` with:
```rust
PlStatement::ReturnQuery(query) => {
    // RETURN QUERY EXECUTE v_sql → mapper call, add results to collection
    // RETURN QUERY SELECT ... → generate mapper call
    // For set-returning functions, accumulate into a result list
    Ok(())
}
```

Python generates:
```java
List<Map<String, Object>> _returnQueryResult = mapper.selectXxx(params);
result.addAll(_returnQueryResult);
```

**Step 2: Implement RETURN NEXT**

Replace `PlStatement::ReturnNext { expression } => Ok(())` with:
```rust
PlStatement::ReturnNext { expression } => {
    let expr_java = crate::expr::expr_to_java(expression, proc)?;
    push_logic_line(proc, format!("_returnResults.add({});", expr_java));
    Ok(())
}
```

**Step 3: Implement MOVE cursor**

Replace `PlStatement::Move { cursor, .. } => Ok(())` with:
```rust
PlStatement::Move { cursor, .. } => {
    // MOVE NEXT cursor → cursorIdx++
    // MOVE PRIOR cursor → cursorIdx--
    // MOVE FORWARD n cursor → cursorIdx += n
    push_logic_line(proc, "// MOVE cursor — index adjustment");
    Ok(())
}
```

**Step 4: Build, test, commit**

---

## Task 11: RECORD Inner Class Generation

**Files:**
- Modify: `crates/fluxgauss/src/generate/service.rs`

**Reference:** Python custom_types → inner class generation

**Step 1: Generate inner classes for RECORD types**

When a procedure has custom types with `is_record: true`, generate inner static class:
```java
public static class TCoordRec {
    private BigDecimal x;
    private BigDecimal y;
    // getters/setters via Lombok or manual
}
```

**Step 2: Update field access in expression conversion**

When accessing a RECORD field, generate `rec.getX()` instead of `rec.get("x")`.

**Step 3: Build, test, commit**

---

## Task 12: Comment Injection

**Files:**
- Modify: `crates/fluxgauss/src/generate/service.rs`
- Modify: `crates/fluxgauss/src/extract.rs` — ensure comment mapping is complete

**Reference:** Python `_inject_inline_comments()` (converter/flux_gauss.py lines 1778–1935)

**Step 1: Map inline comments to logic lines**

In the service generator, after generating logic lines:
1. Get the procedure's inline_comments list
2. For each comment, determine the target logic line based on SQL line range → Java line range mapping
3. Insert `// {comment_text}` at the appropriate position

**Step 2: Convert comment format**

- `-- single line` → `// single line`
- `/* block comment */` → `// block comment`

**Step 3: Build, test, commit**

---

## Task 13: SQL Post-Processing Regex Patterns

**Files:**
- Modify: `crates/fluxgauss/src/generate/mapper.rs` — `fix_postgresql_syntax` function

**Reference:** Python SQL post-processing (converter/flux_gauss.py lines 5689–5861)

**Step 1: Port missing regex patterns from Python**

Python has ~50 regex patterns, Rust has ~20. Missing patterns include:
- Lost `/` in `CEIL(base num)` → `CEIL(base / num)`
- Lost `/` in `POWER(base num, 2)` → `POWER(base / num, 2)`
- Lost `/` in `MOD(base integer, n)` → `MOD(base::integer, n)`
- Lost `/` in `SUM(...) NUMBER` → `SUM(...) / NUMBER`
- Implicit cast recovery: `IDENTIFIER integer as` → `IDENTIFIER::integer as`
- Column alias correction: `name AS #{paramRef}` → `name` (for simple INTO)
- Spurious date after generate_series
- JSON operator cleanup patterns

For each pattern, add to Rust's `fix_postgresql_syntax`:
```rust
// Lost division in CEIL/FLOOR/TRUNC/ROUND/POWER
sql = regex!(r"(?i)(CEIL|FLOOR|TRUNC|ROUND|POWER)\((\w+)\s+([\w.]+)").replace_all(&sql, "${1}(${2} / ${3}").to_string();
```

**Step 2: Test with real SQL files**

Run converter on demo SQL files and verify SQL corrections.

**Step 3: Commit**

---

## Task 14: Helper Methods (CRC32, etc.)

**Files:**
- Modify: `crates/fluxgauss/src/generate/service.rs` — add helper method generation

**Step 1: Add CRC32 helper**

When CRC32 function is used, generate:
```java
private String _crc32(String input) {
    byte[] bytes = input.getBytes();
    java.util.zip.CRC32 crc = new java.util.zip.CRC32();
    crc.update(bytes);
    return Long.toHexString(crc.getValue());
}
```

**Step 2: Add _appendList helper for ARRAY_APPEND**

```java
private <T> java.util.List<T> _appendList(java.util.List<T> list, T element) {
    list.add(element);
    return list;
}
```

**Step 3: Build, test, commit**

---

## Task 15: Final Verification and Comparison

**Step 1: Full regeneration**

```bash
# Python
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full

# Rust
cargo run --release -p fluxgauss -- -c demo-project/fluxgauss_ru.yaml --full
```

**Step 2: Compare TODO/stub counts**

```bash
echo "Python TODOs:" && grep -rn "TODO\|stub\|STUB" dest/src/main/java/*/service/*.java | wc -l
echo "Rust TODOs:" && grep -rn "TODO\|stub\|STUB" dest_ru/src/main/java/*/service/*.java | wc -l
```

**Step 3: Compare specific output files**

For each SQL file, diff the Python and Rust output to identify remaining differences.

**Step 4: Fix remaining issues iteratively**

**Step 5: Verify compilation**

```bash
cd dest_ru && mvn compile && mvn test
```

**Success criteria:**
- Rust TODO count ≤ Python TODO count
- All generated Java compiles (`mvn compile` exit 0)
- All generated tests pass (`mvn test` exit 0)
- No feature in Python that Rust doesn't handle (excluding edge cases documented as known limitations)
