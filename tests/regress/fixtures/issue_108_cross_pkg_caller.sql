CREATE OR REPLACE PROCEDURE prc_call_cross_pkg(p_i_date VARCHAR2)
IS
  v_decl_bonus NUMBER := fn_calc_bonus(12000, 0.10, 2);
  v_bonus      NUMBER;
  v_name       VARCHAR2(64);
  v_sum        NUMBER := 0;
BEGIN
  v_bonus := fn_calc_bonus(15000, 0.15, 3);
  prc_get_emp_name(1002, v_name);
  FOR i IN 1..10 LOOP
    v_sum := v_sum + i;
  END LOOP;
  FOR i IN 1..20 LOOP
    v_sum := v_sum + i;
  END LOOP;
END;
/
