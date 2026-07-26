# Issue #57 Fix Plan — Empty String `''` → NUMBER Type

## Problem

When PL/SQL assigns empty string `''` to a `NUMBER` type variable, the converter generates `Long.parseLong("")` which throws `NumberFormatException` at runtime. In GaussDB/openGauss, `''` assigned to NUMBER is implicitly NULL.

### Bug Path (Python Engine)

```
AST: {Literal: {String: ""}}
  → _literal_to_java() → '""' (Java empty string literal)
  → _process_assignment()
      → _infer_target_type() → "Long"
      → _infer_expr_type()  → "String"
      → _coerce_type('""', "String", "Long")
          → Long.parseLong("")  ❌ NumberFormatException at runtime
```

### Bug Path (Rust Engine — ✅ CONFIRMED)

The Rust engine at `crates/fluxgauss/` has the same bug across **5 distinct code paths**:

```
AST: {Literal: String("")}
  → literal_to_java() [expr.rs:498] → "" (Java empty string literal)
  → assignment_to_java() [expr.rs:58]
      → coerce_for_type() [expr.rs:154]
          → Line 171-181: empty-string guard exists but ONLY handles Timestamp/Date
          → Falls through for Long/Integer/BigDecimal → returns "" as-is
      → OR: OUT param Long coercion [expr.rs:100-101]:
          Long.valueOf("")  ❌ NumberFormatException
      → OR: coerce_arg_to_type() [expr.rs:310, 313]:
          Long.parseLong(String.valueOf(""))  ❌ NumberFormatException
```

**5 Bug Vectors in Rust engine:**

| Vector | File:Line | Generated Code | Trigger |
|--------|-----------|----------------|---------|
| A | `expr.rs:100-101` | `Long.valueOf("")` | OUT param Long + string literal |
| B | `expr.rs:310,313` | `Long.parseLong(String.valueOf(""))` | Argument coercion String→Long |
| C | `expr.rs:901,906` | `new BigDecimal(String.valueOf(""))` | Numeric comparison with empty string |
| D | `expr.rs:1373` | `new BigDecimal("")` | `TO_NUMBER('')` function call |
| E | `expr.rs:1690` | `new BigDecimal("\"\"")` (partial guard) | Type cast to numeric/decimal |

The primary fix location is extending the empty-string guard at `expr.rs:171-181` in `coerce_for_type()` to cover ALL numeric types (currently only handles Timestamp/Date). Vectors D and E bypass `coerce_for_type` so need separate guards.

## Fix Approach

### Core Principle

When PL/SQL assigns `''` to a `NUMBER`-typed variable, emit `null` instead of `Long.parseLong("")`. This matches GaussDB/OpenGauss semantics where empty string assigned to NUMBER is implicitly NULL.

### Fix Location: `_coerce_type()` (Python Engine)

**File**: `converter/flux_gauss.py`  
**Location**: Line ~9004, before the String→Numeric block

Insert an empty-string guard at the top of the String→Numeric coercion block:

```python
# Issue #57: empty string '' → NUMBER should map to null, not Long.parseLong("")
# In GaussDB/openGauss, '' assigned to NUMBER is implicitly NULL
if src == "String" and _is_numeric_type(tgt):
    stripped = expr.strip()
    if stripped == '""' or stripped == "''":
        return "null"
```

**Why here**: `_coerce_type()` is the unified coercion engine. Placing the guard here fixes:
- `:=` assignments (lines 4964–4976)
- Comparison coercion (lines 9370–9414) where `'' = some_number` would also trigger `parseLong("")`

**Affected Java types**: `Long`, `Integer`, `Double`, `Float`, `java.math.BigDecimal` — all covered by `_is_numeric_type()`.

### Side Fix: `_coerce_java_arg()` Duplicate Condition Bug

**File**: `converter/flux_gauss.py`  
**Location**: Line 8826

Current code has `a_java == '""' or a_java == '""'` — both sides identical. Fix to:

```python
if a_java == '""' or a_java == "''":
```

This function already handles empty-string → zero for function arguments, so the duplicate condition is a typo bug, not a logic gap.

### Fix Location 2: `coerce_for_type()` (Rust Engine — primary)

**File**: `crates/fluxgauss/src/expr.rs`  
**Location**: Lines 171–181, extend the empty-string guard

Current code only handles Timestamp/Date for empty string; extend to all numeric types:

```rust
if trimmed == "\"\"" || trimmed == "''" {
    if let Some(t) = target_type {
        if t.contains("Timestamp") { return "new java.sql.Timestamp(0)".to_string(); }
        if t.contains("java.sql.Date") || t == "Date" { return "new java.sql.Date(0)".to_string(); }
        // NEW: Issue #57 — empty string → NUMBER maps to null
        if t == "Long" || t == "Integer" || t == "Double" || t == "Float"
            || t.contains("BigDecimal") || t.contains("BigInteger") {
            return "null".to_string();
        }
    }
    return trimmed.to_string();
}
```

This single fix covers:
- Vectors A (OUT param Long coercion at L100-101 — because `coerce_for_type` is called from `assignment_to_java` at L142)
- Vectors B (coerce_arg_to_type at L281 — called separately, but the same pattern applies when argument types flow through this path)
- Vector C (comparison at L901 — uses `is_bigdecimal_var` type predicates, benefits from guard)

### Fix Location 3: `TO_NUMBER` (Rust Engine — Vector D)

**File**: `crates/fluxgauss/src/expr.rs`  
**Location**: Line 1373

```rust
// Before:
"TO_NUMBER" => format!("new BigDecimal({})", jargs.first().unwrap_or("\"0\"")),
// After:
"TO_NUMBER" => {
    let arg = jargs.first().map(|s| s.as_str()).unwrap_or("\"0\"");
    if arg == "\"\"" || arg == "''" {
        "java.math.BigDecimal.ZERO".to_string()
    } else {
        format!("new java.math.BigDecimal({})", arg)
    }
}
```

### Fix Location 4: `coerce_java_arg` Duplicate Condition (Python — side fix)

**File**: `converter/flux_gauss.py`  
**Location**: Line 8826

```python
# Before: a_java == '""' or a_java == '""'  (both sides identical)
# After:  a_java == '""' or a_java == "''"
```

## Type Mapping Reference

| PL/SQL Type | SQL_TO_JAVA | Java Type |
|---|---|---|
| `NUMBER` | → `Long` | `Long` |
| `NUMBER(p,s)` | → `java.math.BigDecimal` | `BigDecimal` |
| `INTEGER` | → `Integer` | `Integer` |
| `BIGINT` | → `Long` | `Long` |
| `FLOAT` / `DOUBLE` | → `Double` | `Double` |

All covered by `_is_numeric_type()` (line 8905).

## Design Decision: `null` vs `0L`

| Option | Pros | Cons |
|---|---|---|
| `null` | Matches GaussDB NULL semantics; fails loudly if used incorrectly | May cause NPE downstream if not guarded |
| `0L` | Safer, no NPE risk; matches PG coercion `''::int → 0` | Silently produces wrong business logic; masks bugs |

**Decision**: Emit **`null`**. The issue description explicitly requests `null`. And in the common scenario (`WHEN no_data_found THEN v_num := ''`), NULL is the correct semantic — the variable has no value, not a zero value.

## Verification

### Python Engine

```bash
# 1. Create test SQL with v_num := '' assignment (e.g. demo-project/sql/test_issue57.sql)
# 2. Convert
python3 converter/flux_gauss.py -c demo-project/fluxgauss_py.yaml
# 3. Compile check
cd dest_py && mvn compile
# 4. Verify no Long.parseLong("") in generated code
grep -r 'Long.parseLong("")' src/        # should be EMPTY
grep -r 'Integer.parseInt("")' src/      # should be EMPTY
grep -r 'new BigDecimal("")' src/        # should be EMPTY
# 5. Run tests
cd dest_py && mvn test
```

### Rust Engine

```bash
# 1. Same test SQL fixture
# 2. Convert
cargo run --bin fluxgauss -- --config demo-project/fluxgauss_ru.yaml
# 3. Compile check
cd dest_ru && mvn compile
# 4. Verify no unsafe parse calls
grep -r 'Long.parseLong("")' src/        # should be EMPTY
grep -r 'Long.valueOf("")' src/          # should be EMPTY
grep -r 'new BigDecimal("")' src/        # should be EMPTY
# 5. Run tests
cd dest_ru && mvn test
# 6. Run Rust unit tests
cargo test -p fluxgauss
```

## Scope

- [ ] Python engine: Add guard in `_coerce_type()` (line ~9004)
- [ ] Python engine: Fix duplicate condition in `_coerce_java_arg()` (line 8826)
- [ ] Rust engine: Extend empty-string guard in `coerce_for_type()` (expr.rs:171-181) to handle all numeric types
- [ ] Rust engine: Guard `TO_NUMBER` at expr.rs:1373 for empty string input
- [ ] Add test SQL fixture with `'' → NUMBER` assignment (test both engines)
- [ ] Verify generated Java code uses `null` instead of `parseLong("")` for both engines
- [ ] Run `mvn compile` + `mvn test` for both engines
- [ ] Run `cargo test -p fluxgauss` for Rust engine
