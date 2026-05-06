# Refactor: flux_gauss.py 去冗余与去重

## TL;DR

> **Quick Summary**: 对 `converter/flux_gauss.py`（7550行）执行代码审计后的重构：删除死代码（P0）、消除重复模式（P1）、整合类型检查与默认值逻辑（P2）。
> 
> **Deliverables**:
> - 删除 4 个从未被调用的函数
> - 提取字符串包装工具函数 `_ensure_string_expr()`，替换 11 处重复
> - 提取 INTO 变量遍历共享逻辑 `_walk_into_targets()`
> - 提取表名遍历共享逻辑 `_walk_table_nodes()`
> - 提取类型匹配工具函数 `_matches_java_type()`
> - 所有改动后 converter 行为完全不变
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Task 1 → Task 5 → Task 6 → Task 7 → Task 8

---

## Context

### Original Request
用户对 `converter/flux_gauss.py`（7550 行、188 个函数、9 个类）进行代码审计，发现存在死代码、重复模式和可整合的类型检查逻辑，要求按 P0/P1/P2 优先级重构。

### Interview Summary
**Key Discussions**:
- P0（死代码）：4 个函数从未被任何代码调用，可安全删除
- P1（关键去重）：字符串包装模式 11 处重复、INTO 变量提取 3 函数共享核心逻辑、表名提取 4 函数共享核心逻辑
- P2（类型链整合）：类型判断链重复 30+ 处、默认值生成 4 函数共享类型分发
- Metis 建议变量名提取 3 函数语义不同，不宜合并 → **已采纳，从范围中移除**

**Research Findings**:
- `_is_string_expr` (L2136) 使用完全不同的判断逻辑，**不是**字符串包装模式的目标
- `_extract_table_name_from_dml` L4495 存在死代码分支（`elif isinstance(val, list)` 永远不可达），但属于 bug 修复范围，不在本次重构范围内
- 项目无自动化测试框架，验证方式：运行 converter + `mvn compile`

### Metis Review
**Identified Gaps** (addressed):
- 字符串包装模式有 2 处变体 → 计划中明确排除
- 变量名提取函数语义不同 → 从 P1 范围中移除
- 默认值函数用途不同，只提取类型分发 → 已采纳
- 需要基线对比（生成文件 diff） → 加入验证步骤
- P0 必须先完成提交，因为行号会变化 → Wave 1 先做 P0

---

## Work Objectives

### Core Objective
在不改变 converter 输出行为的前提下，删除死代码并消除重复模式，降低维护成本。

### Concrete Deliverables
- `converter/flux_gauss.py` 减少约 200-300 行
- 新增 4 个小型工具函数（每个 5-10 行）
- 所有调用点更新为使用新工具函数

### Definition of Done
- [ ] `python3 converter/flux_gauss.py -c fluxgauss.yaml` 执行无报错
- [ ] `cd dest && mvn compile` 编译通过
- [ ] 重构前后生成的 Java 文件完全一致（diff 确认）
- [ ] 不存在任何新增的 `import` 或外部依赖

### Must Have
- 每一步重构后 converter 行为完全不变（输出字节级一致）
- 每个任务独立提交，commit message 清晰
- 删除的函数确认无动态调用风险（getattr / globals 等）

### Must NOT Have (Guardrails)
- ❌ 不合并 `_extract_var_name` / `_extract_var_name_from_expr` / `_extract_name_from_expr`（语义不同）
- ❌ 不合并 `_default_for_type` / `_default_test_value` / `_mock_value_for_column` / `_itest_generate_test_value`（用途不同，只提取共享的类型分发）
- ❌ 不修复 L4495 的死代码分支 bug（超出重构范围）
- ❌ 不添加新依赖或修改 import
- ❌ 不改变任何函数的公开 API 签名
- ❌ 不拆分文件结构（保持单文件）
- ❌ 不触碰巨型函数拆分（P3 范围，不在本次计划内）
- ❌ 不添加 AI 生成注释或文档

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO
- **Automated tests**: None
- **Framework**: None

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Converter + compile**: Use Bash - Run converter, then mvn compile, then diff output
- **Baseline comparison**: Pre-refactor output checksum vs post-refactor output checksum

