-- ============================================================
-- Issue #44 regression: nested Block.exception_block loss
-- ============================================================
-- The _process_statement Block case (L2744) processes
-- declarations + body but ignores exception_block.
-- Nested BEGIN...EXCEPTION...END inside IF branches lose
-- their handlers. This fixture isolates that pattern.
-- ============================================================

CREATE TABLE t_issue44_config (
    config_key   VARCHAR(200) PRIMARY KEY,
    config_value VARCHAR(4000),
    status       VARCHAR(1)
);

CREATE TABLE t_issue44_log (
    proc_name VARCHAR(200),
    log_date  VARCHAR(8),
    step_no   VARCHAR(10),
    info      VARCHAR(200)
);

CREATE OR REPLACE PACKAGE pkg_issue44_nested_exc AS
    PROCEDURE proc_nested_exception_blocks;
END pkg_issue44_nested_exc;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue44_nested_exc AS

    PROCEDURE proc_nested_exception_blocks IS
        v_count   INTEGER;
        v_flag    VARCHAR(1);
        v_flag2   VARCHAR(1);
        v_msg     VARCHAR(4000);
        v_config  VARCHAR(4000);
        v_date    VARCHAR(8) := to_char(now(), 'YYYYMMDD');
    BEGIN
        v_flag := '0';
        v_msg  := 'success';

        SELECT COUNT(1) INTO v_count
          FROM t_issue44_config
         WHERE status = '1';

        -- BUG #1: Block with exception_block inside IF — handler LOST
        IF v_count > 0 THEN
            BEGIN
                SELECT config_value INTO v_config
                  FROM t_issue44_config
                 WHERE config_key = 'split_pkg_code';

                IF v_flag = '0' THEN
                    v_config := v_config || ',extra';

                    -- BUG #2: nested Block with exception_block inside
                    -- anonymous BEGIN...END — handler LOST
                    BEGIN
                        BEGIN
                            SELECT '1' INTO v_flag2
                              FROM t_issue44_config
                             WHERE EXISTS (
                                 SELECT 1 FROM t_issue44_log
                                  WHERE proc_name = 'test'
                                    AND log_date = v_date
                                    AND step_no = '9'
                             );
                        EXCEPTION
                            WHEN OTHERS THEN
                                v_flag2 := '0';
                        END;

                        IF v_flag2 = '0' THEN
                            v_msg := 'log_written_first_time';
                        END IF;
                    END;
                END IF;

            EXCEPTION
                WHEN OTHERS THEN
                    v_msg := 'inner_block_error';
            END;
        END IF;

    EXCEPTION
        WHEN OTHERS THEN
            v_msg := 'outer_error:' || SQLERRM;
    END;

END pkg_issue44_nested_exc;
/
