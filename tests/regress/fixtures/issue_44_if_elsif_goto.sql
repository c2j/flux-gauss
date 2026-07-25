-- ============================================================
-- Bug-reproducing fixture for Issue #44 (IF condition loss)
-- ============================================================
-- Root cause: _remove_dynamic_sql_build_lines() at L2260 marks
-- `if (` lines as "guard" and REMOVES them. Triggered when:
--   1. Dynamic SQL concat (v_sql := v_sql || '...')
--   2. IF wraps the concat
--   3. Mapper calls exist inside the IF body
-- Result: try { } else { } else { } catch — no if keywords
-- ============================================================

CREATE TABLE t_issue44_orders (
    order_id    BIGINT PRIMARY KEY,
    order_name  VARCHAR(200),
    status      VARCHAR(20),
    amount      NUMERIC(18,4),
    created_at  TIMESTAMP
);

CREATE OR REPLACE PACKAGE pkg_issue44_bugs AS

    PROCEDURE proc_dynamic_sql_if_else(
        p_status     IN VARCHAR,
        p_date       IN VARCHAR,
        p_result     OUT VARCHAR
    );

    PROCEDURE proc_dynamic_sql_if_elsif(
        p_filter     IN VARCHAR,
        p_mode       IN INT,
        p_result     OUT VARCHAR
    );

    PROCEDURE proc_nested_dynamic_if(
        p_type       IN VARCHAR,
        p_sub_type   IN VARCHAR DEFAULT NULL,
        p_result     OUT VARCHAR
    );

    PROCEDURE proc_non_dynamic_if_else(
        p_flag       IN VARCHAR,
        p_result     OUT VARCHAR
    );

    PROCEDURE proc_chained_if_with_concat(
        p_code       IN VARCHAR,
        p_result     OUT VARCHAR
    );

END pkg_issue44_bugs;
/