### Baseline Capture (MANDATORY - Task 0)
Before ANY refactoring, capture the baseline output:
```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
python3 converter/flux_gauss.py -c fluxgauss.yaml
cd dest && mvn compile
find src -name '*.java' -o -name '*.xml' | sort | xargs md5 > /tmp/baseline.checksums
cp /tmp/baseline.checksums .sisyphus/evidence/baseline.checksums
```

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Baseline - MUST complete first):
└── Task 0: Capture baseline output checksums [quick]

Wave 1 (P0 - Dead Code Removal, all parallel):
├── Task 1: Delete mapper_method_id (L623) [quick]
├── Task 2: Delete _mapper_call (L4585) [quick]
├── Task 3: Delete _build_any_matchers (L6192) [quick]
└── Task 4: Delete _build_mock_args (L6227) [quick]

Wave 2 (P1 - Critical Dedup, sequential within):
├── Task 5: Extract _ensure_string_expr() + replace 11 call sites [deep]
├── Task 6: Extract _walk_into_targets() + refactor 3 INTO functions [deep]
└── Task 7: Extract _walk_table_nodes() + refactor 4 table-name functions [deep]

Wave 3 (P2 - Type Chain Consolidation):
└── Task 8: Extract _matches_java_type() + refactor 6 type-dispatch functions [deep]

Wave FINAL (Verification):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Output fidelity verification (deep)
└── Task F4: Scope fidelity check (deep)

Critical Path: Task 0 → Task 1-4 → Task 5 → Task 6 → Task 7 → Task 8 → F1-F4
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 0 | - | 1-8, F1-F4 |
| 1 | 0 | F1-F4 |
| 2 | 0 | F1-F4 |
| 3 | 0 | F1-F4 |
| 4 | 0 | F1-F4 |
| 5 | 0 | F1-F4 |
| 6 | 0 | F1-F4 |
| 7 | 0 | F1-F4 |
| 8 | 0 | F1-F4 |

> Tasks 1-8 can technically run in parallel, but each modifies the same file.
> Actual execution: Tasks 1-4 in parallel (small, non-overlapping line ranges),
> then Tasks 5-8 sequential (each adds a new function + modifies call sites,
> line numbers shift). Executor can batch 1-4 into one commit if preferred.

### Agent Dispatch Summary

- **Wave 0**: 1 task - T0 → `quick`
- **Wave 1**: 4 tasks - T1-T4 → `quick`
- **Wave 2**: 3 tasks - T5-T7 → `deep`
- **Wave 3**: 1 task - T8 → `deep`
- **FINAL**: 4 tasks - F1 → `oracle`, F2 → `unspecified-high`, F3 → `deep`, F4 → `deep`

---

## TODOs

- [ ] 0. Capture Baseline Output Checksums

  **What to do**:
  - Run the converter with config mode: `python3 converter/flux_gauss.py -c fluxgauss.yaml`
  - Run `cd dest && mvn compile` to verify baseline compiles
  - Generate checksums of all output files: `find dest/src -name '*.java' -o -name '*.xml' | sort | xargs md5 > .sisyphus/evidence/baseline.checksums`
  - Also save a copy of the current `converter/flux_gauss.py` checksum for reference
  - Run converter a SECOND time and diff output to verify determinism

  **Must NOT do**:
  - Do not modify any source files

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 0 (solo)
  - **Blocks**: Tasks 1-8, F1-F4
  - **Blocked By**: None

  **References**:
  - `converter/flux_gauss.py` — the file being refactored
  - `fluxgauss.yaml` — config file for converter invocation
  - `dest/` — output directory for generated Java project

  **Acceptance Criteria**:
  - [ ] Converter runs successfully (exit code 0)
  - [ ] `mvn compile` succeeds (BUILD SUCCESS)
  - [ ] `.sisyphus/evidence/baseline.checksums` file exists with MD5 hashes
  - [ ] Second run produces identical output (determinism confirmed)

  **QA Scenarios**:
  ```
  Scenario: Baseline converter execution
    Tool: Bash
    Preconditions: Clean working directory, dest/ may exist from prior runs
    Steps:
      1. Run `python3 converter/flux_gauss.py -c fluxgauss.yaml`
      2. Assert exit code is 0
      3. Run `cd dest && mvn compile`
      4. Assert output contains "BUILD SUCCESS"
      5. Run `find dest/src -name '*.java' -o -name '*.xml' | sort | xargs md5 > .sisyphus/evidence/baseline.checksums`
      6. Assert file `.sisyphus/evidence/baseline.checksums` exists and is non-empty
    Expected Result: All commands succeed, checksum file created
    Failure Indicators: Converter crashes, mvn compile fails, checksum file empty
    Evidence: .sisyphus/evidence/task-0-baseline.txt
  ```

  **Commit**: YES
  - Message: `refactor: capture baseline output checksums`
  - Files: `.sisyphus/evidence/baseline.checksums`

