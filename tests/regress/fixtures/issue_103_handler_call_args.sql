CREATE OR REPLACE PACKAGE pkg_callee IS
    PROCEDURE simple_log(p_msg IN VARCHAR2);
    PROCEDURE simple_log(p_msg IN VARCHAR2, p_detail IN VARCHAR2);
END pkg_callee;
/

CREATE OR REPLACE PACKAGE BODY pkg_callee IS
    PROCEDURE simple_log(p_msg IN VARCHAR2) IS
    BEGIN
        NULL;
    END;

    PROCEDURE simple_log(p_msg IN VARCHAR2, p_detail IN VARCHAR2) IS
    BEGIN
        NULL;
    END;
END pkg_callee;
/

CREATE OR REPLACE PACKAGE pkg_handler IS
    PROCEDURE guarded_run(p_x IN VARCHAR2);
END pkg_handler;
/

CREATE OR REPLACE PACKAGE BODY pkg_handler IS
    PROCEDURE guarded_run(p_x IN VARCHAR2) IS
    BEGIN
        RAISE EXCEPTION 'boom';
    EXCEPTION
        WHEN OTHERS THEN
            pkg_callee.simple_log(p_x);
            pkg_callee.simple_log(p_x, 'detail');
    END;
END pkg_handler;
/
