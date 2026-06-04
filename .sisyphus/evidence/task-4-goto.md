# Task 4 Evidence: GOTO/Control Flow Comparison (dest_py vs dest_ru)

## Summary

| SQL File | Procedure | dest_py | dest_ru | Verdict |
|---|---|---|---|---|
| proc_GOto.sql | search_target | Working (GOTO→_gotoTarget) | **STUB** (empty body) | **dest_py wins** |
| proc_GOto.sql | process_data | Working (cursor+GOTO→while+break) | **Broken** (missing cursor, infinite inner while) | **dest_py wins** |
| proc_GOto.sql | parse_cmd | Working (state machine) | Working (state machine) | **Tie** |
| proc_Five_Gotos.sql | sp_allocate_resource | Working (try/finally) | Working (try/finally, wrong error msg) | **dest_py slightly better** |
| proc_Five_Gotos.sql | sp_purge_logs | **Bug** (missing output set) | **Bug** (__ROWCOUNT__=0, runs once) | **Both broken** |
| proc_Five_Gotos.sql | sp_generate_report | Working (if-guard) | Working (try/finally trick) | **Tie** |
| proc_Five_Gotos.sql | sp_validate_orders | **Bug** (outer loop breaks early) | **Bug** (empty list iteration) | **Both broken** |
| proc_Five_Gotos.sql | sp_order_state_machine | Fragile (fall-through) | **Broken** (unconditional Done override) | **dest_py slightly better** |
| gauss_update_select.sql | proc_batch_adjust_salary | Working | **Bug** (constants=0) | **dest_py wins** |
| gauss_update_select.sql | proc_adjust_by_rank | Working | Working | **Tie** |
| gauss_update_select.sql | proc_rollback_to_date | Working | Working | **Tie** |

**Overall: dest_py 4 wins, dest_ru 0 wins, 3 ties, 4 both-broken (dest_py less broken in most)**

---

## 1. proc_GOto.sql — 3 Procedures

### 1.1 search_target

**SQL GOTO Graph:**
```
FOR i 1..100 → FOR j 1..100 → IF condition → GOTO found_it → <<found_it>>
                                              ↓ (no match)
                                    RAISE NOTICE 'Not found' → RETURN
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO strategy | `_gotoTarget` string + break + post-loop dispatch | Empty stub |
| Nested loop breakout | break from inner + outer with guard | N/A |
| found_it label | Post-loop if dispatch | N/A |
| Completeness | Full implementation | **STUB: "TODO: Auto-generated stub"** |

**Verdict:** dest_py only implementation. dest_ru emits empty method body.

---

### 1.2 process_data

**SQL GOTO Graph (3 labels, 3 GOTOs):**
```
OPEN cur → LOOP → FETCH → IF NOT FOUND → GOTO done → <<done>> CLOSE+log
                           ↓ (found)
                      IF status='INVALID' → GOTO next_iter → <<next_iter>> CONTINUE
                           ↓ (valid)
                      BEGIN INSERT → EXCEPTION → GOTO cleanup → <<cleanup>> CLOSE+RAISE
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| Cursor handling | `selectProcessData()` → List<Map> emulation | **Missing** — no selectProcessData call, `found=false` hardcoded |
| GOTO done | `_gotoTarget="done"; break` | `currentState = Done` but cursor never fetched |
| GOTO next_iter | `continue` (correct for loop skip) | Sets `currentState=NextIter` inside `while(true)` that never breaks |
| GOTO cleanup | `_gotoTarget="cleanup"; break` from catch | `currentState = Cleanup` |
| State machine | while(true) + _gotoTarget dispatch | enum state machine |
| **Critical bug** | — | Inner `while(true)` at line 43 inside `case NextIter` never breaks — infinite loop. State transitions inside never reach the outer switch. |
| **Critical bug** | — | Done case has `// UNREACHABLE` comments — the done path is dead code |
| Mapper: selectProcessData | Present (`SELECT * FROM src_table`) | **Missing from Mapper.java and Mapper.xml** |
| Mapper: insertProcessData | Present | Present |

