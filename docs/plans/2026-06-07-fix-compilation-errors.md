# Fix Compilation Errors: Same-Package Variable Reference, SUBSTR Verbosity, INSTR Type Mismatch

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 6 compilation errors caused by 4 root bugs across both Python and Rust engines.

**Architecture:** The converter has dual engines (Python ~15200 lines, Rust ~33 files). Both engines have identical bugs in three areas: (1) multi-part ColumnRef doesn't detect same-package variable references, (2) SUBSTR generates unparenthesized s_expr causing Math.min type errors, (3) SUBSTR/INSTR don't cast int→Long for Long-typed variables. Fixes must be applied to both engines and verified with compilation.

**Tech Stack:** Python 3.9+, Rust 1.80+, Java 17+ (mvn compile verification), ogsql-parser binary

---

## Test SQL File

Already exists at `demo-project/sql/pkg_bug_test.sql` with two procedures that trigger all bugs.

**Verification config** at `/var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/fluxgauss_bug_test.yaml`

**Run command (Python):**
```bash
OGSQL_BIN=../ogsql-parser/target/release/ogsql python3 converter/flux_gauss.py -c /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/fluxgauss_bug_test.yaml --full
```

**Compile command:**
```bash
cd /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/dest_bug_test && mvn compile
```

**Expected:** 0 compilation errors.

---

## Root Cause → Bug → Error Mapping

| Root Cause | Bugs | Compilation Errors |
|---|---|---|
| RC1: Multi-part ColumnRef doesn't check if `parts[0]` is current package name | `pkg_bug_test.out_err_msg` → `pkgBugTest.get("out_err_msg")` instead of `this.outErrMsg` | #1: `找不到符号 pkgBugTest` |
| RC2: SUBSTR's `s_expr` has no parentheses when containing `+` operator | `Math.min("str" + expr.length(), ...)` → Java parses `.length()` on last operand only | #2-3: `Math.min(String, int) 不匹配` |
| RC3: SUBSTR/INSTR return `int` but no `(long)` cast when assigned to `Long` variable | `vDotPos = indexOf(".") + 1` → int assigned to Long | #4: `int 无法转换为 Long` |
| RC4: SUBSTR uses Long variable as `substring(int, int)` arg without `(int)` cast | `Math.min(len, vDotPos - 1)` → Long can't pass to substring(int, int) | #5-6: `long 转 int 可能有损失` |

---

### Task 1: Fix Python RC1 — Same-package variable reference in multi-part ColumnRef

**Files:**
- Modify: `converter/flux_gauss.py:8808-8836` (the `len(parts) >= 2` block in ColumnRef handler)

**Step 1: Add same-package check before composite field access**

In `_expr_to_java()`, at line 8810, BEFORE the existing `var_name_raw = parts[0]` logic, insert a check:

```python
# Check if this is a same-package variable reference (e.g. PKG_NAME.var_name)
if len(parts) == 2 and proc is not None:
    _pkg_candidate = parts[0]
    _field_candidate = parts[-1]
    if _pkg_candidate.upper() == proc.package.upper() and _field_candidate in _PACKAGE_VARIABLES:
        return f"this.{snake_to_camel(_field_candidate)}"
```

This must be inserted at line 8810 (after `# Multi-part ColumnRef: composite/ROWTYPE/RECORD field access` comment, before `var_name_raw = parts[0]`).

**Step 2: Verify Python engine compiles test case**

Run converter + mvn compile. Error #1 should be gone.

---