CREATE OR REPLACE PACKAGE BODY pkg_issue44_bugs AS

    -- Bug trigger: IF + dynamic SQL concat → if removed
    PROCEDURE proc_dynamic_sql_if_else(
        p_status     IN VARCHAR,
        p_date       IN VARCHAR,
        p_result     OUT VARCHAR
    ) IS
        v_sql       VARCHAR(4000);
        v_count     INT;
        v_name      VARCHAR(200);
    BEGIN
        v_sql := 'SELECT order_id, order_name FROM t_issue44_orders WHERE 1=1';

        IF p_status IS NOT NULL THEN
            v_sql := v_sql || ' AND status = ''' || p_status || '''';
        END IF;

        IF p_date IS NOT NULL THEN
            v_sql := v_sql || ' AND created_at >= TO_DATE(''' || p_date || ''', ''YYYYMMDD'')';
        END IF;

        SELECT COUNT(*) INTO v_count
          FROM t_issue44_orders
         WHERE status = COALESCE(p_status, status);

        -- IF-ELSE with mapper call in THEN → if(line) removed here
        IF v_count > 0 THEN
            SELECT order_name INTO v_name
              FROM t_issue44_orders
             WHERE status = p_status
             LIMIT 1;
            p_result := v_name;
        ELSE
            IF p_status = 'ACTIVE' THEN
                p_result := 'NO_ACTIVE_ORDERS';
            ELSIF p_status = 'PENDING' THEN
                p_result := 'NO_PENDING_ORDERS';
            ELSE
                p_result := 'NO_ORDERS';
            END IF;
        END IF;
    END;

    -- Bug trigger: IF-ELSIF + dynamic SQL in each branch
    PROCEDURE proc_dynamic_sql_if_elsif(
        p_filter     IN VARCHAR,
        p_mode       IN INT,
        p_result     OUT VARCHAR
    ) IS
        v_sql   VARCHAR(4000);
        v_count INT;
    BEGIN
        v_sql := 'SELECT COUNT(*) FROM t_issue44_orders WHERE 1=1';

        IF p_mode = 1 THEN
            v_sql := v_sql || ' AND status = ''ACTIVE''';
        ELSIF p_mode = 2 THEN
            v_sql := v_sql || ' AND status = ''PENDING''';
        ELSIF p_mode = 3 THEN
            IF p_filter IS NOT NULL THEN
                v_sql := v_sql || ' AND order_name LIKE ''%' || p_filter || '%''';
            END IF;
            v_sql := v_sql || ' AND amount > 1000';
        ELSE
            v_sql := v_sql || ' AND status = ''CLOSED''';
        END IF;

        IF p_filter IS NOT NULL THEN
            SELECT COUNT(*) INTO v_count
              FROM t_issue44_orders
             WHERE order_name LIKE '%' || p_filter || '%';
            p_result := 'FILTERED: ' || CAST(v_count AS VARCHAR);
        ELSE
            p_result := 'ALL_MODE';
        END IF;
    END;

    -- Bug trigger: nested IF + dynamic SQL
    PROCEDURE proc_nested_dynamic_if(
        p_type       IN VARCHAR,
        p_sub_type   IN VARCHAR DEFAULT NULL,
        p_result     OUT VARCHAR
    ) IS
        v_sql       VARCHAR(4000);
        v_amount    NUMERIC(18,4);
    BEGIN
        v_sql := 'SELECT COALESCE(SUM(amount), 0) FROM t_issue44_orders WHERE 1=1';

        IF p_type = 'SUMMARY' THEN
            v_sql := v_sql || ' GROUP BY status';
        ELSE
            IF p_sub_type IS NOT NULL THEN
                v_sql := v_sql || ' AND order_name LIKE ''%' || p_sub_type || '%''';
            END IF;
        END IF;

        SELECT COALESCE(SUM(amount), 0) INTO v_amount
          FROM t_issue44_orders;

        IF v_amount > 0 THEN
            p_result := 'AMOUNT: ' || CAST(v_amount AS VARCHAR);
        ELSIF p_type = 'SUMMARY' THEN
            p_result := 'ZERO_SUMMARY';
        ELSE
            p_result := 'ZERO';
        END IF;
    END;

    -- Control: no dynamic SQL → IF preserved
    PROCEDURE proc_non_dynamic_if_else(
        p_flag       IN VARCHAR,
        p_result     OUT VARCHAR
    ) IS
        v_name  VARCHAR(200);
    BEGIN
        IF p_flag = '1' THEN
            SELECT order_name INTO v_name
              FROM t_issue44_orders
             WHERE status = 'ACTIVE'
             LIMIT 1;
            p_result := v_name;
        ELSIF p_flag = '2' THEN
            IF v_name IS NULL THEN
                p_result := 'NULL_NAME';
            ELSE
                p_result := v_name;
            END IF;
        ELSE
            p_result := 'UNKNOWN';
        END IF;
    END;

    -- Bug trigger: chained IFs with concat + final IF with mapper
    PROCEDURE proc_chained_if_with_concat(
        p_code       IN VARCHAR,
        p_result     OUT VARCHAR
    ) IS
        v_sql   VARCHAR(4000);
        v_count INT;
    BEGIN
        v_sql := 'SELECT COUNT(*) FROM t_issue44_orders WHERE 1=1';

        IF p_code = 'A' THEN
            v_sql := v_sql || ' AND status = ''ACTIVE''';
        END IF;

        IF LENGTH(p_code) > 1 THEN
            v_sql := v_sql || ' AND order_name LIKE ''%' || p_code || '%''';
        END IF;

        -- Final IF with mapper call → if line removed by cleanup
        IF p_code IS NOT NULL THEN
            SELECT COUNT(*) INTO v_count
              FROM t_issue44_orders
             WHERE order_name LIKE '%' || p_code || '%';
            IF v_count > 10 THEN
                p_result := 'MANY: ' || CAST(v_count AS VARCHAR);
            ELSE
                p_result := 'FEW: ' || CAST(v_count AS VARCHAR);
            END IF;
        ELSE
            p_result := 'NO_CODE';
        END IF;
    END;

END pkg_issue44_bugs;
/