**dest_py issue (Minor):** `found` variable declared but unused (relies on curIdx/curResult.size()).

**Verdict:** dest_py works correctly. dest_ru is non-functional — missing cursor query, infinite loop.

---

### 1.3 parse_cmd

**SQL GOTO Graph (circular state machine):**
```
<<read_tok>> → tok=substr → IF "$" → GOTO handle_var → <<handle_var>> → GOTO read_tok
                                    IF ";" → GOTO done → <<done>>
                                    ELSE → GOTO read_tok (backward = loop)
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO strategy | State machine enum `ParseCmdState` | State machine enum `ParseCmdState` |
| read_tok→handle_var | `currentState = HandleVar` | `currentState = HandleVar` |
| read_tok→done | `currentState = Done` | `currentState = Done` |
| read_tok→read_tok | `currentState = ReadTok` (self-loop) | `currentState = ReadTok` (self-loop) |
| handle_var→read_tok | `currentState = ReadTok` | `currentState = ReadTok` |
| Guard limit | `_smGuard < 10000` | `_smGuard < 10000` |
| substring expression | Verbose (double Math.min/max) | Slightly cleaner |
| Done: log message | `log.info("Parsing done at {}", ...)` | `log.info("'Parsing done'")` (extra quotes) |

**Verdict:** Both correct and equivalent. Minor style differences.

---

## 2. proc_Five_Gotos.sql — 5 Procedures (5 GOTO Patterns)

### 2.1 Pattern A: sp_allocate_resource (Error Cleanup Exit)

**SQL GOTO Graph:**
```
SELECT nextval → INSERT lock → SELECT quota → IF empty → GOTO cleanup → <<cleanup>> DELETE lock
                                                 ↓ (has quota)
                                           UPDATE + INSERT → p_result := SUCCESS
                                                                 ↓ (falls through to cleanup)
EXCEPTION WHEN OTHERS → DELETE lock → p_result := ERROR → RAISE
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO cleanup strategy | try/finally (cleanup = finally block) | try/try/finally (same pattern) |
| Early return (quota empty) | `pResult.set("QUOTA_EMPTY"); return;` | Same |
| EXCEPTION WHEN OTHERS | outer catch: `throw new BusinessException(e.getMessage())` | outer catch: `log.info("")` — **swallows exception** |
| Error message | `"ERROR:" + e.getMessage()` | `"ERROR:" + __SQLERRM__` — **always empty** (never set) |
| Delete in finally | `deleteSpAllocateResource` | `deleteSpAllocateResource` |
| Delete in catch | `deleteSpAllocateResource_1` | `deleteSpAllocateResource_1` |

**Severity:**
- **Major** (dest_ru): Error handler swallows the re-raised exception (SQL does `RAISE`, dest_ru catches and logs empty). The `__SQLERRM__` variable is never populated.
- **Minor** (dest_ru): `log.info("")` is useless logging.

**Verdict:** Both functionally correct for happy path. dest_py has better error handling.

---

### 2.2 Pattern B: sp_purge_logs (Backward GOTO = Loop)

**SQL GOTO Graph:**
```
<<purge_loop>> → DELETE ... LIMIT v_batch → v_rowcount := SQL%ROWCOUNT → v_deleted += v_rowcount
                                                                              ↓
                                                               IF v_rowcount = v_batch → COMMIT → GOTO purge_loop (backward)
                                                               ELSE → p_deleted_count := v_deleted → COMMIT
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO strategy | `do { ... } while (vRowcount == vBatch)` | Same pattern |
| ROWCOUNT capture | `int __rc = mapper.delete(...); vRowcount = __rc;` — **correct** | `mapper.delete(...); vRowcount = __ROWCOUNT__;` — **__ROWCOUNT__ is always 0** |
| Output param | **Missing**: `pDeletedCount.set(null)` but never set to `vDeleted` | **Correct**: `pDeletedCount.set(vDeleted)` at line 68 |
| COMMIT handling | Dead code comment `// COMMIT — auto-committed` | Comment `// COMMIT;` |

