# issue #136 regression fixtures (batch 1: A + G root causes)

Minimal repros for the NULL/primitive-default-arg and cross-package arity
defects fixed in `fix/issue-136-a-g`.

## Layout

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

`fluxgauss.yaml` `sources` paths resolve relative to the **current working
directory**, so run from this directory and override `output_dir` with `-o`
(to keep the fixture dir clean):

```bash
cd tests/regress/fixtures/issue136
# case a uses schema-qualified standalone functions (REPRO.*) which the
# validator rejects — pass --skip-validate (same as the original repro).
../../../../target/debug/fluxgauss -c a.yaml --skip-validate -o /tmp/issue136-a
../../../../target/debug/fluxgauss -c g.yaml -o /tmp/issue136-g
(cd /tmp/issue136-a && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
(cd /tmp/issue136-g && mvn -B -ntp -Duser.language=en clean compile)   # BUILD SUCCESS
```

Expected generated call shapes:

- a: `this.fncCalc(1, null, 0L)` — `DEFAULT NULL` param stays `null` (boxed
  `Long`), `DEFAULT 0` params pad `0L` (primitive `long`); `if (true)` for
  `p_value != NULL`; `BigDecimal.valueOf(this.fncCalc(...))` for the
  `Long`→`BigDecimal` assignment.
- g: `gTargetService.fncWithDefaults("a", "b", "")` — relaxed arity match +
  `DEFAULT ''` padding.
