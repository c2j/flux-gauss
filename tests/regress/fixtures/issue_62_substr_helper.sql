-- ============================================================
-- Regression fixture for Issue #62 (R3-D)
-- SUBSTR should use _substr helper, not inline Math.min/max
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue62_substr AS
    PROCEDURE proc_substr_basic(
        p_id     IN VARCHAR,
        p_result OUT VARCHAR
    );
END pkg_issue62_substr;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue62_substr AS

    PROCEDURE proc_substr_basic(
        p_id     IN VARCHAR,
        p_result OUT VARCHAR
    ) IS
        v_part VARCHAR(50);
    BEGIN
        v_part := substr(p_id, 3, 3);
        p_result := substr(p_id, 1, 2) || '-' || v_part;
        p_result := p_result || substr(p_id, 6);
    END;

END pkg_issue62_substr;
/