**Severity:**
- **Critical** (dest_ru): `__ROWCOUNT__` is never updated. Always 0. So `vRowcount = 0` and `while (0 == 1000)` is always false. Loop runs exactly **once** instead of batching. This breaks the entire batched delete semantics.
- **Major** (dest_py): `pDeletedCount` never set to `vDeleted`. Output parameter is always null.

**Verdict:** Both broken in different ways. dest_py has correct loop logic but wrong output. dest_ru has correct output but loop never repeats.

---

### 2.3 Pattern C: sp_generate_report (Forward Skip)

**SQL GOTO Graph:**
```
v_header := ... → IF SUMMARY → GOTO assemble_report → skip FOR loop
                  ↓ (not SUMMARY)
              FOR rec IN (SELECT ...) → v_detail := concat...
                  ↓ (falls through)
<<assemble_report>> → IF SUMMARY → p_content := header + " [Summary Mode]"
                                         ELSE → p_content := header + detail
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO strategy | Condition inversion: `if (!"SUMMARY".equals(...))` guards the FOR loop | try/finally: return inside try triggers finally with assemble logic |
| FOR loop skip | Correct — guard with inverted condition | Correct — early return skips FOR body |
| assemble_report block | Lines 279-283 (correct) | finally block at lines 87-93 |
| Code duplication | **Minor**: Lines 279-283 and 284-288 are identical (duplicate assemble_report block) | No duplication |
| Output correctness | Correct | Correct |

**Severity:**
- **Minor** (dest_py): Duplicated assemble_report block (4 lines repeated). Functionally harmless.

**Verdict:** Both correct. dest_ru's try/finally trick is unusual but works correctly.

---

### 2.4 Pattern D: sp_validate_orders (Deep Nested Breakout)

**SQL GOTO Graph:**
```
FOR order_rec IN (SELECT orders) → <<check_next>>
  FOR credit_rec IN (SELECT credits)
    IF credit_level < 60 → v_invalid++ → GOTO next_order → skip to outer loop next iter
    FOR item_rec IN (SELECT items)
      IF item_status = 'BLOCKED' → v_invalid++ → GOTO next_order → skip to outer loop next iter
  UPDATE orders SET process_flag='VALIDATED'  ← only runs if all checks pass
<<next_order>> → NULL;  ← GOTO lands here, continues outer loop
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| GOTO strategy | `_gotoTarget` + cascading break from inner loops | `mainLoop:` labeled while + for with `continue` |
| Outer loop iteration | `for (orderRec : orderRecList)` | `for (_orderRec : Collections.<...>emptyList())` |
| Inner loop queries | 3 separate mapper calls | **No mapper calls** — iterates `Collections.emptyList()` |
| GOTO next_order behavior | `_gotoTarget="next_order"; break;` — breaks all inner loops | `continue;` (no label — continues wrong loop) |

**Severity:**
- **Critical** (dest_ru): `Collections.<Map<String, Object>>emptyList()` — all three FOR loops iterate over empty lists. The queries for orders, credits, and items are **never executed**. The entire procedure body is dead code. Only the `updateSpValidateOrders` mapper call exists but never runs.
- **Critical** (dest_py): After `_gotoTarget` is set to "next_order", line 354 `if (_gotoTarget != null) break;` breaks the **outer** for-loop entirely. In SQL, `GOTO next_order` jumps to the `<<next_order>>` label which is **inside** the outer FOR loop, allowing processing to continue with the next order. The Java implementation stops after the first invalid order. This means if order #1 is invalid, orders #2..#N are never processed.

**Verdict:** Both critically broken. dest_py at least executes queries but stops too early. dest_ru never executes any queries at all.

---

### 2.5 Pattern E: sp_order_state_machine (Network State Machine)

