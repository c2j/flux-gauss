# GOTO Conversion Support — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement automatic GOTO-to-Java conversion for 5 identified PL/pgSQL patterns (cleanup exit, loop simulation, logic skip, deep nested breakout, state machine) in both the Python converter (`converter/flux_gauss.py`) and Rust converter (`crates/fluxgauss/`).

**Architecture:** Two-phase approach per converter: (1) **CFG analysis** — build a control-flow graph from the PL/pgSQL statement list, collect all labels and GOTO targets, classify each procedure into one of the 5 patterns; (2) **Pattern-specific code generation** — generate Java idioms (try-finally, while/do-while, inverted if-else, extracted methods, while-switch state machine) based on the detected pattern.

**Tech Stack:** Python 3.9+ (flux_gauss.py), Rust (crates/fluxgauss), ogsql-parser AST, MyBatis XML generation

---

## Context & Current State

### What exists now
- **Rust parser** (`lib/ogsql-parser/src/parser/plpgsql.rs`): Already parses `GOTO label` → `PlStatement::Goto { label }`. Labels on blocks/loops are preserved. **BUT** standalone labels before non-control statements (like `<<cleanup>> DELETE ...`) are silently dropped by `attach_label()` (line 2437-2454).
- **Python converter** (`converter/flux_gauss.py:2005-2012`): GOTO triggers `STUB_PROCEDURES` — entire procedure becomes a TODO stub.
- **Rust converter** (`crates/fluxgauss/src/statement.rs:406-408`): GOTO emits `// GOTO {label};` comment.
- **Test SQL**: `demo-project/sql/proc_GOto.sql` has 3 simple GOTO procedures. `demo-project/sql/proc_Five_Gotos.sql` has documented 5 patterns but **all SQL is commented out**.
- **Other files with GOTO**: `demo-project/sql/complex_clearing_pkg.sql` (handle_trade_change, run_clearing).

### The 5 GOTO patterns (from proc_Five_Gotos.sql)

| Pattern | PL/pgSQL Feature | Java Strategy | Detection Heuristic |
|---|---|---|---|
| **A. Cleanup exit** | `<<cleanup>>` at end, multiple `GOTO cleanup` | `try-finally`, early `return` | Label at/near end, all jumps forward, no backward jumps |
| **B. Loop simulation** | Backward `GOTO label` | `while`/`do-while` loop | Backward GOTO exists, label marks loop entry |
| **C. Logic skip** | Forward jump over code section | Invert condition + `if-else` wrapper | Single forward GOTO, source is conditional |
| **D. Deep nested breakout** | `GOTO` crosses loop boundaries | Extract private method + `return`/`continue` | GOTO from inside nested loop to outer scope |
| **E. State machine** | Multi-label multi-GOTO graph | `enum` + `while-switch` | Multiple labels with multi-source GOTOs forming a graph |

---

## Task 1: Uncomment the 5 GOTO test procedures

**Files:**
- Modify: `demo-project/sql/proc_Five_Gotos.sql`

**Step 1: Create uncommented SQL procedures**

Extract the 5 `CREATE OR REPLACE PROCEDURE` blocks from the commented-out SQL in proc_Five_Gotos.sql. Keep the documentation comments but make the SQL itself executable.

The 5 procedures to uncomment:
- `sp_allocate_resource` (Pattern A)
- `sp_purge_logs` (Pattern B)
- `sp_generate_report` (Pattern C)
- `sp_validate_orders` (Pattern D)
- `sp_order_state_machine` (Pattern E)

**Step 2: Verify parser can parse them**

Run: `cd lib/ogsql-parser && cargo run -- parse -j -f ../../demo-project/sql/proc_Five_Gotos.sql`
Expected: Valid JSON AST with all 5 procedures containing `PlStatement::Goto` nodes.

**Step 3: Verify Python converter sees them**

Run: `python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full`
Expected: 5 new procedures detected (currently stubbed due to GOTO).

**Step 4: Commit**

```bash
git add demo-project/sql/proc_Five_Gotos.sql
git commit -m "feat: uncomment 5 GOTO pattern test procedures"
```

---

## Task 2: Enhance Rust parser to preserve standalone labels

**Files:**
- Modify: `lib/ogsql-parser/src/ast/plpgsql.rs` — Add `PlStatement::Label(String)` variant
- Modify: `lib/ogsql-parser/src/parser/plpgsql.rs:2437-2454` — Update `attach_label()` to emit `Label` for non-control statements
- Modify: `lib/ogsql-parser/src/formatter.rs:5098` — Format the new `Label` variant
- Modify: `lib/ogsql-parser/src/ast/visitor.rs` — Visit the new variant
- Test: `lib/ogsql-parser/src/parser/tests.rs` — Add test for standalone labels

