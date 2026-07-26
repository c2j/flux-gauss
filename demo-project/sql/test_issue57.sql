-- Regression test for Issue #57: empty string '' → NUMBER type
-- In GaussDB/openGauss, '' assigned to NUMBER is implicitly NULL.
-- The converter must emit `null` in Java, NOT Long.parseLong("") which throws NumberFormatException.

CREATE OR REPLACE PACKAGE pkg_issue57_test IS

  -- Test 1: direct assignment of empty string to NUMBER variable
  PROCEDURE test_empty_to_number;

  -- Test 2: empty string assigned to NUMBER OUT parameter
  PROCEDURE test_out_param(
    p_result OUT NUMBER
  );

  -- Test 3: empty string in no_data_found handler (real-world pattern)
  PROCEDURE test_no_data_found_pattern;

  -- Test 4: empty string assigned to NUMERIC (BigDecimal) variable
  PROCEDURE test_empty_to_bigdecimal;

END pkg_issue57_test;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue57_test IS

  PROCEDURE test_empty_to_number IS
    v_num NUMBER;
  BEGIN
    v_num := '';           -- empty string → NUMBER: should emit `null`, not Long.parseLong("")
    v_num := NULL;         -- explicit NULL → NUMBER: should emit `null` (baseline)
    v_num := 0;            -- numeric literal → NUMBER: should emit `0L` (baseline)
  END test_empty_to_number;

  PROCEDURE test_out_param(
    p_result OUT NUMBER
  ) IS
  BEGIN
    p_result := '';        -- empty string → OUT NUMBER: should emit `null`
  END test_out_param;

  PROCEDURE test_no_data_found_pattern IS
    v_delay_day_num NUMBER;
  BEGIN
    BEGIN
      -- simulate query that may return no rows
      v_delay_day_num := 1;
    EXCEPTION
      WHEN no_data_found THEN
        v_delay_day_num := '';   -- empty string in exception handler: should emit `null`
    END;
  END test_no_data_found_pattern;

  PROCEDURE test_empty_to_bigdecimal IS
    v_amount NUMERIC;
    v_rate   NUMBER(10, 4);
  BEGIN
    v_amount := '';        -- empty string → NUMERIC (BigDecimal): should emit `null`
    v_rate   := '';        -- empty string → NUMBER(p,s) (BigDecimal): should emit `null`
  END test_empty_to_bigdecimal;

END pkg_issue57_test;
/
