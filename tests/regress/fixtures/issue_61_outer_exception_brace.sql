-- ============================================================
-- Regression fixture for Issue #61 (R3-B)
-- Outer EXCEPTION WHEN no_data_found + WHEN OTHERS with nested
-- BEGIN-EXCEPTION in ELSIF branches — extra } between catches
-- ============================================================

CREATE TABLE t_issue61_bond (
    security_id   VARCHAR(20),
    market_type   VARCHAR(10),
    market_price  NUMERIC
);

CREATE OR REPLACE PACKAGE pkg_issue61_exc AS
    PROCEDURE prc_get_zs_price(
        p_i_security_id  IN  VARCHAR,
        p_i_market_type  IN  VARCHAR,
        p_o_market_price OUT NUMERIC,
        p_o_pass_flag    OUT VARCHAR,
        p_o_succeed      OUT VARCHAR
    );
END pkg_issue61_exc;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue61_exc AS

    PROCEDURE prc_get_zs_price(
        p_i_security_id  IN  VARCHAR,
        p_i_market_type  IN  VARCHAR,
        p_o_market_price OUT NUMERIC,
        p_o_pass_flag    OUT VARCHAR,
        p_o_succeed      OUT VARCHAR
    ) IS
    BEGIN
        IF p_i_market_type = 'A' THEN
            BEGIN
                SELECT t.market_price INTO p_o_market_price
                  FROM t_issue61_bond t
                 WHERE t.security_id = p_i_security_id
                   AND t.market_type = 'A';
            EXCEPTION
                WHEN OTHERS THEN
                    p_o_market_price := 0;
            END;
        ELSIF p_i_market_type = 'B' THEN
            BEGIN
                SELECT t.market_price INTO p_o_market_price
                  FROM t_issue61_bond t
                 WHERE t.security_id = p_i_security_id
                   AND t.market_type = 'B';
            EXCEPTION
                WHEN OTHERS THEN
                    p_o_market_price := 0;
            END;
        ELSE
            BEGIN
                SELECT t.market_price INTO p_o_market_price
                  FROM t_issue61_bond t
                 WHERE t.security_id = p_i_security_id;
            EXCEPTION
                WHEN OTHERS THEN
                    p_o_market_price := 0;
            END;
        END IF;

        p_o_pass_flag := 'F';
        p_o_succeed := '0';

    EXCEPTION
        WHEN no_data_found THEN
            p_o_market_price := 0;
        WHEN OTHERS THEN
            IF p_o_succeed = '0' THEN
                p_o_succeed := '取' || p_i_security_id || '中证行情价格子模块内部错误';
            ELSIF p_o_succeed = '1' THEN
                p_o_succeed := 'WARN:' || p_i_security_id;
            ELSE
                p_o_succeed := 'OTHER_ERR';
            END IF;
            RAISE;
    END;

END pkg_issue61_exc;
/
