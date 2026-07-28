-- ============================================================
-- Regression fixture for Issue #64 (R3-F)
-- Empty string '' init on numeric-mapped vars must not emit BigDecimal x = ""
-- ============================================================

CREATE TABLE t_issue64_price (
    security_id VARCHAR(20),
    price_type  VARCHAR(20),
    market_price NUMERIC
);

CREATE OR REPLACE PACKAGE pkg_issue64_bd_init AS
    PROCEDURE proc_empty_string_defaults(
        p_id     IN VARCHAR,
        p_result OUT VARCHAR
    );
END pkg_issue64_bd_init;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue64_bd_init AS

    PROCEDURE proc_empty_string_defaults(
        p_id     IN VARCHAR,
        p_result OUT VARCHAR
    ) IS
        v_openfund_mode       VARCHAR2(200) := '';
        v_openfund_price_flag VARCHAR2(200) := '';
        v_price_type          VARCHAR2(200) := '';
        v_pg_price            VARCHAR2(200) := '';
    BEGIN
        v_openfund_mode := 'M';
        v_price_type := 'T';
        p_result := v_openfund_mode || v_openfund_price_flag || v_price_type || v_pg_price;
        IF p_id IS NOT NULL THEN
            p_result := p_result || p_id;
        END IF;
    END;

END pkg_issue64_bd_init;
/