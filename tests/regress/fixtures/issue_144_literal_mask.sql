CREATE OR REPLACE PACKAGE pkg_issue144lit IS
    PROCEDURE prc_decode_lit(p_i_operation_no IN VARCHAR2,
                             p_i_usage        IN VARCHAR2,
                             p_i_data_group   IN VARCHAR2);
END pkg_issue144lit;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144lit IS
    -- Issue #144 batch 2a: quoted string literals inside SQL text must be
    -- masked before parameter-name substitution. The literal 'S' inside
    -- decode(...) used to match \bs\b and become #{s} — corrupting the SQL
    -- and adding a phantom mapper param for the cursor loop record s.
    PROCEDURE prc_decode_lit(p_i_operation_no IN VARCHAR2,
                             p_i_usage        IN VARCHAR2,
                             p_i_data_group   IN VARCHAR2) IS
        v_table_name VARCHAR2(100);
    BEGIN
        FOR s IN (SELECT t.table_name,
                         decode(t.data_group,
                                'B', t.table_name || '_HIS',
                                'S', t.table_name || '_HIS',
                                nvl(t.his_table_name,
                                    decode(p_i_usage, 'M', t.table_name, t.table_name))) his_table_name
                    FROM par_issue144_lit t
                   WHERE t.operation_no = p_i_operation_no
                     AND (t.data_group = p_i_data_group OR p_i_data_group IS NULL)) LOOP
            v_table_name := s.table_name;
            IF v_table_name IS NOT NULL THEN
                pkg_issue144lit_helper.gen_xml(v_table_name);
            END IF;
        END LOOP;
    END;
END pkg_issue144lit;
/

CREATE OR REPLACE PACKAGE pkg_issue144lit_helper IS
    PROCEDURE gen_xml(p_i_table IN VARCHAR2);
END pkg_issue144lit_helper;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue144lit_helper IS
    PROCEDURE gen_xml(p_i_table IN VARCHAR2) IS
    BEGIN
        NULL;
    END;
END pkg_issue144lit_helper;
/

CREATE OR REPLACE TABLE par_issue144_lit (
    operation_no    VARCHAR2(50),
    data_group      VARCHAR2(10),
    table_name      VARCHAR2(100),
    his_table_name  VARCHAR2(100)
);
