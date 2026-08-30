-- Issue #75: a bare top-level RETURN; followed by unreachable blocks must
-- not leave orphan `}` closers behind (brace imbalance). The dead-code
-- strip must drop trailing code AND its closing braces together.
CREATE OR REPLACE PROCEDURE p_issue75_dead_return(p_o_succeed OUT VARCHAR2)
IS
    v_step_no VARCHAR2(10) := '';
BEGIN
    p_o_succeed := '0';
    v_step_no := '1';
    RETURN;

    -- Unreachable code below: BEGIN...EXCEPTION...END + loop
    BEGIN
        DELETE FROM t_issue75 WHERE id = 1;
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            p_o_succeed := '删除失败';
    END;

    FOR i IN 1 .. 3 LOOP
        INSERT INTO t_issue75 (id) VALUES (i);
    END LOOP;
END;
