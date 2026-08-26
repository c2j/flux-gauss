CREATE TABLE t_issue75_data (
    id NUMBER(10),
    data_value VARCHAR2(64)
);

CREATE OR REPLACE PACKAGE pkg_issue75 IS
    PROCEDURE proc_nested_block_body_fails(p_result OUT VARCHAR2);
    PROCEDURE proc_loop_exc_handler(p_result OUT VARCHAR2);
END pkg_issue75;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue75 IS

    PROCEDURE proc_nested_block_body_fails(p_result OUT VARCHAR2) IS
        CURSOR c_inner IS SELECT data_value AS v FROM t_issue75_data;
        v_tmp VARCHAR2(64);
    BEGIN
        BEGIN
            FOR r2 IN c_inner LOOP
                v_tmp := r2.v;
            END LOOP;
        EXCEPTION
            WHEN OTHERS THEN
                v_tmp := 'fallback';
        END;
        p_result := v_tmp;
    END;

    PROCEDURE proc_loop_exc_handler(p_result OUT VARCHAR2) IS
        CURSOR c_outer IS SELECT data_value AS v FROM t_issue75_data;
        CURSOR c_inner IS SELECT data_value AS v FROM t_issue75_data;
        v_tmp VARCHAR2(64);
    BEGIN
        FOR rec IN c_outer LOOP
            BEGIN
                v_tmp := rec.v;
            EXCEPTION
                WHEN OTHERS THEN
                    FOR r2 IN c_inner LOOP
                        v_tmp := r2.v;
                    END LOOP;
                    CONTINUE;
            END;
        END LOOP;
        p_result := v_tmp;
    END;

END pkg_issue75;
/