**SQL GOTO Graph:**
```
<<state_init>> → IF SUBMIT → UPDATE PENDING → GOTO state_pending
                 ELSE → GOTO state_done

<<state_pending>> → IF PAY → UPDATE PAID → GOTO state_paid
                    IF CANCEL → UPDATE CANCELLED → GOTO state_done
                    IF TIMEOUT AND retry<3 → retry++ → GOTO state_pending (self-loop)
                    ELSE → GOTO state_done

<<state_paid>> → IF SHIP → UPDATE SHIPPED → GOTO state_shipped
                 IF REFUND → UPDATE REFUNDING → GOTO state_refunding
                 ELSE → GOTO state_done

<<state_shipped>> → IF DELIVER → UPDATE COMPLETED
                    GOTO state_done (always)

<<state_refunding>> → IF APPROVE → UPDATE REFUNDED → GOTO state_done
                      IF REJECT → UPDATE PAID → GOTO state_paid (backward!)
                      ELSE → GOTO state_done

<<state_done>> → INSERT log → return v_current
```

| Aspect | dest_py | dest_ru |
|---|---|---|
| State machine | `enum SpOrderStateMachineState {StateInit..StateDone}` | Same enum |
| StateInit→StatePending | Correct (break after) | Sets `StatePending` then **overwrites with StateDone** at line 145 |
| StatePending fall-through | Missing break → falls to StatePaid | Same overwrite bug |
| StatePaid fall-through | Missing break → falls to StateShipped | Same overwrite bug |
| StateShipped→StateDone | Correct (always sets Done) | Correct |
| StateRefunding→StatePaid (backward) | Correct | Sets `StatePaid` then **overwrites with StateDone** |
| StateDone (terminal) | Correct: sets output + inserts log | Correct: sets output + inserts log |

**Severity:**
- **Critical** (dest_ru): Every state case has an **unconditional** `currentState = StateDone` after the conditional transitions. This means:
  - `StateInit + SUBMIT` → sets Pending, then immediately sets Done. Machine terminates at Done after 1 step with vCurrent="PENDING".
  - `StatePaid + SHIP` → sets Shipped, then immediately sets Done. vCurrent="SHIPPED" but log records wrong state.
  - `StateRefunding + REJECT` → sets StatePaid, then immediately sets Done. Never actually re-enters Paid logic.
  - **The state machine can never execute more than 2 iterations.** All backward jumps (REJECT→Paid, TIMEOUT→Pending) are broken.

- **Major** (dest_py): Missing `break;` statements in StatePending, StatePaid, and StateRefunding cases. Causes fall-through to subsequent cases. This is accidentally correct for the "else→done" path (falls through to StateShipped which sets Done), but:
  - The behavior relies on switch fall-through which is a code smell
  - If the order of cases were different, behavior would change
  - The `StatePending + TIMEOUT (retry>=3)` path falls through to StatePaid, then StateShipped — accidentally reaches Done, but via wrong intermediate states

**Verdict:** dest_py is fragile but mostly works. dest_ru is completely broken — state machine never progresses past 2 steps.

---

## 3. gauss_update_select.sql — No GOTO (UPDATE SET = Subquery)

This file has no GOTO. It tests `UPDATE SET (col1, col2, ...) = (SELECT ...)` syntax and package constants.

### 3.1 proc_batch_adjust_salary

| Aspect | dest_py | dest_ru |
|---|---|---|
| Constants MAX_BONUS_PCT | `new BigDecimal("0.50")` ✅ | `BigDecimal.ZERO` ❌ |
| Constants MIN_BONUS_PCT | `new BigDecimal("0.02")` ✅ | `BigDecimal.ZERO` ❌ |
| UPDATE SET = subquery in XML | Preserved as-is (formatted) | Preserved as-is (single line) |
| SUM select (old total) | Correct | Correct |
| SUM select (new total) | Correct | Correct |
| DBE_OUTPUT | Commented out | Commented out |

**Severity:**
- **Critical** (dest_ru): Package constants `MINBONUSPCT` and `MAXBONUSPCT` both initialized to `BigDecimal.ZERO` instead of 0.02 and 0.50. This means the LEAST/GREATEST bounds in the SQL are both 0, clamping bonus to 0 instead of [0.02, 0.50].

