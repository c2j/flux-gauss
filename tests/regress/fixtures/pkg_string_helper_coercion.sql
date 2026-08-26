-- Regression fixture for String-target coercion of same-class helper calls.

CREATE OR REPLACE PACKAGE string_helper_coercion AS
    FUNCTION func_for_dynamic_to_json(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2
    ) RETURN JSON;
END string_helper_coercion;
/

CREATE OR REPLACE PACKAGE BODY string_helper_coercion AS
    FUNCTION func_for_dynamic_to_json(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2
    ) RETURN JSON IS
        v_sql  VARCHAR2(4000);
        v_json JSON := JSON('[]');
        v_item JSON;
        v_idx  INTEGER := 0;
    BEGIN
        v_sql := 'SELECT * FROM ' || p_table_name;
        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        FOR v_rec IN EXECUTE IMMEDIATE v_sql
        LOOP
            v_idx := v_idx + 1;
            v_item := JSON();
            v_item := json_object(
                'id' VALUE v_rec.id,
                'name' VALUE v_rec.name,
                'status' VALUE v_rec.status,
                'amount' VALUE v_rec.amount,
                'seq' VALUE v_idx
            );
            v_json := json_append(v_json, v_item);
        END LOOP;

        RETURN v_json;
    END func_for_dynamic_to_json;
END string_helper_coercion;
/
