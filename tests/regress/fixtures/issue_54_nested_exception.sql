-- ============================================================
-- Regression fixture for Issue #54 (nested BEGIN-EXCEPTION 3+ deep)
-- ============================================================
-- Root cause: Python _wrap_handler_stmts drops nested exception_blocks
-- in handler bodies, and Rust process_with_goto_replace/process_cleanup_stmt
-- Block handlers ignore exception_block entirely.

CREATE TABLE t_issue54_data (
    data_key   VARCHAR(100) PRIMARY KEY,
    data_value VARCHAR(4000)
);

CREATE OR REPLACE PACKAGE pkg_issue54_nested_exc AS
    PROCEDURE proc_nested_block_in_handler(p_result OUT VARCHAR);
    PROCEDURE proc_three_level_nested(p_result OUT VARCHAR);
END pkg_issue54_nested_exc;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue54_nested_exc AS

    -- Pattern from Issue #54: handler body contains a Block with
    -- its own exception_block — inner try/catch was dropped.
    PROCEDURE proc_nested_block_in_handler(p_result OUT VARCHAR) IS
        v_msg   VARCHAR(4000);
        v_data  VARCHAR(4000);
    BEGIN
        SELECT data_value INTO v_data
          FROM t_issue54_data
         WHERE data_key = 'config';
        IF v_data IS NULL THEN
            v_msg := 'no_config';
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            -- Nested Block inside handler: must produce inner try/catch
            BEGIN
                SELECT data_value INTO v_data
                  FROM t_issue54_data
                 WHERE data_key = 'fallback';
                p_result := 'fallback_ok';
            EXCEPTION
                WHEN OTHERS THEN
                    p_result := 'fallback_failed';
            END;
    END;

    -- Issue #54 deep-nesting pattern: 3+ levels of BEGIN-EXCEPTION.
    PROCEDURE proc_three_level_nested(p_result OUT VARCHAR) IS
        v_val   VARCHAR(100);
        v_tmp   VARCHAR(100);
    BEGIN
        v_val := 'start';
        BEGIN
            SELECT data_value INTO v_tmp
              FROM t_issue54_data
             WHERE data_key = 'key1';
            v_val := v_tmp;
        EXCEPTION
            WHEN OTHERS THEN
                BEGIN
                    SELECT data_value INTO v_tmp
                      FROM t_issue54_data
                     WHERE data_key = 'key2';
                    v_val := v_tmp;
                EXCEPTION
                    WHEN OTHERS THEN
                        v_val := 'deepest_fallback';
                END;
        END;
        p_result := v_val;
    EXCEPTION
        WHEN OTHERS THEN
            p_result := 'outer_fallback';
    END;

END pkg_issue54_nested_exc;
/
