-- ============================================================
-- Regression fixture for Issue #60 (R3-A/C)
-- INSTR / CASE WHEN 0 → indexOf()+1.equals(0) precedence bug
-- ============================================================

CREATE OR REPLACE PACKAGE pkg_issue60_instr AS
    PROCEDURE proc_case_instr_when_zero(
        p_file_name IN VARCHAR,
        p_result    OUT VARCHAR
    );
    PROCEDURE proc_instr_eq_zero(
        p_src    IN VARCHAR,
        p_found  OUT VARCHAR
    );
END pkg_issue60_instr;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue60_instr AS

    PROCEDURE proc_case_instr_when_zero(
        p_file_name IN VARCHAR,
        p_result    OUT VARCHAR
    ) IS
        v_file_name VARCHAR(200);
        v_len       INTEGER;
    BEGIN
        v_file_name := p_file_name;
        -- Pattern A: CASE INSTR WHEN 0 inside SUBSTR length arg
        v_file_name := substr(v_file_name,
                              3,
                              CASE instr(v_file_name, '_')
                                WHEN 0 THEN
                                 instr(v_file_name, '.dbf') - 3
                                ELSE
                                 instr(v_file_name, '_') - 3
                              END);
        p_result := v_file_name;
        v_len := CASE instr(v_file_name, '.')
                   WHEN 0 THEN 0
                   ELSE instr(v_file_name, '.')
                 END;
        IF v_len > 0 THEN
            p_result := p_result || '_ok';
        END IF;
    END;

    PROCEDURE proc_instr_eq_zero(
        p_src    IN VARCHAR,
        p_found  OUT VARCHAR
    ) IS
    BEGIN
        IF instr(lower(p_src), 'ipogh') = 0 THEN
            p_found := 'N';
        ELSE
            p_found := 'Y';
        END IF;
        IF instr(p_src, '_') > 0 THEN
            p_found := p_found || 'U';
        END IF;
    END;

END pkg_issue60_instr;
/
