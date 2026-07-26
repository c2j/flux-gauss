-- ============================================================
-- Regression fixture for Issue #56 (RETURN in EXCEPTION handler
-- makes subsequent WHEN handlers unreachable)
-- ============================================================

CREATE TABLE t_issue56_data (
    data_key   VARCHAR(100) PRIMARY KEY,
    data_value VARCHAR(4000)
);

CREATE OR REPLACE PACKAGE pkg_issue56_return_handler AS
    PROCEDURE proc_two_handlers_first_returns(p_result OUT VARCHAR);
    PROCEDURE proc_nested_block_then_return(p_result OUT VARCHAR);
    PROCEDURE proc_three_handlers_returns(p_result OUT VARCHAR);
END pkg_issue56_return_handler;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue56_return_handler AS

    PROCEDURE proc_two_handlers_first_returns(p_result OUT VARCHAR) IS
        v_data VARCHAR(4000);
    BEGIN
        SELECT data_value INTO v_data
          FROM t_issue56_data
         WHERE data_key = 'key1';
        p_result := v_data;
    EXCEPTION
        WHEN no_data_found THEN
            p_result := 'not_found';
            RETURN;
        WHEN OTHERS THEN
            p_result := 'error';
            RETURN;
    END;

    PROCEDURE proc_nested_block_then_return(p_result OUT VARCHAR) IS
        v_data  VARCHAR(4000);
        v_tmp   VARCHAR(4000);
    BEGIN
        SELECT data_value INTO v_data
          FROM t_issue56_data
         WHERE data_key = 'key1';
        IF v_data IS NULL THEN
            v_data := 'default';
        END IF;
        p_result := v_data;
    EXCEPTION
        WHEN no_data_found THEN
            BEGIN
                SELECT data_value INTO v_tmp
                  FROM t_issue56_data
                 WHERE data_key = 'fallback';
                p_result := v_tmp;
            EXCEPTION
                WHEN OTHERS THEN
                    p_result := 'nested_fallback_failed';
            END;
            RETURN;
        WHEN OTHERS THEN
            p_result := 'outer_error';
            RETURN;
    END;

    PROCEDURE proc_three_handlers_returns(p_result OUT VARCHAR) IS
        v_data VARCHAR(4000);
    BEGIN
        SELECT data_value INTO v_data
          FROM t_issue56_data
         WHERE data_key = 'key1';
        p_result := v_data;
    EXCEPTION
        WHEN no_data_found THEN
            p_result := 'not_found';
            RETURN;
        WHEN too_many_rows THEN
            p_result := 'too_many';
            RETURN;
        WHEN OTHERS THEN
            p_result := 'error';
            RETURN;
    END;

END pkg_issue56_return_handler;
/