- [ ] 1. Delete dead function `mapper_method_id`

  **What to do**:
  - Delete the function `mapper_method_id` at line ~L623 (4 lines: def + body)
  - Verify no other file in the project references `mapper_method_id`
  - This function has been confirmed dead: zero call sites, zero string references, zero dynamic dispatch risk

  **Must NOT do**:
  - Do not modify any other function
  - Do not change any other lines

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 2, 3, 4 — non-overlapping line ranges)
  - **Parallel Group**: Wave 1
  - **Blocks**: None directly
  - **Blocked By**: Task 0

  **References**:
  - `converter/flux_gauss.py:623` — the function to delete:
    ```python
    def mapper_method_id(proc_name: str) -> str:
        ...
    ```

  **Acceptance Criteria**:
  - [ ] `mapper_method_id` no longer exists in the file
  - [ ] `grep -r "mapper_method_id" .` returns zero results
  - [ ] Converter still runs and compiles successfully

  **QA Scenarios**:
  ```
  Scenario: Dead function removed cleanly
    Tool: Bash
    Preconditions: Task 0 baseline captured
    Steps:
      1. Run `grep -n "mapper_method_id" converter/flux_gauss.py`
      2. Assert zero matches
      3. Run `grep -rn "mapper_method_id" --include='*.py' .`
      4. Assert zero matches
    Expected Result: Function completely gone from codebase
    Failure Indicators: Any grep match found
    Evidence: .sisyphus/evidence/task-1-dead-func-removal.txt
  ```

  **Commit**: NO (grouped with Tasks 2-4)

- [ ] 2. Delete dead function `_mapper_call`

  **What to do**:
  - Delete the function `_mapper_call` at line ~L4585 (6 lines: def + body)
  - Verify no other file in the project references `_mapper_call`

  **Must NOT do**:
  - Do not modify any other function

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 3, 4)
  - **Parallel Group**: Wave 1
  - **Blocks**: None directly
  - **Blocked By**: Task 0

  **References**:
  - `converter/flux_gauss.py:4585` — the function to delete

  **Acceptance Criteria**:
  - [ ] `_mapper_call` no longer exists in the file
  - [ ] `grep -rn "_mapper_call" --include='*.py' .` returns zero results

  **QA Scenarios**:
  ```
  Scenario: Dead function removed cleanly
    Tool: Bash
    Steps:
      1. Run `grep -n "_mapper_call" converter/flux_gauss.py`
      2. Assert zero matches
    Expected Result: Function gone
    Evidence: .sisyphus/evidence/task-2-dead-func-removal.txt
  ```

  **Commit**: NO (grouped with Tasks 1, 3, 4)

