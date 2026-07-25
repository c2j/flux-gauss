-- NOTE: DDL moved to ddl/*.sql



CREATE OR REPLACE FUNCTION string_split(
    p_string IN VARCHAR2,
    p_delimiter IN VARCHAR2 DEFAULT ','
) RETURN VARCHAR2_ARRAY IS
    v_array VARCHAR2_ARRAY;
    v_start INTEGER := 1;
    v_end   INTEGER;
    v_idx   INTEGER := 1;
BEGIN
    IF p_string IS NULL THEN
        RETURN v_array;
    END IF;

    v_end := INSTR(p_string, p_delimiter, v_start);

    WHILE v_end > 0 LOOP
        v_array(v_idx) := TRIM(SUBSTR(p_string, v_start, v_end - v_start));
        v_idx := v_idx + 1;
        v_start := v_end + LENGTH(p_delimiter);
        v_end := INSTR(p_string, p_delimiter, v_start);
    END LOOP;

    v_array(v_idx) := TRIM(SUBSTR(p_string, v_start));

    RETURN v_array;
END;
/

-- 数组长度函数
DROP FUNCTION IF EXISTS array_length;
CREATE OR REPLACE FUNCTION array_length(
    p_array IN VARCHAR2_ARRAY
) RETURN INTEGER IS
BEGIN
    RETURN p_array.COUNT;
END;
/

-- ============================================
-- 第五部分：存储过程包 - FOR .. IN SELECT
-- ============================================

CREATE OR REPLACE PACKAGE pkg_for_in_select AS
    TYPE rec_employee IS RECORD (
        emp_id      INTEGER,
        emp_name    VARCHAR2(100),
        dept_id     INTEGER,
        salary      NUMERIC(18,2),
        hire_date   DATE
    );

    PROCEDURE proc_sync_employee_bonus;
    FUNCTION func_get_bonus_rate(p_dept_id IN INTEGER) RETURN NUMERIC;
END pkg_for_in_select;
/

CREATE OR REPLACE PACKAGE BODY pkg_for_in_select AS

    FUNCTION func_get_bonus_rate(p_dept_id IN INTEGER) RETURN NUMERIC IS
        v_rate NUMERIC(5,2);
    BEGIN
        CASE p_dept_id
            WHEN 10 THEN v_rate := 0.15;
            WHEN 20 THEN v_rate := 0.10;
            WHEN 30 THEN v_rate := 0.08;
            ELSE v_rate := 0.05;
        END CASE;
        RETURN v_rate;
    END;

    PROCEDURE proc_sync_employee_bonus IS
        v_total_bonus NUMERIC(18,2) := 0;
        v_processed   INTEGER := 0;
        v_log_id      INTEGER;
    BEGIN
        INSERT INTO batch_log(batch_id, batch_type, start_time, status)
        VALUES (seq_batch_log.NEXTVAL, 'BONUS_CALC', SYSDATE, 'RUNNING')
        RETURNING batch_id INTO v_log_id;

        FOR v_emp IN (
            SELECT
                e.employee_id,
                e.employee_name,
                e.department_id,
                e.salary,
                e.hire_date,
                d.department_name,
                (SELECT NVL(SUM(bonus_amount), 0)
                 FROM employee_bonus eb
                 WHERE eb.emp_id = e.employee_id
                 AND eb.bonus_year = EXTRACT(YEAR FROM SYSDATE)
                ) AS year_bonus_total
            FROM employees e
            JOIN departments d ON e.department_id = d.department_id
            WHERE e.status = 'ACTIVE'
              AND e.hire_date <= ADD_MONTHS(SYSDATE, -6)
            ORDER BY e.department_id, e.salary DESC
        ) LOOP

            DECLARE
                v_bonus_rate  NUMERIC(5,2);
                v_bonus_amt   NUMERIC(18,2);
                v_max_bonus   NUMERIC(18,2);
                v_insert_id   INTEGER;
            BEGIN
                v_bonus_rate := func_get_bonus_rate(v_emp.department_id);
                v_bonus_amt  := ROUND(v_emp.salary * v_bonus_rate, 2);
                v_max_bonus := v_emp.salary * 12 * 0.20;

                IF v_emp.year_bonus_total + v_bonus_amt > v_max_bonus THEN
                    v_bonus_amt := v_max_bonus - v_emp.year_bonus_total;
                    INSERT INTO bonus_limit_log(log_time, emp_id, limit_reason)
                    VALUES (SYSDATE, v_emp.employee_id,
                            'Bonus capped at annual 20% limit');
                END IF;

                IF v_bonus_amt > 0 THEN
                    INSERT INTO employee_bonus (
                        bonus_id, emp_id, bonus_amount,
                        bonus_month, bonus_year, calc_reason, create_time
                    ) VALUES (
                        seq_employee_bonus.NEXTVAL,
                        v_emp.employee_id,
                        v_bonus_amt,
                        EXTRACT(MONTH FROM SYSDATE),
                        EXTRACT(YEAR FROM SYSDATE),
                        'Q' || TO_CHAR(SYSDATE, 'Q') || ' performance bonus',
                        SYSDATE
                    )
                    RETURNING bonus_id INTO v_insert_id;

                    v_total_bonus := v_total_bonus + v_bonus_amt;
                    v_processed := v_processed + 1;

                    IF MOD(v_processed, 100) = 0 THEN
                        COMMIT;
                    END IF;
                END IF;

            EXCEPTION
                WHEN DUP_VAL_ON_INDEX THEN
                    UPDATE employee_bonus
                    SET bonus_amount = v_bonus_amt,
                        update_time = SYSDATE
                    WHERE emp_id = v_emp.employee_id
                      AND bonus_year = EXTRACT(YEAR FROM SYSDATE)
                      AND bonus_month = EXTRACT(MONTH FROM SYSDATE);
                WHEN OTHERS THEN
                    INSERT INTO error_log(error_time, procedure_name,
                                         error_code, error_message, context)
                    VALUES (SYSDATE, 'proc_sync_employee_bonus',
                           SQLCODE, SQLERRM,
                           'EmpID=' || v_emp.employee_id);
                    CONTINUE;
            END;

        END LOOP;

        COMMIT;

        UPDATE batch_log
        SET end_time = SYSDATE,
            status = 'SUCCESS',
            record_count = v_processed,
            total_amount = v_total_bonus,
            message = 'Processed ' || v_processed || ' employees, total bonus: ' || v_total_bonus
        WHERE batch_id = v_log_id;

        COMMIT;

    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            UPDATE batch_log
            SET status = 'FAILED',
                end_time = SYSDATE,
                message = SQLERRM
            WHERE batch_id = v_log_id;
            COMMIT;
            RAISE;
    END proc_sync_employee_bonus;

END pkg_for_in_select;
/

-- ============================================
-- 第六部分：存储过程包 - OPEN CURSOR FOR SELECT
-- ============================================

CREATE OR REPLACE PACKAGE pkg_open_cursor AS
    TYPE cur_employee IS REF CURSOR RETURN employees%ROWTYPE;
    TYPE cur_weak IS REF CURSOR;

    PROCEDURE proc_get_employee_cursor(
        p_dept_id   IN  INTEGER,
        p_min_salary IN  NUMERIC,
        p_result     OUT SYS_REFCURSOR
    );

    PROCEDURE proc_process_dynamic_query(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2,
        p_order_by     IN VARCHAR2
    );

    PROCEDURE proc_paginated_query(
        p_page_size   IN  INTEGER,
        p_page_num    IN  INTEGER,
        p_data_cursor OUT SYS_REFCURSOR,
        p_count_cursor OUT SYS_REFCURSOR
    );
END pkg_open_cursor;
/

CREATE OR REPLACE PACKAGE BODY pkg_open_cursor AS

    PROCEDURE proc_get_employee_cursor(
        p_dept_id    IN  INTEGER,
        p_min_salary IN  NUMERIC,
        p_result     OUT SYS_REFCURSOR
    ) IS
    BEGIN
        OPEN p_result FOR
            SELECT
                e.employee_id,
                e.employee_name,
                e.salary,
                e.email,
                d.department_name,
                p.project_name,
                RANK() OVER (PARTITION BY e.department_id
                             ORDER BY e.salary DESC) AS dept_salary_rank,
                PERCENT_RANK() OVER (ORDER BY e.salary) AS salary_percentile
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            LEFT JOIN projects p ON e.current_project_id = p.project_id
            WHERE e.department_id = p_dept_id
              AND e.salary >= p_min_salary
              AND e.status = 'ACTIVE'
            ORDER BY e.salary DESC;
    END proc_get_employee_cursor;

    PROCEDURE proc_process_dynamic_query(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2,
        p_order_by     IN VARCHAR2
    ) IS
        v_cursor      SYS_REFCURSOR;
        v_rec_id      INTEGER;
        v_rec_name    VARCHAR2(200);
        v_rec_value   NUMERIC(18,2);
        v_rec_status  VARCHAR2(20);
        v_rec_time    TIMESTAMP;
        v_sql         VARCHAR2(4000);
        v_row_count   INTEGER := 0;
        v_batch_size  INTEGER := 500;
        v_start_time  TIMESTAMP;
    BEGIN
        v_start_time := SYSTIMESTAMP;

        v_sql := 'SELECT id, name, amount, status, create_time FROM ' || p_table_name;

        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        INSERT INTO audit_log(log_time, operation, sql_text, user_name)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_QUERY', v_sql, USER);

        OPEN v_cursor FOR v_sql;

        LOOP
            FETCH v_cursor INTO v_rec_id, v_rec_name, v_rec_value, v_rec_status, v_rec_time;
            EXIT WHEN v_cursor%NOTFOUND;

            v_row_count := v_row_count + 1;

            BEGIN
                CASE v_rec_status
                    WHEN 'PENDING' THEN
                        EXECUTE IMMEDIATE
                            'UPDATE ' || p_table_name ||
                            ' SET status = ''PROCESSING'', process_time = :1 WHERE id = :2'
                        USING SYSTIMESTAMP, v_rec_id;

                    WHEN 'PROCESSING' THEN
                        IF v_rec_time < SYSTIMESTAMP - INTERVAL '2' HOUR THEN
                            EXECUTE IMMEDIATE
                                'UPDATE ' || p_table_name ||
                                ' SET status = ''TIMEOUT'', retry_count = NVL(retry_count,0)+1 WHERE id = :1'
                            USING v_rec_id;
                        END IF;

                    WHEN 'COMPLETED' THEN
                        INSERT INTO archive_table (id, name, amount, status, archived_time)
                        VALUES (v_rec_id, v_rec_name, v_rec_value, v_rec_status, SYSTIMESTAMP);

                        EXECUTE IMMEDIATE
                            'DELETE FROM ' || p_table_name || ' WHERE id = :1'
                        USING v_rec_id;

                    ELSE
                        INSERT INTO exception_log(exception_time, record_id, exception_type, detail)
                        VALUES (SYSTIMESTAMP, v_rec_id, 'UNKNOWN_STATUS', v_rec_status);
                END CASE;

                IF MOD(v_row_count, v_batch_size) = 0 THEN
                    COMMIT;
                END IF;

            EXCEPTION
                WHEN OTHERS THEN
                    INSERT INTO error_log(error_time, context, sqlcode, sqlerrm)
                    VALUES (SYSTIMESTAMP, 'Record ID=' || v_rec_id, SQLCODE, SQLERRM);
                    CONTINUE;
            END;

        END LOOP;

        COMMIT;
        CLOSE v_cursor;

        INSERT INTO performance_log(log_time, procedure_name, rows_processed, elapsed_ms)
        VALUES (SYSTIMESTAMP, 'proc_process_dynamic_query', v_row_count,
                EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start_time)) * 1000);

    EXCEPTION
        WHEN OTHERS THEN
            IF v_cursor%ISOPEN THEN
                CLOSE v_cursor;
            END IF;
            ROLLBACK;
            RAISE;
    END proc_process_dynamic_query;

    PROCEDURE proc_paginated_query(
        p_page_size   IN  INTEGER,
        p_page_num    IN  INTEGER,
        p_data_cursor OUT SYS_REFCURSOR,
        p_count_cursor OUT SYS_REFCURSOR
    ) IS
        v_offset INTEGER;
    BEGIN
        v_offset := (p_page_num - 1) * p_page_size;

        OPEN p_data_cursor FOR
            SELECT
                t.*,
                COUNT(*) OVER () AS total_rows
            FROM (
                SELECT
                    employee_id,
                    employee_name,
                    department_id,
                    salary,
                    hire_date,
                    ROW_NUMBER() OVER (ORDER BY employee_id) AS rn
                FROM employees
                WHERE status = 'ACTIVE'
            ) t
            WHERE rn > v_offset AND rn <= v_offset + p_page_size
            ORDER BY rn;

        OPEN p_count_cursor FOR
            SELECT
                COUNT(*) AS total_count,
                COUNT(DISTINCT department_id) AS dept_count,
                ROUND(AVG(salary), 2) AS avg_salary,
                MIN(salary) AS min_salary,
                MAX(salary) AS max_salary
            FROM employees
            WHERE status = 'ACTIVE';
    END proc_paginated_query;

END pkg_open_cursor;
/

-- ============================================
-- 第七部分：存储过程包 - FOR .. IN 动态SQL
-- ============================================

CREATE OR REPLACE PACKAGE pkg_dynamic_for_loop AS
    PROCEDURE proc_dynamic_for_processing(
        p_table_name     IN VARCHAR2,
        p_select_columns IN VARCHAR2,
        p_where_clause   IN VARCHAR2,
        p_order_by       IN VARCHAR2,
        p_process_type   IN VARCHAR2
    );
END pkg_dynamic_for_loop;
/

CREATE OR REPLACE PACKAGE BODY pkg_dynamic_for_loop AS

    PROCEDURE proc_dynamic_for_processing(
        p_table_name     IN VARCHAR2,
        p_select_columns IN VARCHAR2,
        p_where_clause   IN VARCHAR2,
        p_order_by       IN VARCHAR2,
        p_process_type   IN VARCHAR2
    ) IS
        v_sql          VARCHAR2(8000);
        v_process_sql  VARCHAR2(4000);
        v_row_count    INTEGER := 0;
        v_batch_commit INTEGER := 200;
        v_start_time   TIMESTAMP;
        v_pk_id        INTEGER;
        v_status       VARCHAR2(50);
        v_amount       NUMERIC(18,2);
        v_name         VARCHAR2(200);
        v_create_time  TIMESTAMP;
    BEGIN
        v_start_time := SYSTIMESTAMP;

        v_sql := 'SELECT ' || NVL(p_select_columns, '*') || ' FROM ' || p_table_name;
        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;
        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        INSERT INTO audit_log(log_time, operation, sql_text, params)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_FOR_LOOP', v_sql,
                'table=' || p_table_name || ',type=' || p_process_type);

        FOR v_rec IN EXECUTE IMMEDIATE v_sql
        LOOP
            v_row_count := v_row_count + 1;
            v_pk_id       := v_rec.id;
            v_status      := v_rec.status;
            v_amount      := v_rec.amount;
            v_name        := v_rec.name;
            v_create_time := v_rec.create_time;

            CASE p_process_type
                WHEN 'ARCHIVE' THEN
                    v_process_sql := 'INSERT INTO ' || p_table_name || '_hist
                        SELECT *, :1, :2 FROM ' || p_table_name || ' WHERE id = :3';
                    EXECUTE IMMEDIATE v_process_sql USING SYSTIMESTAMP, 'ARCHIVED', v_pk_id;
                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET status = :1, archive_time = :2 WHERE id = :3'
                        USING 'ARCHIVED', SYSTIMESTAMP, v_pk_id;

                WHEN 'UPDATE' THEN
                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET status = :1, update_time = :2, update_count = NVL(update_count,0)+1 WHERE id = :3'
                        USING CASE v_status
                                WHEN 'PENDING' THEN 'PROCESSING'
                                WHEN 'PROCESSING' THEN 'COMPLETED'
                                ELSE v_status
                              END,
                              SYSTIMESTAMP, v_pk_id;

                WHEN 'DELETE' THEN
                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET is_deleted = :1, delete_time = :2 WHERE id = :3'
                        USING 1, SYSTIMESTAMP, v_pk_id;

                ELSE
                    INSERT INTO scan_log(scan_time, table_name, record_id, record_status)
                    VALUES (SYSTIMESTAMP, p_table_name, v_pk_id, v_status);
            END CASE;

            IF MOD(v_row_count, v_batch_commit) = 0 THEN
                COMMIT;
            END IF;
        END LOOP;

        COMMIT;

        INSERT INTO performance_log(log_time, operation, rows_affected, elapsed_ms)
        VALUES (SYSTIMESTAMP, 'dynamic_for_' || p_process_type, v_row_count,
                EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start_time)) * 1000);

    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            INSERT INTO error_log(error_time, procedure_name, sql_text, error_msg)
            VALUES (SYSTIMESTAMP, 'proc_dynamic_for_processing', v_sql, SQLERRM);
            RAISE;
    END proc_dynamic_for_processing;

END pkg_dynamic_for_loop;
/

-- ============================================
-- 第八部分：存储过程包 - 高级游标（USING参数、返回游标）
-- ============================================

CREATE OR REPLACE PACKAGE pkg_cursor_advanced AS
    TYPE rec_order_summary IS RECORD (
        order_id      INTEGER,
        customer_name VARCHAR2(100),
        total_amount  NUMERIC(18,2),
        item_count    INTEGER,
        order_status  VARCHAR2(20)
    );
    TYPE cur_order_typed IS REF CURSOR RETURN rec_order_summary;
    TYPE cur_generic IS REF CURSOR;

    PROCEDURE proc_cursor_dynamic_using(
        p_table_name    IN  VARCHAR2,
        p_status_list   IN  VARCHAR2,
        p_min_amount    IN  NUMERIC,
        p_start_date    IN  DATE,
        p_result        OUT SYS_REFCURSOR
    );

    FUNCTION func_get_order_cursor(
        p_customer_id IN INTEGER,
        p_date_from   IN DATE,
        p_date_to     IN DATE
    ) RETURN cur_order_typed;

    PROCEDURE proc_multi_cursor_return(
        p_query_id     IN  INTEGER,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_meta_cursor  OUT SYS_REFCURSOR
    );

    PROCEDURE proc_paginate_with_using(
        p_base_sql     IN  VARCHAR2,
        p_page_size    IN  INTEGER,
        p_page_num     IN  INTEGER,
        p_bind_values  IN  VARCHAR2,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_total_cursor OUT SYS_REFCURSOR
    );
END pkg_cursor_advanced;
/

CREATE OR REPLACE PACKAGE BODY pkg_cursor_advanced AS

    PROCEDURE proc_cursor_dynamic_using(
        p_table_name    IN  VARCHAR2,
        p_status_list   IN  VARCHAR2,
        p_min_amount    IN  NUMERIC,
        p_start_date    IN  DATE,
        p_result        OUT SYS_REFCURSOR
    ) IS
        v_sql           VARCHAR2(4000);
        v_status_array  VARCHAR2_ARRAY;
        v_status_count  INTEGER;
        v_in_clause     VARCHAR2(1000);
    BEGIN
        v_status_array := string_split(p_status_list, ',');
        v_status_count := array_length(v_status_array);

        FOR i IN 1..v_status_count LOOP
            IF i > 1 THEN
                v_in_clause := v_in_clause || ',';
            END IF;
            v_in_clause := v_in_clause || ':' || i;
        END LOOP;

        v_sql := 'SELECT
                    t.id,
                    t.name,
                    t.status,
                    t.amount,
                    t.create_time,
                    t.customer_id,
                    c.customer_name,
                    CASE WHEN t.amount > :' || (v_status_count + 1) || ' THEN ''HIGH'' ELSE ''NORMAL'' END AS amount_level,
                    EXTRACT(DAY FROM (CURRENT_TIMESTAMP - :' || (v_status_count + 2) || ')) AS days_elapsed
                  FROM ' || p_table_name || ' t
                  LEFT JOIN customers c ON t.customer_id = c.customer_id
                  WHERE t.status IN (' || v_in_clause || ')
                    AND t.amount >= :' || (v_status_count + 1) || '
                    AND t.create_time >= :' || (v_status_count + 2) || '
                  ORDER BY t.amount DESC';

        CASE v_status_count
            WHEN 1 THEN
                OPEN p_result FOR v_sql USING v_status_array[1], p_min_amount, p_start_date;
            WHEN 2 THEN
                OPEN p_result FOR v_sql USING v_status_array[1], v_status_array[2], p_min_amount, p_start_date;
            WHEN 3 THEN
                OPEN p_result FOR v_sql USING v_status_array[1], v_status_array[2], v_status_array[3], p_min_amount, p_start_date;
            ELSE
                v_sql := REPLACE(v_sql, v_in_clause, p_status_list);
                OPEN p_result FOR v_sql USING p_min_amount, p_start_date;
        END CASE;

        INSERT INTO audit_log(log_time, operation, sql_text, bind_params)
        VALUES (SYSTIMESTAMP, 'CURSOR_DYNAMIC_USING', v_sql,
                'status=' || p_status_list || ',min=' || p_min_amount || ',date=' || p_start_date);
    END proc_cursor_dynamic_using;

    FUNCTION func_get_order_cursor(
        p_customer_id IN INTEGER,
        p_date_from   IN DATE,
        p_date_to     IN DATE
    ) RETURN cur_order_typed IS
        v_cursor cur_order_typed;
        v_sql    VARCHAR2(4000);
    BEGIN
        v_sql := 'SELECT
                    o.order_id,
                    c.customer_name,
                    SUM(oi.quantity * oi.unit_price) AS total_amount,
                    COUNT(DISTINCT oi.item_id) AS item_count,
                    o.order_status
                  FROM orders o
                  JOIN customers c ON o.customer_id = c.customer_id
                  JOIN order_items oi ON o.order_id = oi.order_id
                  WHERE o.customer_id = :1
                    AND o.order_date BETWEEN :2 AND :3
                  GROUP BY o.order_id, c.customer_name, o.order_status
                  ORDER BY total_amount DESC';

        OPEN v_cursor FOR v_sql USING p_customer_id, p_date_from, p_date_to;
        RETURN v_cursor;
    END func_get_order_cursor;

    PROCEDURE proc_multi_cursor_return(
        p_query_id     IN  INTEGER,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_meta_cursor  OUT SYS_REFCURSOR
    ) IS
    BEGIN
        OPEN p_data_cursor FOR
            SELECT q.*, ROW_NUMBER() OVER (ORDER BY q.priority DESC, q.create_time) AS rn
            FROM query_results q
            WHERE q.query_id = :1
            ORDER BY q.priority DESC
            USING p_query_id;

        OPEN p_meta_cursor FOR
            SELECT
                COUNT(*) AS total_rows,
                MIN(create_time) AS earliest,
                MAX(create_time) AS latest,
                COUNT(DISTINCT status) AS status_distinct_count,
                AVG(CASE WHEN amount IS NOT NULL THEN amount END) AS avg_amount,
                query_params
            FROM query_results
            WHERE query_id = :1
            GROUP BY query_params
            USING p_query_id;
    END proc_multi_cursor_return;

    PROCEDURE proc_paginate_with_using(
        p_base_sql     IN  VARCHAR2,
        p_page_size    IN  INTEGER,
        p_page_num     IN  INTEGER,
        p_bind_values  IN  VARCHAR2,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_total_cursor OUT SYS_REFCURSOR
    ) IS
        v_offset       INTEGER;
        v_count_sql    VARCHAR2(4000);
        v_page_sql     VARCHAR2(4000);
    BEGIN
        v_offset := (p_page_num - 1) * p_page_size;

        v_count_sql := 'SELECT COUNT(*) FROM (' || p_base_sql || ') t';
        v_page_sql := 'SELECT * FROM (' || p_base_sql || ') t LIMIT :1 OFFSET :2';

        OPEN p_data_cursor FOR v_page_sql USING p_page_size, v_offset;
        OPEN p_total_cursor FOR v_count_sql;
    END proc_paginate_with_using;

END pkg_cursor_advanced;
/

-- ============================================
-- 第九部分：存储过程包 - 游标生命周期管理
-- ============================================

CREATE OR REPLACE PACKAGE pkg_cursor_lifecycle AS
    TYPE cur_var IS REF CURSOR;

    PROCEDURE proc_get_raw_cursor(
        p_dept_id IN INTEGER,
        p_cursor  OUT SYS_REFCURSOR
    );

    PROCEDURE proc_enhance_cursor(
        p_in_cursor  IN  SYS_REFCURSOR,
        p_out_cursor OUT SYS_REFCURSOR
    );

    PROCEDURE proc_consume_cursor(
        p_cursor IN SYS_REFCURSOR,
        p_summary OUT VARCHAR2
    );

    PROCEDURE proc_full_pipeline(p_dept_id IN INTEGER);
END pkg_cursor_lifecycle;
/

CREATE OR REPLACE PACKAGE BODY pkg_cursor_lifecycle AS

    PROCEDURE proc_get_raw_cursor(
        p_dept_id IN INTEGER,
        p_cursor  OUT SYS_REFCURSOR
    ) IS
    BEGIN
        OPEN p_cursor FOR
            SELECT employee_id, employee_name, salary, department_id, hire_date
            FROM employees
            WHERE department_id = :1 AND status = 'ACTIVE'
            ORDER BY salary DESC
            USING p_dept_id;
    END;

    PROCEDURE proc_enhance_cursor(
        p_in_cursor  IN  SYS_REFCURSOR,
        p_out_cursor OUT SYS_REFCURSOR
    ) IS
        v_id       INTEGER;
        v_name     VARCHAR2(100);
        v_salary   NUMERIC(18,2);
        v_dept_id  INTEGER;
        v_hire_date DATE;
        v_temp     VARCHAR2(30) := 'tmp_enhanced_' || TO_CHAR(SYSTIMESTAMP, 'YYYYMMDDHH24MISS');
    BEGIN
        EXECUTE IMMEDIATE 'CREATE TEMP TABLE ' || v_temp || ' (
            employee_id INTEGER, employee_name VARCHAR2(100), salary NUMERIC(18,2),
            department_id INTEGER, hire_date DATE,
            years_of_service NUMERIC(5,2), salary_level VARCHAR2(20), bonus_eligible INTEGER
        )';

        LOOP
            FETCH p_in_cursor INTO v_id, v_name, v_salary, v_dept_id, v_hire_date;
            EXIT WHEN p_in_cursor%NOTFOUND;

            EXECUTE IMMEDIATE 'INSERT INTO ' || v_temp || ' VALUES (
                :1, :2, :3, :4, :5,
                EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM :6),
                CASE WHEN :7 > 80000 THEN ''SENIOR'' ELSE ''JUNIOR'' END,
                CASE WHEN EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM :8) >= 2 THEN 1 ELSE 0 END
            )' USING v_id, v_name, v_salary, v_dept_id, v_hire_date,
                    v_hire_date, v_salary, v_hire_date;
        END LOOP;

        CLOSE p_in_cursor;
        OPEN p_out_cursor FOR 'SELECT * FROM ' || v_temp || ' ORDER BY salary DESC';
    END;

    PROCEDURE proc_consume_cursor(
        p_cursor IN SYS_REFCURSOR,
        p_map IN OUT map_object,
        p_summary OUT VARCHAR2
    ) IS
        v_id        INTEGER;
        v_name      VARCHAR2(100);
        v_salary    NUMERIC(18,2);
        v_level     VARCHAR2(20);
        v_eligible  INTEGER;
        v_count     INTEGER := 0;
        v_total     NUMERIC(18,2) := 0;
        v_senior_count INTEGER := 0;
    BEGIN
        LOOP
            map_object.ad_item(p_map, "Date", func_get_frame_date);
            map_object.ad_item(p_map, "Date-1", func_get_frame_date || '-sss');
            v_level := func_get_frame_date;
            v_level := func_get_frame_date || '-sss';
            FETCH p_cursor INTO v_id, v_name, v_salary, v_level, v_eligible;
            EXIT WHEN p_cursor%NOTFOUND;

            v_count := v_count + 1;
            v_total := v_total + v_salary;
            IF v_level = 'SENIOR' THEN
                v_senior_count := v_senior_count + 1;
            END IF;
        END LOOP;

        CLOSE p_cursor;
        p_summary := 'Total: ' || v_count || ', Senior: ' || v_senior_count ||
                     ', Avg Salary: ' || ROUND(v_total / NULLIF(v_count, 0), 2);
    END;

    PROCEDURE proc_full_pipeline(p_dept_id IN INTEGER) IS
        v_cursor1 SYS_REFCURSOR;
        v_cursor2 SYS_REFCURSOR;
        v_result  VARCHAR2(500);
    BEGIN
        proc_get_raw_cursor(p_dept_id, v_cursor1);
        proc_enhance_cursor(v_cursor1, v_cursor2);
        proc_consume_cursor(v_cursor2, v_result);
        DBE_OUTPUT.PRINT_LINE('Pipeline result: ' || v_result);
    END;

END pkg_cursor_lifecycle;
/

-- ============================================
-- 第十部分：调用示例
-- ============================================

-- 1. 测试 FOR .. IN SELECT
BEGIN
    pkg_for_in_select.proc_sync_employee_bonus();
END;
/

-- 2. 测试 OPEN CURSOR 返回
DECLARE
    v_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR2(100);
    v_salary NUMERIC(18,2);
    v_rank INTEGER;
BEGIN
    pkg_open_cursor.proc_get_employee_cursor(20, 70000, v_cur);
    LOOP
        FETCH v_cur INTO v_id, v_name, v_salary, v_rank;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_id || ' ' || v_name || ' ' || v_salary);
    END LOOP;
    CLOSE v_cur;
END;
/

-- 3. 测试 FOR .. IN 动态SQL
BEGIN
    pkg_dynamic_for_loop.proc_dynamic_for_processing(
        'orders', 'id, customer_id, order_status, total_amount, order_date',
        'order_status IN (''PENDING'',''PROCESSING'') AND total_amount > 100000',
        'total_amount DESC', 'UPDATE'
    );
END;
/

-- 4. 测试游标 USING 参数
DECLARE
    v_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR2(100);
BEGIN
    pkg_cursor_advanced.proc_cursor_dynamic_using(
        'orders', 'PENDING,PROCESSING', 150000, DATE '2024-01-01', v_cur
    );
    LOOP
        FETCH v_cur INTO v_id, v_name;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_id || ' ' || v_name);
    END LOOP;
    CLOSE v_cur;
END;
/

-- 5. 测试函数返回游标
DECLARE
    v_cur pkg_cursor_advanced.cur_order_typed;
    v_rec pkg_cursor_advanced.rec_order_summary;
BEGIN
    v_cur := pkg_cursor_advanced.func_get_order_cursor(2001, DATE '2024-01-01', DATE '2024-12-31');
    LOOP
        FETCH v_cur INTO v_rec;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_rec.order_id || ': ' || v_rec.total_amount);
    END LOOP;
    CLOSE v_cur;
END;
/

-- 6. 测试分页游标
DECLARE
    v_data SYS_REFCURSOR;
    v_total SYS_REFCURSOR;
    v_count INTEGER;
BEGIN
    pkg_cursor_advanced.proc_paginate_with_using(
        'SELECT * FROM products WHERE category_id = :1 AND price < :2',
        5, 1, '[1, 10000]', v_data, v_total
    );
    FETCH v_total INTO v_count;
    CLOSE v_total;
    DBE_OUTPUT.PRINT_LINE('Total: ' || v_count);
END;
/

-- 7. 测试完整流水线
BEGIN
    pkg_cursor_lifecycle.proc_full_pipeline(20);
END;
/
