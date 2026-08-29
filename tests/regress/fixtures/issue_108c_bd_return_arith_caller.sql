CREATE OR REPLACE PROCEDURE prc_bd_return_arith(p_i_id INTEGER)
IS
  v_threshold NUMERIC(18,2);
BEGIN
  v_threshold := fn_avg_amount(p_i_id) * 1.2;
END;
/
