CREATE OR REPLACE PROCEDURE REPRO.PRC_CHECK_NULL(p_value IN NUMBER) IS
  v_result NUMERIC(18, 4);
  v_timeout NUMBER;
BEGIN
  v_result := fnc_calc(1);
  v_timeout := NVL(p_value, 3600);
  IF p_value != NULL THEN
    v_result := v_result + 1;
  END IF;
END PRC_CHECK_NULL;
/
