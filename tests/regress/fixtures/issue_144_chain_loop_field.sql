CREATE OR REPLACE TABLE v_my_tab_columns_144 (
    table_name  VARCHAR2(100),
    column_name VARCHAR2(100)
);

CREATE OR REPLACE PACKAGE pkg_issue144cols IS
    PROCEDURE prc_move_cols(p_i_inst_num IN NUMBER,
                            p_i_data_date IN VARCHAR2,
                            p_o_retcode OUT VARCHAR2,
                            p_o_retmsg OUT VARCHAR2);
END pkg_issue144cols;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144cols IS
    -- Issue #144 batch 2b: v_columns is BUILT inside the j cursor loop using
    -- the record field j.column_name; a later dynamic-SQL template that
    -- appends v_columns must inline the VALUE placeholder (#{vColumns}) only.
    -- Concat-chain merging used to drag the ',column_name' fragments (with the
    -- loop-record ref) into the outer template — emitting j.get("column_name")
    -- at a call site OUTSIDE the j loop ("cannot find symbol").
    PROCEDURE prc_move_cols(p_i_inst_num IN NUMBER,
                            p_i_data_date IN VARCHAR2,
                            p_o_retcode OUT VARCHAR2,
                            p_o_retmsg OUT VARCHAR2) IS
        v_source_table VARCHAR2(100);
        v_target_table VARCHAR2(100);
        v_columns      VARCHAR2(2000) := '';
        v_sql          VARCHAR2(4000);
        v_sql_diff     VARCHAR2(500);
        v_diff_column  VARCHAR2(100);
        v_diff_value   VARCHAR2(200);
        v_cnt          NUMBER := 0;
    BEGIN
        IF v_source_table IS NULL OR v_target_table IS NULL THEN
            p_o_retcode := '0';
            RETURN;
        END IF;

        IF v_diff_column IS NOT NULL THEN
            v_sql_diff := ' AND ' || v_diff_column || '= ''' || v_diff_value || '''';
        ELSE
            v_sql_diff := '';
        END IF;

        FOR j IN (SELECT t.column_name AS column_name
                    FROM v_my_tab_columns_144 t
                   WHERE t.table_name = v_source_table
                     AND t.column_name NOT IN ('USAGE')) LOOP
            v_columns := v_columns || ',' || j.column_name;
        END LOOP;
        v_columns := substr(v_columns, 2);

        v_sql := 'insert into ' || v_target_table || '(' || v_columns ||
                 ') select ' || v_columns ||
                 ' from ' || v_source_table ||
                 ' where INST_NUM = :1 AND INST_DATA_DATE = :2' ||
                 v_sql_diff;
        EXECUTE IMMEDIATE v_sql USING p_i_inst_num, p_i_data_date;
        p_o_retcode := '0';
        p_o_retmsg  := 'ok';
    END;
END pkg_issue144cols;
/
