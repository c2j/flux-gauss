-- =====================================================================
-- Bug verification test cases
-- =====================================================================

-- Bug 1: Same-package variable referenced with full-qualified name
CREATE OR REPLACE PACKAGE pkg_bug_test AS
    out_err_msg VARCHAR2(400);
    PROCEDURE prc_test_exception(p_i_id BIGINT, p_o_errmsg OUT VARCHAR2, p_o_succeed OUT VARCHAR2);
    PROCEDURE prc_test_substr_instr(packg_proc_func VARCHAR2);
END pkg_bug_test;
/

CREATE OR REPLACE PACKAGE BODY pkg_bug_test AS

PROCEDURE prc_test_exception(p_i_id BIGINT, p_o_errmsg OUT VARCHAR2, p_o_succeed OUT VARCHAR2) IS
    v_proc_name VARCHAR2(100);
    v_step_no   INTEGER;
BEGIN
    v_proc_name := 'test';
    v_step_no := 1;

    UPDATE t_orders SET status = 'DONE' WHERE id = p_i_id;

    COMMIT;
    p_o_succeed := '0';
EXCEPTION
    WHEN OTHERS THEN
        ROLLBACK;
        -- Reference package variable with full qualified name
        p_o_errmsg := p_o_errmsg || pkg_bug_test.out_err_msg;
        p_o_succeed := '1';
END;

-- Bug 2: SUBSTR with complex expression + INSTR with 3/4 args
PROCEDURE prc_test_substr_instr(packg_proc_func VARCHAR2) IS
    v_query_part_swh  VARCHAR2(50);
    v_packg_name      VARCHAR2(100);
    v_proc_func_name  VARCHAR2(100);
    v_packg_proc_func VARCHAR2(200);
    v_dot_pos         NUMBER;
BEGIN
    -- Bug 2A: SUBSTR with concat expression as first arg
    v_query_part_swh := SUBSTR('PKG_MK_SWH_' || UPPER(packg_proc_func), 1, 40);

    -- Bug 2B: INSTR and SUBSTR with variable start
    v_packg_proc_func := packg_proc_func;
    v_dot_pos := INSTR(v_packg_proc_func, '.', 1, 1);
    v_packg_name := SUBSTR(v_packg_proc_func, 1, v_dot_pos - 1);
    v_proc_func_name := SUBSTR(v_packg_proc_func, v_dot_pos + 1);

    INSERT INTO t_log(id, msg) VALUES(1, v_packg_name || '.' || v_proc_func_name);
END;

END pkg_bug_test;
