-- ============================================================
-- Regression fixture for Issue #63 (R3-E)
-- FUNCTION RETURN VARCHAR2 must map to Java String, not Long
-- ============================================================

CREATE TABLE t_issue63_fund (
    security_id   VARCHAR(20),
    market_price  VARCHAR(200),
    bonus_value   VARCHAR(200)
);

CREATE OR REPLACE PACKAGE pkg_issue63_ret AS
    FUNCTION fnc_get_open_fund_value(
        p_i_date        VARCHAR2,
        p_i_price_date  VARCHAR2,
        p_i_security_id VARCHAR2
    ) RETURN VARCHAR2;

    FUNCTION fnc_trd_get_unit_cash(
        p_i_date VARCHAR2
    ) RETURN VARCHAR2;

    -- Control: numeric return must stay numeric
    FUNCTION fnc_get_price_num(
        p_i_id VARCHAR2
    ) RETURN NUMBER;
END pkg_issue63_ret;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue63_ret AS

    FUNCTION fnc_get_open_fund_value(
        p_i_date        VARCHAR2,
        p_i_price_date  VARCHAR2,
        p_i_security_id VARCHAR2
    ) RETURN VARCHAR2 IS
        v_market_price VARCHAR2(200);
        v_bonus_value  VARCHAR2(200);
    BEGIN
        v_market_price := '';
        v_bonus_value  := '0';
        BEGIN
            SELECT t.market_price, t.bonus_value
              INTO v_market_price, v_bonus_value
              FROM t_issue63_fund t
             WHERE t.security_id = p_i_security_id;
        EXCEPTION
            WHEN OTHERS THEN
                v_market_price := '0';
                v_bonus_value  := '0';
        END;
        v_market_price := v_market_price || v_bonus_value;
        RETURN v_market_price;
    END;

    FUNCTION fnc_trd_get_unit_cash(
        p_i_date VARCHAR2
    ) RETURN VARCHAR2 IS
        p_o_unit_cash VARCHAR2(200);
    BEGIN
        p_o_unit_cash := 'CASH_' || p_i_date;
        RETURN p_o_unit_cash;
    END;

    FUNCTION fnc_get_price_num(
        p_i_id VARCHAR2
    ) RETURN NUMBER IS
        v_total NUMBER := 0;
    BEGIN
        v_total := COALESCE(v_total, 0) + 1;
        RETURN COALESCE(v_total, 0);
    END;

END pkg_issue63_ret;
/