### 3.2 proc_adjust_by_rank

Both converters produce correct output. Single mapper call, correct XML.

### 3.3 proc_rollback_to_date

| Aspect | dest_py | dest_ru |
|---|---|---|
| vCnt select | Correct | Correct (extra unused param) |
| UPDATE rollback | Correct | Correct |
| __ROWCOUNT__ | N/A | Always 0, only used in commented-out DBE_OUTPUT — no functional impact |

---

## Difference Summary by Category

### Critical (Functional correctness broken)
| ID | Converter | File | Procedure | Issue |
|---|---|---|---|---|
| C1 | dest_ru | proc_GOto | search_target | Entire method is empty stub |
| C2 | dest_ru | proc_GOto | process_data | Missing cursor query, infinite inner while(true) |
| C3 | dest_ru | proc_FiveGotos | sp_purge_logs | `__ROWCOUNT__` always 0 → loop runs once |
| C4 | dest_py | proc_FiveGotos | sp_purge_logs | `pDeletedCount` never set to `vDeleted` |
| C5 | dest_ru | proc_FiveGotos | sp_validate_orders | All loops iterate `Collections.emptyList()` — dead code |
| C6 | dest_py | proc_FiveGotos | sp_validate_orders | `_gotoTarget` breaks outer loop — only processes first invalid |
| C7 | dest_ru | proc_FiveGotos | sp_order_state_machine | Unconditional `currentState = StateDone` after every case |
| C8 | dest_ru | gauss_update | proc_batch_adjust_salary | Constants initialized to ZERO instead of 0.50/0.02 |

### Major (Semantic divergence, may cause runtime issues)
| ID | Converter | File | Procedure | Issue |
|---|---|---|---|---|
| M1 | dest_py | proc_FiveGotos | sp_order_state_machine | Missing break; statements — fragile fall-through behavior |
| M2 | dest_ru | proc_FiveGotos | sp_allocate_resource | Error handler swallows exception, `__SQLERRM__` always empty |

### Minor (Style, non-functional)
| ID | Converter | File | Procedure | Issue |
|---|---|---|---|---|
| m1 | dest_py | proc_FiveGotos | sp_generate_report | Duplicated assemble_report block (4 lines) |
| m2 | dest_ru | proc_GOto | parseCmd | Extra quotes in log message: `'Parsing done'` |
| m3 | dest_ru | proc_FiveGotos | sp_allocate_resource | `log.info("")` in catch block — useless logging |
| m4 | dest_py | proc_GOto | process_data | Unused `found` boolean variable |

---

## GOTO Translation Strategy Comparison

### dest_py strategies:
1. **Simple forward GOTO** (search_target): `_gotoTarget` string variable + post-loop if/else dispatch
2. **Multi-target in loop** (process_data): `while(true)` + `_gotoTarget` + break + post-loop dispatch
3. **Circular GOTO** (parse_cmd): State machine enum + while+switch + guard
4. **Cleanup GOTO** (sp_allocate_resource): try/finally (cleanup = finally)
5. **Backward GOTO** (sp_purge_logs): do/while loop
6. **Forward skip** (sp_generate_report): Condition inversion + if-guard
7. **Nested breakout** (sp_validate_orders): `_gotoTarget` + cascading break
8. **Network state machine** (sp_order_state_machine): State machine enum + while+switch

### dest_ru strategies:
1-8. Same pattern recognition, but execution has more bugs. Key differences:
- More likely to emit **stubs** for complex patterns (search_target)
- `__ROWCOUNT__` and `__SQLERRM__` variables never populated
- `Collections.emptyList()` used instead of actual mapper queries in some cases
- State machine transitions have unconditional overrides after conditionals

### Both converters share:
- State machine enum pattern for circular/network GOTOs
- `_smGuard` safety limit (10000 iterations)
- try/finally for cleanup patterns
- Condition inversion for forward-skip patterns