- [ ] 3. Delete dead function `_build_any_matchers`

  **What to do**:
  - Delete the function `_build_any_matchers` at line ~L6192 (6 lines: def + body)
  - Verify no other file references it

  **Must NOT do**:
  - Do not modify any other function

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 2, 4)
  - **Parallel Group**: Wave 1
  - **Blocks**: None directly
  - **Blocked By**: Task 0

  **References**:
  - `converter/flux_gauss.py:6192` — the function to delete

  **Acceptance Criteria**:
  - [ ] `_build_any_matchers` no longer exists in the file
  - [ ] `grep -rn "_build_any_matchers" --include='*.py' .` returns zero results

  **QA Scenarios**:
  ```
  Scenario: Dead function removed cleanly
    Tool: Bash
    Steps:
      1. Run `grep -n "_build_any_matchers" converter/flux_gauss.py`
      2. Assert zero matches
    Expected Result: Function gone
    Evidence: .sisyphus/evidence/task-3-dead-func-removal.txt
  ```

  **Commit**: NO (grouped with Tasks 1, 2, 4)

- [ ] 4. Delete dead function `_build_mock_args`

  **What to do**:
  - Delete the function `_build_mock_args` at line ~L6227 (13 lines: def + body)
  - Verify no other file references it

  **Must NOT do**:
  - Do not modify any other function

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 2, 3)
  - **Parallel Group**: Wave 1
  - **Blocks**: None directly
  - **Blocked By**: Task 0

  **References**:
  - `converter/flux_gauss.py:6227` — the function to delete

  **Acceptance Criteria**:
  - [ ] `_build_mock_args` no longer exists in the file
  - [ ] `grep -rn "_build_mock_args" --include='*.py' .` returns zero results

  **QA Scenarios**:
  ```
  Scenario: Dead function removed cleanly
    Tool: Bash
    Steps:
      1. Run `grep -n "_build_mock_args" converter/flux_gauss.py`
      2. Assert zero matches
    Expected Result: Function gone
    Evidence: .sisyphus/evidence/task-4-dead-func-removal.txt
  ```

  **Commit**: YES (groups with Tasks 1-3)
  - Message: `refactor: remove 4 dead functions (mapper_method_id, _mapper_call, _build_any_matchers, _build_mock_args)`
  - Files: `converter/flux_gauss.py`

