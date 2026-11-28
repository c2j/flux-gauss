CREATE OR REPLACE PACKAGE pkg_issue144xb_helper IS
    PROCEDURE helper(p_i_in IN NUMBER, p_o_out OUT VARCHAR2, p_o_flag OUT NUMBER);
END pkg_issue144xb_helper;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144xb_helper IS
    PROCEDURE helper(p_i_in IN NUMBER, p_o_out OUT VARCHAR2, p_o_flag OUT NUMBER) IS
    BEGIN
        IF p_i_in > 0 THEN
            p_o_out := 'ok';
            p_o_flag := 1;
        ELSE
            p_o_out := 'empty';
            p_o_flag := 0;
        END IF;
    END;
END pkg_issue144xb_helper;
/

CREATE OR REPLACE PACKAGE pkg_issue144xb IS
    PROCEDURE prc_xblock(p_i_id IN NUMBER, p_o_msg IN OUT VARCHAR2);
END pkg_issue144xb;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144xb IS
    -- Issue #144 batch 1a: an OUT-carrier local (v_retmsg/v_flag — promoted to
    -- AtomicReference by the cross-package OUT-arg call) is assigned inside the
    -- first nested BEGIN block but referenced again from a SIBLING block and
    -- from the EXCEPTION branch. In PL/SQL the declaration is procedure-scoped
    -- and every reference is legal; the generated Java must keep the
    -- AtomicReference declaration method-scoped (hoisted) too — block-scoped
    -- decls make the sibling/catch references compile-error ("cannot find
    -- symbol").
    PROCEDURE prc_xblock(p_i_id IN NUMBER, p_o_msg IN OUT VARCHAR2) IS
        v_retmsg VARCHAR2(500);
        v_flag   NUMBER;
    BEGIN
        BEGIN
            v_retmsg := NULL;
            v_flag := 0;
            IF p_i_id > 0 THEN
                pkg_issue144xb_helper.helper(p_i_id, v_retmsg, v_flag);
            END IF;
        END;
        -- sibling block: the carriers must still be visible here
        pkg_issue144xb_helper.helper(p_i_id, v_retmsg, v_flag);
        p_o_msg := v_retmsg;
    EXCEPTION
        WHEN OTHERS THEN
            p_o_msg := v_retmsg;
    END;
END pkg_issue144xb;
/