**Step 1: Add `Label` variant to `PlStatement` enum**

In `lib/ogsql-parser/src/ast/plpgsql.rs`, add to the `PlStatement` enum (around line 205, before Goto):

```rust
/// Standalone label: `<<label_name>>` before a non-control-flow statement.
/// For labels on control-flow statements (LOOP, WHILE, FOR, Block), the label
/// is stored directly on the statement node instead.
Label {
    name: String,
},
```

**Step 2: Update `attach_label()` in `parser/plpgsql.rs`**

Replace the `_ => {}` branch (line 2454) with:

```rust
_ => {
    // Standalone label before non-control statement: emit as Label + original statement
    if let Some(lbl) = label {
        // Return a Block containing [Label, original_stmt] to preserve order
        // Actually simpler: just set the label on a wrapping Block
        return PlStatement::Block(Spanned::new(PlBlock {
            label: Some(lbl),
            declarations: Vec::new(),
            body: vec![stmt],
            exception_block: None,
            end_label: None,
        }, None));
    }
}
```

Wait — this changes the AST structure. A cleaner approach: emit the `Label` as a separate statement. But `attach_label` currently returns one statement. The cleanest fix is to change the approach in `parse_pl_statement()` to return a `Vec<PlStatement>` when there's a label, OR change `attach_label` to emit a labeled Block wrapper.

**Chosen approach**: Wrap in `Block` with label. This preserves the label without changing the PlStatement signature, and the converter can detect labeled blocks that contain a single non-control statement.

**Step 3: Update formatter**

In `formatter.rs`, the `PlStatement::Label` case doesn't need handling since we're using Block wrapping. But verify the Goto formatter at line 5098 still works.

**Step 4: Write tests**

In `lib/ogsql-parser/src/parser/tests.rs`:

```rust
#[test]
fn test_plpgsql_standalone_label() {
    let sql = "BEGIN <<cleanup>> DELETE FROM t WHERE id = 1; END";
    let stmts = parse_pl_body(sql);
    // Should produce a labeled Block wrapping the DELETE statement
    assert!(stmts.iter().any(|s| matches!(s, PlStatement::Block(b) if b.node.label.as_deref() == Some("cleanup"))));
}
```

**Step 5: Run parser tests**

Run: `cd lib/ogsql-parser && cargo test`
Expected: All existing tests pass + new test passes.

**Step 6: Verify GOTO procedures parse correctly**

Run: `cd lib/ogsql-parser && cargo run -- parse -j -f ../../demo-project/sql/proc_Five_Gotos.sql | head -100`
Expected: Labels like `cleanup`, `purge_loop`, `assemble_report`, `next_order`, `state_*` appear in the AST.

**Step 7: Commit**

```bash
git add lib/ogsql-parser/
git commit -m "feat(ogsql-parser): preserve standalone labels before non-control statements"
```

---

## Task 3: Implement GOTO pattern analysis in Python converter

**Files:**
- Modify: `converter/flux_gauss.py`

This is the core of the feature. Add a new analysis phase between `analyze_procedure()` and code generation that:
1. Detects GOTO statements in the procedure body
2. Builds a label → statement-index map
3. Classifies the GOTO pattern (A-E)
4. Applies pattern-specific Java code generation

### Design: New function `_analyze_goto_patterns()`

```python
def _analyze_goto_patterns(proc: ProcedureInfo) -> dict:
    """
    Analyze GOTO statements in a procedure and classify the pattern.

    Returns:
        {
            "pattern": "A" | "B" | "C" | "D" | "E" | None,
            "labels": {name: stmt_index},
            "gotos": [{label: str, stmt_index: int, source_block_depth: int}],
            "has_backward_goto": bool,
            "has_forward_goto": bool,
            "label_graph": {label: [source_indices]},  # incoming edges
            "nested_goto": bool,  # GOTO crosses block boundary
        }
    """
```

### Pattern Detection Logic

```python
def _classify_goto_pattern(labels, gotos, stmts):
    """
    Classify the GOTO pattern based on the CFG structure.

    Pattern A (Cleanup): Labels near end, all GOTOs forward, no backward jumps
    Pattern B (Loop): Has backward GOTO (target index < source index)
    Pattern C (Logic Skip): Single forward GOTO from conditional, no other GOTOs
    Pattern D (Deep Nested): GOTO from nested block depth > 0 to outer scope
    Pattern E (State Machine): Multiple labels with multiple incoming GOTOs, forming a graph

    Priority: E > D > A > B > C (most specific first)
    """
```

### Code Generation per Pattern