### Task 2: Fix Rust RC1 — Same-package variable reference in `resolve_column_ref`

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs:452-474` (the `name.contains('.')` block)

**Step 1: Add same-package check in Rust `resolve_column_ref`**

At line 452, in the `name.contains('.')` branch, BEFORE the existing `var_name = snake_to_camel(parts[0])` line, add:

```rust
// Check if this is a same-package variable reference (e.g. PKG_NAME.var_name)
if parts.len() == 2 {
    let pkg_candidate = parts[0];
    let field_candidate = parts[1];
    let pkg_matches = pkg_candidate.to_lowercase().replace("_", "") == proc.package.to_lowercase().replace("_", "");
    if pkg_matches {
        // Check if field is a package variable
        let field_lower = field_candidate.to_lowercase();
        let field_matches_pkg_var = proc.package_vars.keys().any(|k| {
            k.to_lowercase() == field_lower || k.to_lowercase().replace("_", "") == field_lower.replace("_", "")
        });
        if field_matches_pkg_var {
            return format!("this.{}", crate::naming::snake_to_camel(field_candidate));
        }
    }
}
```

**Step 2: Verify Rust engine compiles test case**

```bash
cd crates/fluxgauss && cargo build 2>&1
```

---

### Task 3: Fix Python RC2+RC4 — SUBSTR parentheses and int/Long casting

**Files:**
- Modify: `converter/flux_gauss.py:7772-7787` (`_sf_substr()` function)

**Step 1: Rewrite `_sf_substr()` to add parentheses and int casts**

Replace the entire `_sf_substr()` function (lines 7772-7787) with:

```python
def _sf_substr(val, proc, _expr_to_java_fn):
    """SUBSTR/SUBSTRING: SQL 1-based → Java 0-based substring."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    s = args_java[0] if len(args_java) > 0 else '""'
    s_expr = s if (s.startswith('"') or s.startswith("'")) else f"String.valueOf({s})"
    # Wrap s_expr in parentheses if it contains operators that affect precedence
    needs_parens = any(op in s_expr for op in (" + ", " - ", " * ", " / "))
    if needs_parens:
        s_expr = f"({s_expr})"
    if len(args_java) >= 3:
        start = args_java[1]
        length = args_java[2]
        # Cast start/length to int if they might be Long
        start_int = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
        length_int = f"(int)({length})" if _might_be_long(length, proc) else f"({length})"
        _clamped_start = f"Math.max(0, {start_int} - 1)"
        _e = f"Math.min({s_expr}.length(), {_clamped_start} + {length_int})"
        return f"{s_expr}.substring({_clamped_start}, {_e})"
    elif len(args_java) == 2:
        start = args_java[1]
        start_int = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
        return f"{s_expr}.substring(Math.max(0, {start_int} - 1))"
    return f"{s_expr}"
```

**Step 2: Add helper function `_might_be_long()` near `_sf_substr()`**

```python
def _might_be_long(expr: str, proc) -> bool:
    """Check if an expression might evaluate to Long type."""
    if not proc:
        return False
    stripped = expr.strip()
    # Direct variable lookup
    for var_name, var_type in proc.local_vars.items():
        if snake_to_camel(var_name) == stripped and var_type in ("Long", "long", "java.math.BigDecimal"):
            return True
    # Arithmetic expression with Long operand
    if any(op in stripped for op in (" + ", " - ", " * ", " / ")):
        # Check if any operand is Long
        for var_name, var_type in proc.local_vars.items():
            camel = snake_to_camel(var_name)
            if camel in stripped and var_type in ("Long", "long"):
                return True
    return False
```

**Step 3: Verify — errors #2, #3, #5, #6 should be gone**

---

### Task 4: Fix Rust RC2+RC4 — SUBSTR parentheses and int/Long casting

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs:1247-1259` (SUBSTR in `special_function_to_java`)
- Modify: `crates/fluxgauss/src/expr.rs:956-967` (SUBSTRING in FunctionCall handler)

**Step 1: Fix SUBSTR in `special_function_to_java` (lines 1247-1259)**

Replace lines 1249-1258 with:

```rust
"substring" | "substr" => {
    let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
    if jargs.len() >= 3 {
        let s_raw = &jargs[0];
        let needs_parens = s_raw.contains(" + ") || s_raw.contains(" - ") || s_raw.contains(" * ") || s_raw.contains(" / ");
        let s = if s_raw.starts_with('"') || s_raw.starts_with('\'') {
            s_raw.clone()
        } else if needs_parens {
            format!("(String.valueOf({}))", s_raw)
        } else {
            format!("String.valueOf({})", s_raw)
        };
        let start = &jargs[1];
        let len = &jargs[2];
        let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
        let len_cast = if might_be_long(len, proc) { format!("(int)({})", len) } else { format!("({})", len) };
        format!("{}.substring(Math.max(0, {} - 1), Math.min({}.length(), Math.max(0, {} - 1) + {}))", s, start_cast, s, start_cast, len_cast)
    } else if jargs.len() >= 2 {
        let s_raw = &jargs[0];
        let needs_parens = s_raw.contains(" + ") || s_raw.contains(" - ") || s_raw.contains(" * ") || s_raw.contains(" / ");
        let s = if s_raw.starts_with('"') || s_raw.starts_with('\'') {
            s_raw.clone()
        } else if needs_parens {
            format!("(String.valueOf({}))", s_raw)
        } else {
            format!("String.valueOf({})", s_raw)
        };
        let start = &jargs[1];
        let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
        format!("{}.substring(Math.max(0, {} - 1))", s, start_cast)
    } else {
        jargs.first().cloned().unwrap_or_else(|| "null".into())
    }
}
```

**Step 2: Fix SUBSTRING in FunctionCall handler (lines 956-967)**

Apply the same fix pattern as Step 1, replacing the `"SUBSTRING"` match arm.

**Step 3: Add `might_be_long()` helper function in expr.rs**

```rust
fn might_be_long(expr: &str, proc: &ProcedureInfo) -> bool {
    let stripped = expr.trim();
    for (var_name, var_type) in &proc.local_vars {
        if crate::naming::snake_to_camel(var_name) == stripped {
            return var_type == "Long" || var_type == "long";
        }
    }
    // Check for arithmetic with Long operand
    if stripped.contains(" + ") || stripped.contains(" - ") || stripped.contains(" * ") || stripped.contains(" / ") {
        for (var_name, var_type) in &proc.local_vars {
            let camel = crate::naming::snake_to_camel(var_name);
            if stripped.contains(&camel) && (var_type == "Long" || var_type == "long") {
                return true;
            }
        }
    }
    false
}
```

**Step 4: Verify Rust builds**

```bash
cd crates/fluxgauss && cargo build 2>&1
```

---

### Task 5: Fix Python RC3 — INSTR int→Long assignment

**Files:**
- Modify: `converter/flux_gauss.py:7679` (INSTR in SQL_FUNCTION_MAP)

**Step 1: Move INSTR from SQL_FUNCTION_MAP template to `_handle_function()` for proper type-aware handling**

Change line 7679 from:
```python
"instr": "__EXPR__String.valueOf({args0}).indexOf({args1}) + 1",
```
to:
```python
"instr": "__HANDLER__",
```

Then add an INSTR handler in `_handle_function()` (around line 8142, after the `encode` handler):

```python
elif func_name == "instr":
    if len(args_java) >= 2:
        s = args_java[0]
        substr_expr = args_java[1]
        s_expr = s if (s.startswith('"') or s.startswith("'")) else f"String.valueOf({s})"
        sub_expr = substr_expr if (substr_expr.startswith('"') or substr_expr.startswith("'")) else f"String.valueOf({substr_expr})"
        result = f"{s_expr}.indexOf({sub_expr}) + 1"
        # Handle 3-arg form: INSTR(str, substr, start)
        if len(args_java) >= 3:
            start = args_java[2]
            start_cast = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
            result = f"{s_expr}.indexOf({sub_expr}, Math.max(0, {start_cast} - 1)) + 1"
        return result
    return "0"
```

**Step 2: Fix assignment-level type coercion for Long targets**

In `_emit_assignment()` (around line 4520-4590), when the target variable is `Long` and the expression contains `indexOf`, add `(long)` cast.

Find the existing coercion logic and ensure that when `var_type == "Long"` and the expression is an `int`-returning operation (like `indexOf` + 1), it wraps with `(long)`.

Look for the section that handles Long type coercion and add:
```python
if target_var_type == "Long" and ".indexOf(" in expr and "long" not in expr.lower():
    expr = f"(long)({expr})"
```

**Step 3: Verify — error #4 should be gone**

---

### Task 6: Fix Rust RC3 — INSTR int→Long assignment

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs:1155` (INSTR in function handler)
- Modify: `crates/fluxgauss/src/expr.rs:113-221` (`coerce_for_type` function)

**Step 1: Enhance INSTR to support 3-arg form**

Replace line 1155:
```rust
"INSTR" if jargs.len() >= 2 => format!("{}.indexOf({}) + 1", jargs[0], jargs[1]),
```
with:
```rust
"INSTR" if jargs.len() >= 3 => {
    let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') { jargs[0].clone() } else { format!("String.valueOf({})", jargs[0]) };
    let sub = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') { jargs[1].clone() } else { format!("String.valueOf({})", jargs[1]) };
    let start = &jargs[2];
    let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
    format!("{}.indexOf({}, Math.max(0, {} - 1)) + 1", s, sub, start_cast)
}
"INSTR" if jargs.len() >= 2 => {
    let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') { jargs[0].clone() } else { format!("String.valueOf({})", jargs[0]) };
    let sub = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') { jargs[1].clone() } else { format!("String.valueOf({})", jargs[1]) };
    format!("{}.indexOf({}) + 1", s, sub)
}
```

**Step 2: Add Long coercion for indexOf expressions in `coerce_for_type`**

In the `coerce_for_type` function (line 113), add a case for Long targets with indexOf:

```rust
Some(t) if (t == "Long" || t == "long") && trimmed.contains(".indexOf(") && !trimmed.contains("(long)") => {
    format!("(long)({})", trimmed)
}
```

This should be added before the catch-all `_ => expr.to_string()` at line 219.

**Step 3: Verify Rust builds**

```bash
cd crates/fluxgauss && cargo build 2>&1
```

---

### Task 7: Full verification — Python + Rust + mvn compile

**Step 1: Run Python engine on full demo project**

```bash
rm -rf /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/dest_bug_test
OGSQL_BIN=../ogsql-parser/target/release/ogsql python3 converter/flux_gauss.py -c /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/fluxgauss_bug_test.yaml --full
cd /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/dest_bug_test && mvn compile
```

Expected: `BUILD SUCCESS`

**Step 2: Run Python engine on full demo project (existing config)**

```bash
rm -rf dest_py
OGSQL_BIN=../ogsql-parser/target/release/ogsql python3 converter/flux_gauss.py -c demo-project/fluxgauss_py.yaml --full
cd dest_py && mvn compile
```

Expected: No new regressions (same error count or fewer).

**Step 3: Run Rust engine on full demo project**

```bash
cd crates/fluxgauss && cargo build --release 2>&1
```

Expected: Compiles without errors.

**Step 4: Run Rust engine on test config**

```bash
./crates/fluxgauss/target/release/fluxgauss --config /var/folders/xh/8xyzggmj4jg02gnjyxwwbnb00000gn/T/opencode/fluxgauss_bug_test.yaml --full
```

Then compile the output.

**Step 5: Commit**

```bash
git add converter/flux_gauss.py crates/fluxgauss/src/expr.rs demo-project/sql/pkg_bug_test.sql
git commit -m "fix: same-package variable ref, SUBSTR verbosity, INSTR type mismatch (both engines)"
```

---

## Key Files Reference

| What | Python Location | Rust Location |
|---|---|---|
| ColumnRef resolution | `flux_gauss.py:8779-8882` | `expr.rs:438-505` |
| SUBSTR (SpecialFunction) | `flux_gauss.py:7772-7787` (`_sf_substr`) | `expr.rs:1247-1259` |
| SUBSTR/SUBSTRING (FunctionCall) | `flux_gauss.py:7666-7668` (SQL_FUNCTION_MAP) | `expr.rs:956-967` |
| INSTR | `flux_gauss.py:7679` (SQL_FUNCTION_MAP) | `expr.rs:1155` |
| Type coercion on assignment | `flux_gauss.py:4520-4590` (`_emit_assignment`) | `expr.rs:113-221` (`coerce_for_type`) |
| Package variables dict | `flux_gauss.py:524` (`_PACKAGE_VARIABLES`) | `types.rs` (package_vars field) |
