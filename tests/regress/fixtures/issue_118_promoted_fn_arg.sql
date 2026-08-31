CREATE OR REPLACE PACKAGE pkg_issue118_callee IS
    FUNCTION fill_name(p_id IN NUMBER, p_name OUT VARCHAR2) RETURN NUMBER;
END pkg_issue118_callee;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue118_callee IS
    FUNCTION fill_name(p_id IN NUMBER, p_name OUT VARCHAR2) RETURN NUMBER IS
    BEGIN
        p_name := 'alice';
        RETURN p_id;
    END;
END pkg_issue118_callee;
/

CREATE OR REPLACE PACKAGE pkg_issue118_caller IS
    PROCEDURE consume_name;
END pkg_issue118_caller;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue118_caller IS
    PROCEDURE consume_name IS
        v_result NUMBER;
        v_name   VARCHAR2(64);
        v_upper  VARCHAR2(64);
        v_length NUMBER;
    BEGIN
        v_result := pkg_issue118_callee.fill_name(1, v_name);
        v_upper := UPPER(v_name);
        v_length := LENGTH(v_name);
    END;
END pkg_issue118_caller;
/
