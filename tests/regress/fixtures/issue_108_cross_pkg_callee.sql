CREATE OR REPLACE FUNCTION fn_calc_bonus(p_base NUMBER, p_pct NUMBER, p_years NUMBER)
RETURN NUMBER IS
BEGIN
  RETURN p_base * p_pct * p_years;
END;
/

CREATE OR REPLACE PROCEDURE prc_get_emp_name(p_i_id NUMBER, p_o_name OUT VARCHAR2)
IS
BEGIN
  p_o_name := 'emp';
END;
/
