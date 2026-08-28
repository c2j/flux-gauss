CREATE OR REPLACE FUNCTION FNC_GET_PURCHASE_JS_DAYS (p_i_date VARCHAR2, p_i_security_id VARCHAR2)
  RETURN VARCHAR2 IS
  v_purchase_js_date VARCHAR2(8);
BEGIN
  v_purchase_js_date := fnc_com_getday(substr(p_i_security_id, 3, 3), p_i_date, 1);
  RETURN v_purchase_js_date;
END;
/
