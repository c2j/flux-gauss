CREATE OR REPLACE PACKAGE pkg_callee IS
    PROCEDURE plan_increment(p_source IN VARCHAR2, p_id_from OUT NUMBER, p_id_to OUT NUMBER);
    PROCEDURE build_incremental(p_run_id IN VARCHAR2, p_rows OUT NUMBER, p_months OUT NUMBER);
END pkg_callee;
/

CREATE OR REPLACE PACKAGE BODY pkg_callee IS
    PROCEDURE plan_increment(p_source IN VARCHAR2, p_id_from OUT NUMBER, p_id_to OUT NUMBER) IS
    BEGIN
        NULL;
    END;

    PROCEDURE build_incremental(p_run_id IN VARCHAR2, p_rows OUT NUMBER, p_months OUT NUMBER) IS
    BEGIN
        NULL;
    END;
END pkg_callee;
/

CREATE OR REPLACE PACKAGE pkg_caller IS
    PROCEDURE run_daily(p_run_id IN VARCHAR2);
END pkg_caller;
/

CREATE OR REPLACE PACKAGE BODY pkg_caller IS
    PROCEDURE run_daily(p_run_id IN VARCHAR2) IS
        v_rows NUMBER;
        v_months NUMBER;
    BEGIN
        pkg_callee.build_incremental(p_run_id, v_rows, v_months);
        IF v_rows > 0 THEN
            NULL;
        END IF;
        pkg_callee.plan_increment('x', v_rows, v_months);
    END;
END pkg_caller;
/
