CREATE OR REPLACE PACKAGE issue_144_bind_index IS
    -- Issue #144 batch 4d goal_a: positional bind-index construction.
    -- `' = :' || n` inside a scalar FOR-range loop builds `:1`, `:2`, ...
    -- bind markers as TEXT for EXECUTE IMMEDIATE ... USING. The loop var n
    -- is construction-time text, not a runtime mapper parameter: rendering
    -- it as `${n}` produces @Param("n") + a call-site `n` reference outside
    -- the loop scope (javac 「找不到符号: 变量 n」, PpmXmlManageService:89/91).
    PROCEDURE prc_bind_index(p_seqno IN VARCHAR2, p_fund_id IN VARCHAR2);
END issue_144_bind_index;
/

CREATE OR REPLACE PACKAGE BODY issue_144_bind_index IS
    PROCEDURE prc_bind_index(p_seqno IN VARCHAR2, p_fund_id IN VARCHAR2) IS
        v_sql      VARCHAR2(4000);
        v_col      VARCHAR2(100);
        v_para_num NUMBER;
        v_xml_tmp  VARCHAR2(4000);
    BEGIN
        v_col := 'pk_id';
        v_para_num := 2;
        -- scalar FOR-range var n builds positional bind text ':1', ':2', ...
        FOR n IN 1 .. 2 LOOP
            IF n = 1 THEN
                v_sql := 'SELECT xmlforest(:element_name) FROM src_t WHERE ' ||
                         v_col || ' = :' || n;
            ELSE
                v_sql := v_sql || ' AND ' || v_col || ' = :' || n;
            END IF;
        END LOOP;
        v_sql := REPLACE(v_sql, ':element_name', 'col_a as "A"');
        IF v_para_num = 1 THEN
            EXECUTE IMMEDIATE v_sql INTO v_xml_tmp USING p_seqno;
        ELSE
            EXECUTE IMMEDIATE v_sql INTO v_xml_tmp USING p_seqno, p_fund_id;
        END IF;
        NULL;
    END;
END issue_144_bind_index;
/