Each pattern gets a dedicated generation function that replaces the default `_process_statement()` GOTO handling:

**Pattern A**: `_generate_cleanup_pattern(proc, analysis)`
- Wrap the pre-cleanup code in `try { ... }`
- Put the label code in `finally { ... }`
- Replace `GOTO cleanup` with early return/break

**Pattern B**: `_generate_loop_pattern(proc, analysis)`
- Generate `do { ... } while (condition)` wrapping the code between label and GOTO
- Extract the backward-GOTO condition as the while condition

**Pattern C**: `_generate_skip_pattern(proc, analysis)`
- Invert the IF condition that guards the GOTO
- Wrap the skipped section in the else branch

**Pattern D**: `_generate_nested_breakout(proc, analysis)`
- Extract the inner loop body into a private helper method
- Replace GOTO with `return true/false`
- Use `if (helperMethod()) { continue; }` in outer loop

**Pattern E**: `_generate_state_machine(proc, analysis)`
- Generate an enum for states
- Generate `while (running) { switch (state) { ... } }` structure
- Each label becomes a `case` in the switch

### Integration Point

Modify `analyze_procedure()` (called at line ~1519) to call `_analyze_goto_patterns()` after the regular statement processing. When a pattern is detected, clear the `java_logic_lines` and regenerate them using the pattern-specific generator. When no pattern matches (unclassifiable GOTO), keep the existing stub behavior.

**Step 1: Add GOTO analysis dataclass**

Add near line 860 (after `ProcedureInfo`):

```python
@dataclass
class GotoAnalysis:
    pattern: Optional[str]  # "A", "B", "C", "D", "E", or None
    labels: dict            # label_name -> statement_index
    gotos: list             # [{label, stmt_index, block_depth}]
    has_backward: bool
    has_forward: bool
    label_graph: dict       # label_name -> [source_stmt_indices]
    cross_block: bool       # any GOTO crosses block boundary
```

**Step 2: Implement `_analyze_goto_patterns()`**

Add new function around line 1750 (before `_process_statement`). This function:
- Walks the procedure body AST recursively
- Records all `PlStatement::Goto` and `PlStatement::Label` (or labeled blocks)
- Computes direction (forward/backward), nesting depth, graph structure
- Calls `_classify_goto_pattern()` to determine the pattern

**Step 3: Implement `_classify_goto_pattern()`**

Decision tree:
1. Multiple labels + multiple GOTOs forming non-trivial graph → **E** (state machine)
2. Any GOTO crosses loop boundary (source in loop, target outside) → **D** (nested breakout)
3. Label at end + all GOTOs forward + code after label is cleanup → **A** (cleanup exit)
4. Any backward GOTO → **B** (loop simulation)
5. Single forward GOTO from conditional → **C** (logic skip)
6. Otherwise → None (unclassifiable, keep stub)

**Step 4: Implement pattern-specific generators**

Add 5 functions:
- `_generate_cleanup_goto(proc, analysis)` — Pattern A
- `_generate_loop_goto(proc, analysis)` — Pattern B
- `_generate_skip_goto(proc, analysis)` — Pattern C
- `_generate_nested_breakout_goto(proc, analysis)` — Pattern D
- `_generate_state_machine_goto(proc, analysis)` — Pattern E

Each function clears `proc.java_logic_lines` and regenerates them.

**Step 5: Integrate into `analyze_procedure()`**

After the regular statement processing loop, add:

```python
if any GOTO in proc.body:
    analysis = _analyze_goto_patterns(proc)
    if analysis.pattern:
        _apply_goto_pattern(proc, analysis)
    else:
        # Keep existing stub behavior
        pass
```

**Step 6: Test with proc_Five_Gotos.sql**

Run: `python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full`
Expected: 5 procedures no longer stubbed, generating pattern-specific Java code.

**Step 7: Verify existing proc_GOto.sql still works**

Run: `python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full`
Expected: Existing 3 procedures in proc_GOto.sql either get pattern-matched or remain as stubs (no regression).

