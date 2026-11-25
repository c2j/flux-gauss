CREATE OR REPLACE PACKAGE PKG_D_COERCION AS
  FUNCTION fnc_text(p_value IN VARCHAR2) RETURN VARCHAR2;
  FUNCTION fnc_decimal(p_value IN VARCHAR2) RETURN NUMBER;
  PROCEDURE prc_assign(p_number IN NUMBER);
END PKG_D_COERCION;
/

CREATE OR REPLACE PACKAGE BODY PKG_D_COERCION AS
  FUNCTION fnc_text(p_value IN VARCHAR2) RETURN VARCHAR2 IS
  BEGIN
    RETURN p_value;
  END fnc_text;

  FUNCTION fnc_decimal(p_value IN VARCHAR2) RETURN NUMBER IS
  BEGIN
    RETURN TO_NUMBER(p_value);
  END fnc_decimal;

  PROCEDURE prc_assign(p_number IN NUMBER) IS
    v_decimal NUMERIC(18, 2);
    v_from_number NUMERIC(18, 2);
    v_count NUMBER;
    v_text VARCHAR2(32);
  BEGIN
    v_decimal := '1.25';
    v_from_number := p_number;
    v_from_number := fnc_decimal('1');
    v_count := 1;
    v_count := fnc_text('1');
    v_decimal := v_count * v_decimal;
    v_text := v_count;
    v_text := fnc_text(1);
    v_decimal := java.math.BigDecimal.valueOf(v_text);
  END prc_assign;
END PKG_D_COERCION;
/
