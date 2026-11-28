CREATE OR REPLACE PACKAGE issue_144_stmt_call_out IS
    PROCEDURE prc_stmt_call_out;
    PROCEDURE prc_stmt_call_out_handler;
    PROCEDURE helper_proc(p_a IN VARCHAR2, p_b IN VARCHAR2, p_out OUT VARCHAR2);
    PROCEDURE helper_num(p_n IN NUMBER, p_out OUT NUMBER);
END issue_144_stmt_call_out;
/

CREATE OR REPLACE PACKAGE BODY issue_144_stmt_call_out IS
    -- Issue #144 batch 4b: statement-level procedure calls must apply the
    -- same OUT-carrier decision matrix as expression-context calls
    -- (_plan_out_arg_expr). Covers three residual shapes:
    --  a) OUT carrier passed bare (this.helperProc(..., vOut))
    --  b) TODO-comment commas inside args shifting OUT positions
    --     (/* TODO: implement f(...) — pkg=?, ret=?, args=[..], .. */)
    --  c) carrier inner-type mismatch (AR<String> var vs AR<Long> param)
    --  d) promoted carriers inside EXCEPTION-handler calls bypassing the
    --     post-pass .get() rewrite.
    PROCEDURE helper_proc(p_a IN VARCHAR2, p_b IN VARCHAR2, p_out OUT VARCHAR2) IS
    BEGIN
        p_out := p_a || '_' || p_b;
    END;

    PROCEDURE helper_num(p_n IN NUMBER, p_out OUT NUMBER) IS
    BEGIN
        p_out := p_n * 2;
    END;

    PROCEDURE prc_stmt_call_out IS
        v_out   VARCHAR2(100);
        v_out2  VARCHAR2(100);
        v_num   NUMBER;
        v_in    VARCHAR2(100) := 'x';
        v_cnt   NUMBER := 0;
    BEGIN
        -- same-package statement call, OUT carrier must be passed as-is
        helper_proc(v_in, 'a', v_out);
        -- qualified same-package call
        issue_144_stmt_call_out.helper_proc(v_in, 'b', v_out2);
        -- OUT NUMBER carrier
        helper_num(v_cnt, v_num);
        -- carrier inner-type conflict: v_num is OUT to both a NUMBER callee
        -- (AR<Long>) and a VARCHAR2 callee (AR<String>) — last promotion wins,
        -- the other site needs a typed temp carrier + writeback
        helper_proc(v_in, 'c', v_num);
        -- TODO-comment arg containing commas must not shift OUT positions
        helper_proc(v_in, some_unknown_udf(v_in, v_in), v_out2);
        v_cnt := v_cnt + 1;
    END;

    PROCEDURE prc_stmt_call_out_handler IS
        v_out   VARCHAR2(100);
        v_in    VARCHAR2(100) := 'x';
    BEGIN
        helper_proc(v_in, 'h', v_out);
    EXCEPTION WHEN OTHERS THEN
        -- handler-emitted call: promoted carrier at IN position needs .get(),
        -- at OUT position stays a bare carrier
        helper_proc(v_out, 'e', v_out);
        -- bare assignment to a promoted carrier must re-wrap the RHS
        v_out := 'fb';
    END;
END issue_144_stmt_call_out;
/
