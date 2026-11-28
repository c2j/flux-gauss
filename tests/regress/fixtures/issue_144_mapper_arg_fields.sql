CREATE OR REPLACE TABLE par_issue144_model (
    operation_no  VARCHAR2(50),
    table_name    VARCHAR2(100),
    diff_column   VARCHAR2(100),
    diff_value    VARCHAR2(200),
    forest_no     NUMBER
);

CREATE OR REPLACE PACKAGE pkg_issue144mp_helper IS
    PROCEDURE gen_xml(p_i_table IN VARCHAR2, p_i_diff_col IN VARCHAR2, p_i_diff_val IN VARCHAR2);
END pkg_issue144mp_helper;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144mp_helper IS
    PROCEDURE gen_xml(p_i_table IN VARCHAR2, p_i_diff_col IN VARCHAR2, p_i_diff_val IN VARCHAR2) IS
    BEGIN
        NULL;
    END;
END pkg_issue144mp_helper;
/

CREATE OR REPLACE PACKAGE pkg_issue144mp IS
    PROCEDURE prc_model_loop(p_i_operation_no IN VARCHAR2);
END pkg_issue144mp;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144mp IS
    -- Issue #144 batch 1b: a dynamic-SQL fragment built from cursor FOR-loop
    -- record fields (s.diff_column / s.diff_value) is appended to another
    -- dynamic-SQL variable and executed. The generated mapper call used to
    -- flatten the fields to bare, undeclared locals — javac "cannot find
    -- symbol" (tableName / diffColumn / diffValue). They must become
    -- #{s_diff_column} placeholders bound to s.get("diff_column").
    PROCEDURE prc_model_loop(p_i_operation_no IN VARCHAR2) IS
        v_table_name VARCHAR2(100);
        v_sql_diff   VARCHAR2(500);
        v_sql        VARCHAR2(2000);
        v_cnt        NUMBER := 0;
    BEGIN
        FOR s IN (SELECT table_name, diff_column, diff_value
                    FROM par_issue144_model t
                   WHERE t.operation_no = p_i_operation_no
                   ORDER BY t.forest_no) LOOP
            v_table_name := s.table_name;
            IF s.diff_column IS NOT NULL THEN
                v_sql_diff := ' AND ' || s.diff_column || '= ''' || s.diff_value || '''';
            ELSE
                v_sql_diff := '';
            END IF;
            IF v_table_name IS NOT NULL THEN
                v_sql := 'SELECT xmlforest(:element_name) FROM ' || v_table_name ||
                         ' WHERE SEQ_NO = :seq_no' || v_sql_diff;
                EXECUTE IMMEDIATE v_sql;
                v_cnt := v_cnt + 1;
            END IF;
        END LOOP;
    END;
END pkg_issue144mp;
/
