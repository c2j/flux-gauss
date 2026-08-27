CREATE OR REPLACE PACKAGE pkg_issue72 IS
    PROCEDURE prc_type_wall(p_i_date IN VARCHAR2, p_o_succeed OUT VARCHAR2);
    PROCEDURE prc_str_method_wall(p_in IN VARCHAR2, p_o_succeed OUT VARCHAR2);
END pkg_issue72;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue72 IS
    PROCEDURE prc_type_wall(p_i_date IN VARCHAR2, p_o_succeed OUT VARCHAR2) IS
        v_count NUMBER(38);
        v_amt   NUMBER(18,2);
        v_flag  VARCHAR2(8);
    BEGIN
        v_flag := '1';
        v_count := v_flag;
        v_amt := v_flag * 0.5;
        IF v_count > 0 THEN
            p_o_succeed := '0';
        END IF;
    END;

    PROCEDURE prc_str_method_wall(p_in IN VARCHAR2, p_o_succeed OUT VARCHAR2) IS
        v_len  NUMBER(38);
        v_str  VARCHAR2(64);
        v_next NUMBER(38);
    BEGIN
        v_str  := 'abc';
        v_len  := LENGTH(p_in);
        v_next := LENGTH(v_str) + 1;
        IF v_len > 0 AND v_next > 0 THEN
            p_o_succeed := '0';
        END IF;
    END;
END pkg_issue72;
/