**Step 8: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: implement GOTO pattern analysis and conversion in Python converter"
```

---

## Task 4: Implement GOTO pattern analysis in Rust converter

**Files:**
- Create: `crates/fluxgauss/src/statements/goto.rs` — GOTO analysis + pattern generators
- Modify: `crates/fluxgauss/src/statements/mod.rs` — Register the new module
- Modify: `crates/fluxgauss/src/statement.rs` — Update GOTO handling to call pattern analysis
- Modify: `crates/fluxgauss/src/types.rs` — Add `GotoAnalysis` struct to `ProcedureInfo`
- Modify: `crates/fluxgauss/src/analyze.rs` — Call GOTO analysis during analysis phase

### Structure

The Rust converter mirrors the Python converter's approach:
1. New `goto.rs` module with `analyze_goto_patterns()` and pattern-specific generators
2. Integration into the analysis pipeline
3. Pattern classification logic identical to Python version

**Step 1: Add GotoAnalysis to types.rs**

```rust
pub struct GotoAnalysis {
    pub pattern: Option<GotoPattern>,
    pub labels: HashMap<String, usize>,
    pub gotos: Vec<GotoInfo>,
    pub has_backward: bool,
    pub has_forward: bool,
    pub label_graph: HashMap<String, Vec<usize>>,
    pub cross_block: bool,
}

pub enum GotoPattern {
    CleanupExit,     // A
    LoopSimulation,  // B
    LogicSkip,       // C
    DeepNestedBreak, // D
    StateMachine,    // E
}

pub struct GotoInfo {
    pub label: String,
    pub stmt_index: usize,
    pub block_depth: usize,
}
```

**Step 2: Implement goto.rs**

Port the Python pattern analysis logic:
- `analyze_goto_patterns(body: &[PlStatement]) -> GotoAnalysis`
- `classify_goto_pattern(analysis: &GotoAnalysis) -> Option<GotoPattern>`
- Pattern-specific generators that return `Vec<String>` (Java logic lines)

**Step 3: Integrate into statement.rs**

Replace the simple `// GOTO {label};` with pattern-based generation.

**Step 4: Test**

Run: `cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java && cargo test -p fluxgauss`
Expected: New tests pass.

**Step 5: Commit**

```bash
git add crates/fluxgauss/
git commit -m "feat: implement GOTO pattern analysis and conversion in Rust converter"
```

---

## Task 5: Integration testing and verification

**Files:**
- Verify generated output for all 5 patterns

**Step 1: Run full conversion**

Run: `python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full`

**Step 2: Verify generated Java files**

Check `dest/src/main/java/com/example/demo/service/` for:
- `ProcFiveGotosService.java` — 5 methods with pattern-specific implementations
- No stub markers (`// TODO: Auto-generated stub`) on the 5 GOTO procedures

**Step 3: Compile check**

Run: `cd dest && mvn compile`
Expected: BUILD SUCCESS (no compilation errors)

**Step 4: Verify Rust converter output**

Run: `cargo run -p fluxgauss -- -c demo-project/fluxgauss.yaml`
Expected: Similar output to Python converter.

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete GOTO conversion support for 5 patterns (Python + Rust)"
```

---

## Implementation Notes

### Key Design Decisions

1. **Block-wrapping for labels in Rust parser**: Rather than adding a `PlStatement::Label` variant and changing the statement list structure, we wrap labeled non-control statements in a `PlStatement::Block { label: Some("cleanup"), body: [original_stmt] }`. This is backwards-compatible and doesn't require changing the visitor/formatter pattern matching in many places.

2. **Post-hoc analysis approach**: Rather than trying to detect GOTO patterns during the single-pass statement processing, we do a separate analysis pass after all statements are processed. This gives us the full picture of the control flow before making generation decisions.

3. **Pattern priority**: When a procedure could match multiple patterns (e.g., a state machine that also has a cleanup section), we use the most specific pattern first (E > D > A > B > C).

4. **Fallback to stub**: If the GOTO pattern cannot be classified, the procedure remains a stub. This is safe — partial conversion is better than broken conversion.

### Risk Areas

- **Pattern A with EXCEPTION blocks**: The `try-finally` wrapping must not conflict with existing exception block handling in `_build_service_method()`.
- **Pattern D method extraction**: Extracting private methods requires generating additional methods in the Service class, which affects `_write_service_class()`.
- **Pattern E enum generation**: State enum must be generated as an inner class in the Service class.
- **Label scope**: PL/pgSQL labels have procedure-level scope. The converter must not confuse labels in different procedures.
- **Mixed patterns**: A procedure might contain multiple GOTO patterns (e.g., a loop simulation inside a state machine). The classifier must handle this by selecting the dominant pattern.

### Testing Checklist

- [ ] `sp_allocate_resource` (Pattern A) generates try-finally
- [ ] `sp_purge_logs` (Pattern B) generates do-while loop
- [ ] `sp_generate_report` (Pattern C) generates inverted if-else
- [ ] `sp_validate_orders` (Pattern D) generates extracted method
- [ ] `sp_order_state_machine` (Pattern E) generates enum + while-switch
- [ ] Existing `proc_GOto.sql` procedures don't regress
- [ ] `complex_clearing_pkg.sql` procedures don't regress
- [ ] `dest/` compiles with `mvn compile`
