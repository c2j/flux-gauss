-- Guard SQL fixture for OUT promotion regression tests.
-- This file exercises every AST nesting path that requires OUT parameter promotion.
-- Conversion should produce compilable Java with correct AtomicReference types and preserved defaults.

CREATE OR REPLACE PACKAGE pkg_out_promo_guard IS

  -- Helper: called by all test cases, has OUT params at various positions
  PROCEDURE helper_with_out(
    p_in    IN  VARCHAR2,
    p_flag  OUT NUMBER,
    p_msg   OUT VARCHAR2
  );

  -- Guard 1: top-level call promotes local vars (baseline)
  PROCEDURE guard_top_level;

  -- Guard 2: call inside IF then_stmts → outer_msg must be promoted
  PROCEDURE guard_if_then;

  -- Guard 3: call inside IF else_stmts → else_var must be promoted
  PROCEDURE guard_if_else;

  -- Guard 4: call inside IF elsifs → elsif_var must be promoted
  PROCEDURE guard_if_elsif;

  -- Guard 5: call inside CASE WHEN → case_var must be promoted
  PROCEDURE guard_case_when;

  -- Guard 6: call inside WHILE body → while_var must be promoted
  PROCEDURE guard_while;

  -- Guard 7: call inside FOR body → for_var must be promoted
  PROCEDURE guard_for;

  -- Guard 8: call inside LOOP body → loop_var must be promoted
  PROCEDURE guard_loop;

  -- Guard 9: call inside exception handler → err_var must be promoted
  PROCEDURE guard_exception;

  -- Guard 10: IF → WHILE nesting (the real PKG_2008802001_MGT pattern)
  --           outer_msg must be promoted even though it only appears inside IF→WHILE
  PROCEDURE guard_if_while_nest;

  -- Guard 11: default values preserved after promotion
  --           v_str should keep '"hello"', v_num should keep '0'
  PROCEDURE guard_defaults_preserved;

END pkg_out_promo_guard;
/

CREATE OR REPLACE PACKAGE BODY pkg_out_promo_guard IS

  PROCEDURE helper_with_out(
    p_in    IN  VARCHAR2,
    p_flag  OUT NUMBER,
    p_msg   OUT VARCHAR2
  ) IS
  BEGIN
    p_flag := 0;
    p_msg  := 'OK:' || p_in;
  END;

  -- Guard 1: baseline
  PROCEDURE guard_top_level IS
    v_flag NUMBER;
    v_msg  VARCHAR2(100);
  BEGIN
    helper_with_out('top', v_flag, v_msg);
  END;

  -- Guard 2: IF then_stmts
  PROCEDURE guard_if_then IS
    v_flag NUMBER;
    v_msg  VARCHAR2(100);
    outer_msg VARCHAR2(30) := 'init';
  BEGIN
    IF outer_msg IS NOT NULL THEN
      helper_with_out('if_then', v_flag, outer_msg);
    END IF;
  END;

  -- Guard 3: IF else_stmts
  PROCEDURE guard_if_else IS
    v_flag NUMBER;
    else_var VARCHAR2(30);
  BEGIN
    IF 1 = 0 THEN
      NULL;
    ELSE
      helper_with_out('if_else', v_flag, else_var);
    END IF;
  END;

  -- Guard 4: IF elsifs
  PROCEDURE guard_if_elsif IS
    v_flag NUMBER;
    elsif_var VARCHAR2(30);
    v_code VARCHAR2(10) := 'B';
  BEGIN
    IF v_code = 'A' THEN
      NULL;
    ELSIF v_code = 'B' THEN
      helper_with_out('elsif', v_flag, elsif_var);
    END IF;
  END;

  -- Guard 5: CASE WHEN
  PROCEDURE guard_case_when IS
    v_flag NUMBER;
    case_var VARCHAR2(30);
    v_code VARCHAR2(10) := 'X';
  BEGIN
    CASE v_code
      WHEN 'X' THEN
        helper_with_out('case_x', v_flag, case_var);
      ELSE
        NULL;
    END CASE;
  END;

  -- Guard 6: WHILE
  PROCEDURE guard_while IS
    v_flag NUMBER;
    while_var VARCHAR2(30);
    v_cnt NUMBER := 0;
  BEGIN
    WHILE v_cnt < 3 LOOP
      helper_with_out('while_' || TO_CHAR(v_cnt), v_flag, while_var);
      v_cnt := v_cnt + 1;
    END LOOP;
  END;

  -- Guard 7: FOR
  PROCEDURE guard_for IS
    v_flag NUMBER;
    for_var VARCHAR2(30);
  BEGIN
    FOR i IN 1..3 LOOP
      helper_with_out('for_' || TO_CHAR(i), v_flag, for_var);
    END LOOP;
  END;

  -- Guard 8: LOOP
  PROCEDURE guard_loop IS
    v_flag NUMBER;
    loop_var VARCHAR2(30);
    v_cnt NUMBER := 0;
  BEGIN
    LOOP
      helper_with_out('loop', v_flag, loop_var);
      v_cnt := v_cnt + 1;
      EXIT WHEN v_cnt >= 3;
    END LOOP;
  END;

  -- Guard 9: exception handler
  PROCEDURE guard_exception IS
    v_flag NUMBER;
    err_var VARCHAR2(200);
  BEGIN
    NULL;
  EXCEPTION
    WHEN OTHERS THEN
      helper_with_out('err', v_flag, err_var);
  END;

  -- Guard 10: IF → WHILE (reproduces PKG_2008802001_MGT pattern)
  PROCEDURE guard_if_while_nest IS
    v_date VARCHAR2(30);
    outer_msg VARCHAR2(30) := '2';
    another_outer_msg VARCHAR2(30) := '2';
    v_flag NUMBER;
  BEGIN
    -- First call at top level — promotes another_outer_msg
    helper_with_out('first', v_flag, another_outer_msg);

    -- Second call inside IF → WHILE — must also promote outer_msg
    IF v_date IS NOT NULL THEN
      WHILE v_date IS NOT NULL LOOP
        helper_with_out('nested', v_flag, outer_msg);
      END LOOP;
    END IF;
  END;

  -- Guard 11: default values must survive promotion
  PROCEDURE guard_defaults_preserved IS
    v_flag NUMBER;
    v_str  VARCHAR2(30) := 'hello';
    v_num  NUMBER := 0;
  BEGIN
    helper_with_out('defaults', v_flag, v_str);
    helper_with_out('defaults', v_flag, v_num);
  END;

END pkg_out_promo_guard;
/
