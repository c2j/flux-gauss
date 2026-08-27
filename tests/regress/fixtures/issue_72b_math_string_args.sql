CREATE OR REPLACE PACKAGE pkg_issue72b IS
    PROCEDURE prc_math_wall(p_o_succeed OUT VARCHAR2);
END pkg_issue72b;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue72b IS
    PROCEDURE prc_math_wall(p_o_succeed OUT VARCHAR2) IS
        v_flag VARCHAR2(8);
        v_abs  NUMBER(38);
        v_rnd  NUMBER(38);
    BEGIN
        v_flag := '-5';
        v_abs := ABS(v_flag);
        v_rnd := ROUND(v_flag);
        p_o_succeed := '0';
    END;
END pkg_issue72b;
/