- [ ] 5. Extract `_ensure_string_expr()` and replace 11 duplicate patterns (P1)

  **What to do**:
  - Add a new utility function near the other naming helpers (around L593-630, after `snake_to_pascal`):
    ```python
    def _ensure_string_expr(expr: str) -> str:
        """If expr is already a Java string literal, return as-is; otherwise wrap in String.valueOf()."""
        if expr.startswith('"') or expr.startswith("'"):
            return expr
        return f"String.valueOf({expr})"
    ```
  - Replace the following 11 occurrences of the repeated pattern `x if (x.startswith('"') or x.startswith("'")) else f"String.valueOf({x})"` with calls to `_ensure_string_expr(x)`:
    1. `_sf_substr` L3455: `s_expr = s if ...` → `s_expr = _ensure_string_expr(s)`
    2. `_sf_overlay` L3474: `s_expr = s if ...` → `s_expr = _ensure_string_expr(s)`
    3. `_sf_overlay` L3475: `repl_expr = repl if ...` → `repl_expr = _ensure_string_expr(repl)`
    4. `_sf_position` L3487: `substr_expr = substr if ...` → `substr_expr = _ensure_string_expr(substr)`
    5. `_sf_trim` L3541: `chars_expr = chars if ...` → `chars_expr = _ensure_string_expr(chars)`
    6. `_sf_convert` L3563: `expr_expr = expr_str if ...` → `expr_expr = _ensure_string_expr(expr_str)`
    7. `_handle_function` L3691: `fc = from_chars if ...` → `fc = _ensure_string_expr(from_chars)`
    8. `_handle_function` L3692: `tc = to_chars if ...` → `tc = _ensure_string_expr(to_chars)`
    9. `_handle_function` L3698: `arg0_expr = arg0 if ...` → `arg0_expr = _ensure_string_expr(arg0)`
    10. `_handle_function` L3705: `arg0_expr = arg0 if ...` → `arg0_expr = _ensure_string_expr(arg0)`
    11. `_handle_function` L3714: `arg0_expr = arg0 if ...` → `arg0_expr = _ensure_string_expr(arg0)`

  **Must NOT do**:
  - Do NOT touch `_is_string_expr` (L2136) — it has completely different semantics (returns bool, checks for String.valueOf prefix etc.)
  - Do NOT touch any logic other than the exact 11 `startswith` + `String.valueOf` wrap patterns listed above
  - Do NOT add comments or docstrings beyond the one in the new function

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []
  - Reason: Need careful reading of each call site to avoid touching wrong patterns

  **Parallelization**:
  - **Can Run In Parallel**: NO (modifies same file, must be after Wave 1)
  - **Parallel Group**: Wave 2 (sequential, first)
  - **Blocks**: Task 6
  - **Blocked By**: Tasks 0, 1-4

  **References**:

  **Pattern References** (the 11 sites to modify):
  - `converter/flux_gauss.py:3455` — `_sf_substr` string wrap for `s`
  - `converter/flux_gauss.py:3474-3475` — `_sf_overlay` string wrap for `s` and `repl`
  - `converter/flux_gauss.py:3487` — `_sf_position` string wrap for `substr`
  - `converter/flux_gauss.py:3541` — `_sf_trim` string wrap for `chars`
  - `converter/flux_gauss.py:3563` — `_sf_convert` string wrap for `expr_str`
  - `converter/flux_gauss.py:3691-3692` — `_handle_function` TRANSLATE wraps for `from_chars`, `to_chars`
  - `converter/flux_gauss.py:3698` — `_handle_function` UPPER wrap for `arg0`
  - `converter/flux_gauss.py:3705` — `_handle_function` LOWER wrap for `arg0`
  - `converter/flux_gauss.py:3714` — `_handle_function` INITCAP wrap for `arg0`

  **Contrast Reference** (do NOT touch):
  - `converter/flux_gauss.py:2136-2146` — `_is_string_expr()` is a boolean check, NOT a string wrap

  **Acceptance Criteria**:
  - [ ] `_ensure_string_expr` function exists near L593-630
  - [ ] All 11 patterns replaced with `_ensure_string_expr()` calls
  - [ ] `grep -n 'startswith.*String.valueOf' converter/flux_gauss.py` returns 0 matches for the wrap pattern
  - [ ] Converter runs and output matches baseline checksums

  **QA Scenarios**:
  ```
  Scenario: All 11 patterns replaced
    Tool: Bash
    Preconditions: Wave 1 completed
    Steps:
      1. Run `grep -n '_ensure_string_expr' converter/flux_gauss.py`
      2. Assert exactly 12 matches (1 def + 11 calls)
      3. Run `grep -c "startswith.*String.valueOf" converter/flux_gauss.py`
      4. Assert the specific wrap pattern `x if (x.startswith('"') or x.startswith("'")) else f"String.valueOf({x})"` no longer appears
    Expected Result: 12 references to _ensure_string_expr, zero remaining inline patterns
    Failure Indicators: Count mismatch, old pattern still present
    Evidence: .sisyphus/evidence/task-5-string-expr-dedup.txt

  Scenario: Output fidelity preserved
    Tool: Bash
    Steps:
      1. Run `python3 converter/flux_gauss.py -c fluxgauss.yaml`
      2. Run `find dest/src -name '*.java' -o -name '*.xml' | sort | xargs md5`
      3. Diff against `.sisyphus/evidence/baseline.checksums`
    Expected Result: All checksums identical to baseline
    Failure Indicators: Any checksum difference
    Evidence: .sisyphus/evidence/task-5-output-fidelity.txt
  ```

  **Commit**: YES
  - Message: `refactor: extract _ensure_string_expr() to eliminate 11 duplicate patterns`
  - Files: `converter/flux_gauss.py`
  - Pre-commit: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`

- [ ] 6. Extract `_walk_into_targets()` and refactor 3 INTO functions (P1)

  **What to do**:
  - Add a new shared inner-loop function near the other INTO functions (around L4504):
    ```python
    def _walk_into_targets(into_targets: list):
        """Yield (var_name, full_path_parts) for each INTO target variable."""
        for target in into_targets:
            for k, v in target.items():
                if k == "Expr" and isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            for ik, iv in item.items():
                                if ik in ("PlVariable", "ColumnRef"):
                                    name = iv[-1] if isinstance(iv, list) else iv
                                    parts = list(iv) if isinstance(iv, list) else [iv]
                                    yield (name, parts)
    ```
  - Refactor `_extract_into_variable(targets)` to use `next(_walk_into_targets(targets), None)` and return just the name (or None)
  - Refactor `_extract_all_into_variables(targets)` to use `[name for name, _ in _walk_into_targets(targets)]`
  - Refactor `_extract_all_into_targets(targets)` to use `list(_walk_into_targets(targets))`
  - All three functions keep their existing signatures and return types — only their internal implementation changes

  **Must NOT do**:
  - Do not change function signatures or return types
  - Do not merge the three functions into one

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Task 5)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 0, 1-4

  **References**:

  **Pattern References**:
  - `converter/flux_gauss.py:4504-4517` — `_extract_into_variable`: extracts first variable name from INTO targets, returns `Optional[str]`
  - `converter/flux_gauss.py:4520-4531` — `_extract_all_into_variables`: extracts all variable names, returns `list[str]`
  - `converter/flux_gauss.py:4534-4547` — `_extract_all_into_targets`: extracts all (name, full_path) tuples, returns `list[tuple]`

  **Why these share logic**: All three have identical 4-level nested loops: `for target → for k,v → for item → for ik,iv` checking `PlVariable`/`ColumnRef`.

  **Acceptance Criteria**:
  - [ ] `_walk_into_targets` generator function exists
  - [ ] All 3 INTO functions use `_walk_into_targets` internally
  - [ ] Function signatures unchanged
  - [ ] Converter output matches baseline

  **QA Scenarios**:
  ```
  Scenario: INTO functions refactored correctly
    Tool: Bash
    Steps:
      1. Run `grep -n '_walk_into_targets' converter/flux_gauss.py`
      2. Assert at least 4 matches (1 def + 3 call sites)
      3. Verify _extract_into_variable still returns Optional[str]
      4. Verify _extract_all_into_variables still returns list
      5. Verify _extract_all_into_targets still returns list of tuples
    Expected Result: Shared generator used by all 3 functions
    Failure Indicators: Any function not using the shared generator
    Evidence: .sisyphus/evidence/task-6-into-dedup.txt

  Scenario: Output fidelity preserved
    Tool: Bash
    Steps:
      1. Run converter and compare checksums against baseline
    Expected Result: All checksums identical
    Evidence: .sisyphus/evidence/task-6-output-fidelity.txt
  ```

  **Commit**: YES
  - Message: `refactor: extract _walk_into_targets() to deduplicate INTO variable extraction`
  - Files: `converter/flux_gauss.py`
  - Pre-commit: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`

