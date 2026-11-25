# issue #136 regression fixtures

Minimal repros for the defects fixed across `fix/issue-136-a-g` (batch 1:
classes A + G) and `fix/issue-136-c-b-f` (batch 2: classes C, B-OUT, F).

## Layout

Batch 1 (PR #137):

- `a-null-numeric.sql` + `a-caller.sql` + `a.yaml` — case **a** (A-class):
  same-package call `fnc_calc(1)` omits `DEFAULT` params (one `DEFAULT NULL`,
  two `DEFAULT 0` on primitive `long` signature); `p_value IN NUMBER` is a
  primitive `long` compared via `p_value != NULL` and passed through `NVL`.
  Pre-fix output pushed `null` for every missing arg (`null` → `long`
  compile error), emitted `pValue != null` on a primitive, and cast a `Long`
  self-call result to `BigDecimal`.
- `g-arity.sql` + `g.yaml` — case **g** (G-class): cross-package qualified
  call `pkg_g_target.fnc_with_defaults('a','b')` passes 2 args to a 3-param
  function whose `p_c` has `DEFAULT ''`; strict arity equality dropped all
  candidates → emitted call with wrong arity.

## Verification

Since batch 4 (F7), relative `sources` entries in a yaml resolve against
the **yaml file's directory**, not the process CWD — the commands below
work from any directory (`output_dir` still resolves against the CWD, and
`-o` is ignored when `-c` is used, so run from this directory to keep the
`out-*` dirs together). Declared sources that are missing, or runs that
produce zero procedures, now exit non-zero instead of silently emitting a
skeleton project:

```bash
cd tests/regress/fixtures/issue136
# case a uses schema-qualified standalone functions (REPRO.*) which the
# validator rejects — pass --skip-validate (same as the original repro).
../../../../target/debug/fluxgauss -c a.yaml --skip-validate
../../../../target/debug/fluxgauss -c g.yaml
(cd out-a && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
(cd out-g && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
```

Expected generated call shapes:

- a: `this.fncCalc(1, null, 0L)` — `DEFAULT NULL` param stays `null` (boxed
  `Long`), `DEFAULT 0` params pad `0L` (primitive `long`); batch-4 F5:
  `p_value != NULL` folds to `if (false)` (SQL UNKNOWN, not constant-true),
  `NVL(p_value, 3600)` emits `(pValue != null ? pValue : 3600L)` on the
  boxed `Long` signature, and the generated test passes `null` for `p_b`.
- g: `gTargetService.fncWithDefaults("a", "b", "")` — relaxed arity match +
  `DEFAULT ''` padding.

Batch 2:

- `c-atomic.sql` + `c.yaml` — case **c** (C-class): `pkg_c_callee.prc_log`
  declares 3- and 6-param overloads; the caller passes its own OUT param
  `p_code` into the 6-param IN slot. Statement-level procedure calls used to
  resolve only the first-declared overload for arg coercion, so tail args
  skipped the OUT→IN `.get()` unwrap (`AtomicReference<String>` passed where
  `String` expected). Now the overload whose arity best fits the call site
  is selected (exact → smallest ≥ → widest).
- `b-out-cursor.sql` + `b.yaml` — case **b** (B-OUT): a procedure whose
  `OUT refcur` param is promoted to a `List` return still emitted
  `pResult.set(null)` for the exception-branch `p_result := NULL`. Residual
  reads/writes of the removed variable now degrade to a comment / `null`.
- `f-mapper-duplicate.sql` + `f.yaml` — case **f** (F-class):
  `EXECUTE IMMEDIATE v_sql ... USING n` where the template contains a named
  `:n` bind produced two `@Param("n")` entries (one from `extra_params`, one
  from SQL-text local-var scanning) and a 3-param mapper vs 2-arg service
  call. `build_mapper_method` now dedupes bind names across all sources.

```bash
cd tests/regress/fixtures/issue136
../../../../target/debug/fluxgauss -c c.yaml
../../../../target/debug/fluxgauss -c b.yaml
../../../../target/debug/fluxgauss -c f.yaml
(cd out-c && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
(cd out-b && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
(cd out-f && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
```

Expected generated shapes:

- c: `cCalleeService.prcLog("proc", "1", "info", "4", String.valueOf(pCode.get()), "")`
- b: `// p_result := <value> — refcursor OUT param promoted to List return (issue #136)`
- f: `selectPrcDynamic(@Param("pId") Long pId, @Param("n") Long n)` — single `n`.

Batch 3 (`fix/issue-136-d-e-x`):

- `d-coercion.sql` + `d.yaml` — case **d** (D-class): `v_count := fnc_text('1')`
  assigns a String-returning call to NUMBER (`(Number)` cast cannot compile →
  `Long.parseLong(String.valueOf(..))`); `fnc_text(1)` passes an int literal
  into a VARCHAR2 param (`String.valueOf(1)`).
- `e-collection.sql` + `e.yaml` — case **e** (E-class): `v_nums :=
  t_num_table()` is a `TYPE ... IS TABLE OF NUMBER` constructor (→
  `new java.util.ArrayList<Long>()`); `FOR v_i IN 1 .. v_item.item_count`
  has a record-field (`Object`) upper bound and a boxed `Long` counter
  (bounds coerced via null-safe `Optional...orElse(0L)` / `1L`).
- `b-xml.sql` + `bxml.yaml` — case **b-xml** (B-XMLDOM): unresolved
  `DBE_XMLDOM.*` calls must degrade to `/* TOBEFIX: unresolved fn ... */ null`
  instead of a phantom `this.<fn>` self-call (the builtin prefix check now
  runs before the package `ends_with` self-match).
- `b-xml-schema-prefixed.sql` + `bxml-schema.yaml` — 附带发现: `END
  REPRO.PKG_XMLDOM;` (schema-qualified END name) defeats the ogsql-parser
  package-body parser → 0 packages / skeleton output. flux-gauss now emits a
  WARNING in the conversion report; the parser-side fix is tracked
  separately (see issue #136).

```bash
cd tests/regress/fixtures/issue136
../../../../target/debug/fluxgauss -c d.yaml
../../../../target/debug/fluxgauss -c e.yaml
../../../../target/debug/fluxgauss -c bxml.yaml
../../../../target/debug/fluxgauss -c bxml-schema.yaml
(cd out-d && mvn -B -ntp -Duser.language=en clean compile)            # BUILD SUCCESS
(cd out-e && mvn -B -ntp -Duser.language=en clean compile)            # BUILD SUCCESS
(cd out-bxml && mvn -B -ntp -Duser.language=en clean compile)         # BUILD SUCCESS
# bxml-schema: skeleton output + "0 packages" WARNING in conversion report
```

Expected generated shapes:

- d: `vCount = Long.parseLong(String.valueOf(this.fncText("1")));` and
  `this.fncText(String.valueOf(1))`.
- e: `vNums = new java.util.ArrayList<Long>();` and
  `for (vI = 1L; vI <= java.util.Optional.ofNullable((Number) (vItem.getOrDefault(...))).map(Number::longValue).orElse(0L); vI++)`.
- b-xml: `vValue = /* TOBEFIX: unresolved fn GETCHILDNODES(pNode) - pkg=DBE_XMLDOM, ... */ null;`