- [ ] 7. Extract `_walk_table_nodes()` and refactor 4 table-name functions (P1)

  **What to do**:
  - Add a new shared function near the other table-name functions (around L4459):
    ```python
    def _walk_table_nodes(data, key_hint=None):
        """Extract table name strings from AST node(s) containing Table entries.
        
        key_hint: which key to check ('table' for single, 'tables' for list)
        """
        # Shared logic: iterate nodes, find Table entries, extract name[-1]
    ```
  - The exact design: observe that all 4 functions share the pattern `for item → for k,v → if k=="Table" → name[-1]`. Extract this into `_walk_table_nodes()` that yields table names.
  - Refactor `_extract_table_names(from_clause)` to use it
  - Refactor `_extract_table_names_from_insert(insert_data)` to use it
  - Refactor `_extract_table_names_from_update(update_data)` to use it
  - Refactor `_extract_table_name_from_dml(dml_data)` to use it (this is the unified version that handles both cases)

  **Must NOT do**:
  - Do not change function signatures or return types
  - Do not merge the four functions into one
  - Do NOT fix the L4495 dead branch bug (it's in `_extract_table_name_from_dml`, the `elif isinstance(val, list)` after `if isinstance(val, list)` — out of scope)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Task 6)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 0, 1-4

  **References**:

  **Pattern References**:
  - `converter/flux_gauss.py:4459-4469` — `_extract_table_names`: iterates FROM clause items, finds `Table` entries
  - `converter/flux_gauss.py:4472-4474` — `_extract_table_names_from_insert`: extracts from `insert_data["table"]`
  - `converter/flux_gauss.py:4477-4485` — `_extract_table_names_from_update`: iterates `update_data["tables"]`, finds `Table`
  - `converter/flux_gauss.py:4488-4501` — `_extract_table_name_from_dml`: unified version, handles both patterns

  **Acceptance Criteria**:
  - [ ] `_walk_table_nodes` utility function exists
  - [ ] All 4 table-name functions use it
  - [ ] Function signatures unchanged
  - [ ] Converter output matches baseline

  **QA Scenarios**:
  ```
  Scenario: Table name functions refactored
    Tool: Bash
    Steps:
      1. Run `grep -n '_walk_table_nodes' converter/flux_gauss.py`
      2. Assert at least 5 matches (1 def + 4 call sites)
      3. Verify all 4 original functions still exist with same signatures
    Expected Result: Shared utility used by all 4 functions
    Evidence: .sisyphus/evidence/task-7-table-dedup.txt

  Scenario: Output fidelity preserved
    Tool: Bash
    Steps:
      1. Run converter and compare checksums against baseline
    Expected Result: All checksums identical
    Evidence: .sisyphus/evidence/task-7-output-fidelity.txt
  ```

  **Commit**: YES
  - Message: `refactor: extract _walk_table_nodes() to deduplicate table name extraction`
  - Files: `converter/flux_gauss.py`
  - Pre-commit: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`

- [ ] 8. Extract `_matches_java_type()` and consolidate 6 type-dispatch functions (P2)

  **What to do**:
  - Add a new utility function near the type mapping section (around L555, after `is_simple_java_type`):
    ```python
    def _matches_java_type(java_type: str, *keywords: str) -> bool:
        """Check if java_type (case-insensitive) contains any of the given keywords.
        
        Common keywords: 'bigdecimal', 'long', 'integer', 'double', 'float', 'boolean'
        """
        t = java_type.lower() if java_type else ""
        return any(kw in t for kw in keywords)
    ```
  - Identify the 6 functions with repeated type-check chains and refactor them to use `_matches_java_type()`:
    1. `_default_for_type` (L5354) — has chain: `"long" in t`, `"integer" in t or t == "int"`, `"bigdecimal" in t`, `"double" in t`, `"float" in t`, `"boolean" in t`
    2. `_is_numeric_default` (L5375) — has chain: `"bigdecimal" in t`, `"long" in t`, `"integer" in t or t == "int"`, `"double" in t`, `"float" in t`
    3. `_wrap_default_for_type` (L5412) — has chain: `"bigdecimal" in t`, `"long" in t`, `"integer" in t or t == "int"`, `"double" in t`, `"float" in t`
    4. `_default_test_value` (L5972) — has chain: `"long" in lower`, `"integer" in lower or "int" in lower`, `"big_decimal" in lower`, `"double" in lower`, `"float" in lower`, `"boolean" in lower`
    5. `_mock_value_for_column` (L6108) — uses column-name heuristics, may only partially benefit
    6. `_itest_generate_test_value` (L6536) — uses SQL type names (not Java), may only partially benefit

  - For functions 1-4, replace the `if "keyword" in t:` chains with `if _matches_java_type(java_type, "keyword"):`
  - For functions 5-6, evaluate case-by-case: if they use Java type checks, apply; if they use SQL type checks, skip
  - **Important**: `_default_for_type` uses `t.startswith("map<")` and `t.startswith("atomicreference")` — these are NOT type-keyword checks, keep them as-is
  - **Important**: Some functions use `t == "int"` as exact match — ensure this is preserved (e.g., `_matches_java_type(t, "integer") or t == "int"`)

  **Must NOT do**:
  - Do not merge `_default_for_type`, `_default_test_value`, `_mock_value_for_column`, `_itest_generate_test_value` into one function (they serve different purposes)
  - Do not change function signatures
  - Do not change the actual default values or behavior — only the type-checking mechanism

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (after Task 7)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 0, 1-4

  **References**:

  **Pattern References**:
  - `converter/flux_gauss.py:5354-5372` — `_default_for_type`: zero-value defaults, type chain
  - `converter/flux_gauss.py:5375-5410` — `_is_numeric_default`: numeric check, type chain
  - `converter/flux_gauss.py:5412-5449` — `_wrap_default_for_type`: boxing wrappers, type chain
  - `converter/flux_gauss.py:5972-6023` — `_default_test_value`: test mock values, type chain
  - `converter/flux_gauss.py:6108-6120` — `_mock_value_for_column`: column-name heuristic mock values
  - `converter/flux_gauss.py:6536-6559` — `_itest_generate_test_value`: SQL-type based test values

  **Acceptance Criteria**:
  - [ ] `_matches_java_type` function exists near L555
  - [ ] At least 4 of the 6 functions use `_matches_java_type()` for their type chains
  - [ ] All `if "keyword" in t:` patterns in those functions replaced
  - [ ] Converter output matches baseline

  **QA Scenarios**:
  ```
  Scenario: Type dispatch consolidated
    Tool: Bash
    Steps:
      1. Run `grep -n '_matches_java_type' converter/flux_gauss.py`
      2. Assert at least 5 matches (1 def + 4+ calls)
      3. Verify no function has bare `"bigdecimal" in t` or `"long" in t` chains remaining (in the 4 target functions)
    Expected Result: Shared type matcher used consistently
    Evidence: .sisyphus/evidence/task-8-type-dispatch.txt

  Scenario: Output fidelity preserved
    Tool: Bash
    Steps:
      1. Run converter and compare checksums against baseline
    Expected Result: All checksums identical
    Evidence: .sisyphus/evidence/task-8-output-fidelity.txt
  ```

  **Commit**: YES
  - Message: `refactor: extract _matches_java_type() to consolidate type dispatch chains`
  - Files: `converter/flux_gauss.py`
  - Pre-commit: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `python3 converter/flux_gauss.py -c fluxgauss.yaml` and `cd dest && mvn compile`. Review all changed regions of `converter/flux_gauss.py` for: syntax errors, indentation issues, missing imports, broken references to deleted functions. Check for AI slop: excessive comments, over-abstraction, generic names.
  Output: `Build [PASS/FAIL] | Compile [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Output Fidelity Verification** — `deep`
  Run the converter. Compare output checksums against baseline in `.sisyphus/evidence/baseline.checksums`. Every generated file must be byte-identical. Run converter TWICE to verify determinism. Diff the two runs.
  Output: `Checksums [N/N match] | Determinism [PASS/FAIL] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Verify no function signatures changed. Verify no external deps added. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Task 0**: `refactor: capture baseline output checksums` — baseline.checksums
- **Tasks 1-4** (one commit): `refactor: remove 4 dead functions (mapper_method_id, _mapper_call, _build_any_matchers, _build_mock_args)` — converter/flux_gauss.py
- **Task 5**: `refactor: extract _ensure_string_expr() to eliminate 11 duplicate patterns` — converter/flux_gauss.py
- **Task 6**: `refactor: extract _walk_into_targets() to deduplicate INTO variable extraction` — converter/flux_gauss.py
- **Task 7**: `refactor: extract _walk_table_nodes() to deduplicate table name extraction` — converter/flux_gauss.py
- **Task 8**: `refactor: extract _matches_java_type() to consolidate type dispatch chains` — converter/flux_gauss.py

---

## Success Criteria

### Verification Commands
```bash
python3 converter/flux_gauss.py -c fluxgauss.yaml  # Expected: no errors, normal output
cd dest && mvn compile                               # Expected: BUILD SUCCESS
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All 4 dead functions completely removed
- [ ] `_ensure_string_expr()` defined and used at all 11 sites
- [ ] `_walk_into_targets()` defined, 3 INTO functions use it
- [ ] `_walk_table_nodes()` defined, 4 table-name functions use it
- [ ] `_matches_java_type()` defined, 6 type-dispatch functions use it
- [ ] Generated output byte-identical to baseline
