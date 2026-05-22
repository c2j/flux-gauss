
-- ============================================================
-- 高斯/OpenGauss 游标与动态SQL 完整示例集
--
-- 新增内容：
--   1. FOR .. IN EXECUTE IMMEDIATE (动态SQL遍历)
--   2. OPEN cursor FOR 动态SQL + USING 绑定参数
--   3. 返回游标给调用者 (OUT SYS_REFCURSOR)
--   4. 强类型游标 vs 弱类型游标对比
--   5. 游标变量作为函数返回值
-- ============================================================

-- ============================================
-- 示例一扩展：FOR .. IN 执行动态SQL
-- 表名、条件、排序全部动态传入
-- ============================================

CREATE OR REPLACE PACKAGE pkg_dynamic_for_loop AS
    -- 动态遍历并处理，支持任意表结构
    PROCEDURE proc_dynamic_for_processing(
        p_table_name     IN VARCHAR2,      -- 目标表名
        p_select_columns IN VARCHAR2,      -- 要查询的列（逗号分隔）
        p_where_clause   IN VARCHAR2,      -- WHERE条件（不含WHERE关键字）
        p_order_by       IN VARCHAR2,      -- ORDER BY（不含ORDER BY关键字）
        p_process_type   IN VARCHAR2       -- 处理类型：ARCHIVE/UPDATE/DELETE
    );

    -- 动态FOR遍历返回JSON数组（高斯支持JSON类型）
    FUNCTION func_for_dynamic_to_json(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2
    ) RETURN JSON;
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

        -- 动态结果接收（使用通用类型，适用于任何表）
        v_pk_id        INTEGER;
        v_status       VARCHAR2(50);
        v_amount       NUMERIC(18,2);
        v_name         VARCHAR2(200);
        v_create_time  TIMESTAMP;
    BEGIN
        v_start_time := SYSTIMESTAMP;

        -- 构建动态查询SQL
        v_sql := 'SELECT ' || NVL(p_select_columns, '*') || ' FROM ' || p_table_name;

        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        -- 审计日志
        INSERT INTO audit_log(log_time, operation, sql_text, params)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_FOR_LOOP', v_sql,
                'table=' || p_table_name || ',type=' || p_process_type);

        -- ==========================================
        -- 核心：FOR .. IN EXECUTE IMMEDIATE 动态SQL
        -- 高斯支持 EXECUTE IMMEDIATE 返回结果集用于FOR遍历
        -- ==========================================
        FOR v_rec IN EXECUTE IMMEDIATE v_sql
        LOOP
            -- v_rec 是动态行类型，通过列名访问
            -- 注意：动态SQL的列名在编译期不确定，需确保传入列存在

            v_row_count := v_row_count + 1;

            -- 提取关键字段（假设表都有这些标准字段）
            v_pk_id       := v_rec.id;
            v_status      := v_rec.status;
            v_amount      := v_rec.amount;
            v_name        := v_rec.name;
            v_create_time := v_rec.create_time;

            -- 根据处理类型执行不同操作
            CASE p_process_type
                WHEN 'ARCHIVE' THEN
                    -- 归档：插入历史表 + 标记原记录
                    v_process_sql := 'INSERT INTO ' || p_table_name || '_hist
                        SELECT *, :1, :2 FROM ' || p_table_name || ' WHERE id = :3';
                    EXECUTE IMMEDIATE v_process_sql
                        USING SYSTIMESTAMP, 'ARCHIVED', v_pk_id;

                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET status = :1, archive_time = :2 WHERE id = :3'
                        USING 'ARCHIVED', SYSTIMESTAMP, v_pk_id;

                WHEN 'UPDATE' THEN
                    -- 批量更新状态
                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET status = :1, update_time = :2, update_count = NVL(update_count,0)+1
                         WHERE id = :3'
                        USING CASE v_status
                                WHEN 'PENDING' THEN 'PROCESSING'
                                WHEN 'PROCESSING' THEN 'COMPLETED'
                                ELSE v_status
                              END,
                              SYSTIMESTAMP, v_pk_id;

                WHEN 'DELETE' THEN
                    -- 软删除
                    EXECUTE IMMEDIATE 'UPDATE ' || p_table_name ||
                        ' SET is_deleted = :1, delete_time = :2 WHERE id = :3'
                        USING 1, SYSTIMESTAMP, v_pk_id;

                ELSE
                    -- 仅记录，不修改
                    INSERT INTO scan_log(scan_time, table_name, record_id, record_status)
                    VALUES (SYSTIMESTAMP, p_table_name, v_pk_id, v_status);
            END CASE;

            -- 批量提交
            IF MOD(v_row_count, v_batch_commit) = 0 THEN
                COMMIT;
                DBE_OUTPUT.PRINT_LINE('Processed ' || v_row_count || ' rows...');
            END IF;

        END LOOP;

        COMMIT;

        -- 记录性能
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

    -- ==========================================
    -- FOR动态SQL返回JSON（适合REST API场景）
    -- ==========================================
    FUNCTION func_for_dynamic_to_json(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2
    ) RETURN JSON IS
        v_sql    VARCHAR2(4000);
        v_json   JSON := JSON('[]');  -- 初始化JSON数组
        v_item   JSON;
        v_idx    INTEGER := 0;
    BEGIN
        v_sql := 'SELECT * FROM ' || p_table_name;
        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        FOR v_rec IN EXECUTE IMMEDIATE v_sql
        LOOP
            v_idx := v_idx + 1;

            -- 构建JSON对象（动态列访问）
            v_item := JSON();
            -- 使用动态对象属性赋值（高斯JSON支持）
            v_item := json_object(
                'id' VALUE v_rec.id,
                'name' VALUE v_rec.name,
                'status' VALUE v_rec.status,
                'amount' VALUE v_rec.amount,
                'seq' VALUE v_idx
            );

            -- 追加到数组
            v_json := json_append(v_json, v_item);

            -- 限制返回条数，防止内存溢出
            IF v_idx >= 1000 THEN
                EXIT;
            END IF;
        END LOOP;

        RETURN v_json;
    END func_for_dynamic_to_json;

END pkg_dynamic_for_loop;
/


-- ============================================
-- 示例二扩展：OPEN CURSOR + 动态SQL + USING参数
-- 强类型游标与弱类型游标对比
-- ============================================

CREATE OR REPLACE PACKAGE pkg_cursor_advanced AS
    -- 强类型游标：基于表结构定义
    TYPE cur_employee_typed IS REF CURSOR RETURN employees%ROWTYPE;

    -- 强类型游标：基于自定义记录
    TYPE rec_order_summary IS RECORD (
        order_id      INTEGER,
        customer_name VARCHAR2(100),
        total_amount  NUMERIC(18,2),
        item_count    INTEGER,
        order_status  VARCHAR2(20)
    );
    TYPE cur_order_typed IS REF CURSOR RETURN rec_order_summary;

    -- 弱类型游标：通用
    TYPE cur_generic IS REF CURSOR;

    -- ==========================================
    -- 1. 动态SQL + USING绑定参数 + 返回游标
    -- ==========================================
    PROCEDURE proc_cursor_dynamic_using(
        p_table_name    IN  VARCHAR2,
        p_status_list   IN  VARCHAR2,   -- 逗号分隔的状态，如 'PENDING,PROCESSING'
        p_min_amount    IN  NUMERIC,
        p_start_date    IN  DATE,
        p_result        OUT SYS_REFCURSOR
    );

    -- ==========================================
    -- 2. 游标作为函数返回值（强类型）
    -- ==========================================
    FUNCTION func_get_order_cursor(
        p_customer_id IN INTEGER,
        p_date_from   IN DATE,
        p_date_to     IN DATE
    ) RETURN cur_order_typed;

    -- ==========================================
    -- 3. 多游标返回（数据游标 + 游标元信息）
    -- ==========================================
    PROCEDURE proc_multi_cursor_return(
        p_query_id     IN  INTEGER,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_meta_cursor  OUT SYS_REFCURSOR
    );

    -- ==========================================
    -- 4. 游标变量传递：接收游标，加工后返回新游标
    -- ==========================================
    PROCEDURE proc_cursor_transform(
        p_source_cursor IN  SYS_REFCURSOR,
        p_filter_amount IN  NUMERIC,
        p_result        OUT SYS_REFCURSOR
    );

    -- ==========================================
    -- 5. 分页游标：使用USING绑定分页参数
    -- ==========================================
    PROCEDURE proc_paginate_with_using(
        p_base_sql     IN  VARCHAR2,    -- 基础SQL（不含分页）
        p_page_size    IN  INTEGER,
        p_page_num     IN  INTEGER,
        p_bind_values  IN  VARCHAR2,     -- 绑定值JSON数组字符串
        p_data_cursor  OUT SYS_REFCURSOR,
        p_total_cursor OUT SYS_REFCURSOR
    );

END pkg_cursor_advanced;
/

CREATE OR REPLACE PACKAGE BODY pkg_cursor_advanced AS

    -- ==========================================
    -- 1. 动态SQL + USING绑定参数 + 返回游标
    -- ==========================================
    PROCEDURE proc_cursor_dynamic_using(
        p_table_name    IN  VARCHAR2,
        p_status_list   IN  VARCHAR2,
        p_min_amount    IN  NUMERIC,
        p_start_date    IN  DATE,
        p_result        OUT SYS_REFCURSOR
    ) IS
        v_sql           VARCHAR2(4000);
        v_status_array  VARCHAR2_ARRAY;  -- 假设已定义数组类型
        v_status_count  INTEGER;
        v_in_clause     VARCHAR2(1000);
    BEGIN
        -- 解析逗号分隔的状态列表到数组
        v_status_array := string_split(p_status_list, ',');
        v_status_count := array_length(v_status_array);

        -- 构建IN子句的占位符 :1, :2, :3...
        FOR i IN 1..v_status_count LOOP
            IF i > 1 THEN
                v_in_clause := v_in_clause || ',';
            END IF;
            v_in_clause := v_in_clause || ':' || i;
        END LOOP;

        -- 构建完整SQL（使用USING占位符）
        v_sql := 'SELECT
                    t.id,
                    t.name,
                    t.status,
                    t.amount,
                    t.create_time,
                    t.customer_id,
                    c.customer_name,
                    -- 计算字段
                    CASE
                        WHEN t.amount > :' || (v_status_count + 1) || ' THEN ''HIGH''
                        ELSE ''NORMAL''
                    END AS amount_level,
                    -- 日期差
                    EXTRACT(DAY FROM (CURRENT_TIMESTAMP - :' || (v_status_count + 2) || ')) AS days_elapsed
                  FROM ' || p_table_name || ' t
                  LEFT JOIN customers c ON t.customer_id = c.id
                  WHERE t.status IN (' || v_in_clause || ')
                    AND t.amount >= :' || (v_status_count + 1) || '
                    AND t.create_time >= :' || (v_status_count + 2) || '
                  ORDER BY t.amount DESC';

        -- 构建USING参数列表（动态数量）
        -- 高斯支持 EXECUTE IMMEDIATE ... USING 变长参数
        -- 但OPEN FOR USING 需要固定参数，这里演示最多5个状态的方案

        -- ==========================================
        -- 核心：OPEN cursor FOR dynamic_sql USING bind_vars...
        -- 绑定参数防止SQL注入，且利用执行计划缓存
        -- ==========================================
        CASE v_status_count
            WHEN 1 THEN
                OPEN p_result FOR v_sql
                    USING v_status_array[1], p_min_amount, p_start_date;
            WHEN 2 THEN
                OPEN p_result FOR v_sql
                    USING v_status_array[1], v_status_array[2], p_min_amount, p_start_date;
            WHEN 3 THEN
                OPEN p_result FOR v_sql
                    USING v_status_array[1], v_status_array[2], v_status_array[3],
                          p_min_amount, p_start_date;
            WHEN 4 THEN
                OPEN p_result FOR v_sql
                    USING v_status_array[1], v_status_array[2], v_status_array[3],
                          v_status_array[4], p_min_amount, p_start_date;
            WHEN 5 THEN
                OPEN p_result FOR v_sql
                    USING v_status_array[1], v_status_array[2], v_status_array[3],
                          v_status_array[4], v_status_array[5], p_min_amount, p_start_date;
            ELSE
                -- 超过5个状态，退化为字符串拼接（安全性降低）
                v_sql := REPLACE(v_sql, v_in_clause, p_status_list);
                OPEN p_result FOR v_sql USING p_min_amount, p_start_date;
        END CASE;

        -- 记录查询审计
        INSERT INTO audit_log(log_time, operation, sql_text, bind_params)
        VALUES (SYSTIMESTAMP, 'CURSOR_DYNAMIC_USING', v_sql,
                'status=' || p_status_list || ',min=' || p_min_amount || ',date=' || p_start_date);

    END proc_cursor_dynamic_using;

    -- ==========================================
    -- 2. 游标作为函数返回值（强类型）
    -- ==========================================
    FUNCTION func_get_order_cursor(
        p_customer_id IN INTEGER,
        p_date_from   IN DATE,
        p_date_to     IN DATE
    ) RETURN cur_order_typed IS
        v_cursor cur_order_typed;
        v_sql    VARCHAR2(4000);
    BEGIN
        -- 强类型游标：返回结构必须与rec_order_summary完全匹配
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

        -- ==========================================
        -- OPEN强类型游标 FOR 动态SQL USING 参数
        -- ==========================================
        OPEN v_cursor FOR v_sql USING p_customer_id, p_date_from, p_date_to;

        RETURN v_cursor;
        -- 注意：调用方负责关闭此游标
    END func_get_order_cursor;

    -- ==========================================
    -- 3. 多游标返回：数据 + 元信息
    -- ==========================================
    PROCEDURE proc_multi_cursor_return(
        p_query_id     IN  INTEGER,
        p_data_cursor  OUT SYS_REFCURSOR,
        p_meta_cursor  OUT SYS_REFCURSOR
    ) IS
    BEGIN
        -- 第一个游标：实际数据
        OPEN p_data_cursor FOR
            SELECT
                q.*,
                ROW_NUMBER() OVER (ORDER BY q.priority DESC, q.create_time) AS rn
            FROM query_results q
            WHERE q.query_id = :1
            ORDER BY q.priority DESC
            USING p_query_id;

        -- 第二个游标：元信息（列统计、查询参数等）
        OPEN p_meta_cursor FOR
            SELECT
                COUNT(*) AS total_rows,
                MIN(create_time) AS earliest,
                MAX(create_time) AS latest,
                COUNT(DISTINCT status) AS status_distinct_count,
                AVG(CASE WHEN amount IS NOT NULL THEN amount END) AS avg_amount,
                query_params  -- 原始查询参数JSON
            FROM query_results
            WHERE query_id = :1
            GROUP BY query_params
            USING p_query_id;
    END proc_multi_cursor_return;

    -- ==========================================
    -- 4. 游标变量传递：接收游标，过滤后返回新游标
    -- ==========================================
    PROCEDURE proc_cursor_transform(
        p_source_cursor IN  SYS_REFCURSOR,
        p_filter_amount IN  NUMERIC,
        p_result        OUT SYS_REFCURSOR
    ) IS
        v_rec          employees%ROWTYPE;
        v_temp_table   VARCHAR2(30) := 'tmp_cursor_' || TO_CHAR(SYSTIMESTAMP, 'YYYYMMDDHH24MISS');
    BEGIN
        -- 创建临时表存储过滤结果
        EXECUTE IMMEDIATE 'CREATE TEMP TABLE ' || v_temp_table || ' (
            id INTEGER, name VARCHAR2(100), salary NUMERIC(18,2),
            dept_id INTEGER, status VARCHAR2(20)
        )';

        -- 遍历输入游标
        LOOP
            FETCH p_source_cursor INTO v_rec;
            EXIT WHEN p_source_cursor%NOTFOUND;

            -- 过滤条件
            IF v_rec.salary >= p_filter_amount THEN
                EXECUTE IMMEDIATE 'INSERT INTO ' || v_temp_table ||
                    ' VALUES (:1, :2, :3, :4, :5)'
                    USING v_rec.employee_id, v_rec.employee_name,
                          v_rec.salary, v_rec.department_id, v_rec.status;
            END IF;
        END LOOP;

        CLOSE p_source_cursor;

        -- 返回新游标（基于临时表）
        OPEN p_result FOR 'SELECT * FROM ' || v_temp_table || ' ORDER BY salary DESC';

        -- 注意：临时表在会话结束时自动清理
    END proc_cursor_transform;

    -- ==========================================
    -- 5. 分页游标：USING绑定分页参数
    -- ==========================================
    PROCEDURE proc_paginate_with_using(
        p_base_sql     IN  VARCHAR2,
        p_page_size    IN  INTEGER,
        p_page_num     IN  INTEGER,
        p_bind_values  IN  VARCHAR2,     -- JSON格式绑定值
        p_data_cursor  OUT SYS_REFCURSOR,
        p_total_cursor OUT SYS_REFCURSOR
    ) IS
        v_offset       INTEGER;
        v_count_sql    VARCHAR2(4000);
        v_page_sql     VARCHAR2(4000);
        v_total_rows   INTEGER;
    BEGIN
        v_offset := (p_page_num - 1) * p_page_size;

        -- 构建COUNT SQL
        v_count_sql := 'SELECT COUNT(*) FROM (' || p_base_sql || ') t';

        -- 构建分页SQL（高斯LIMIT/OFFSET语法）
        v_page_sql := 'SELECT * FROM (' || p_base_sql || ') t
                       LIMIT :1 OFFSET :2';

        -- ==========================================
        -- OPEN FOR USING 绑定分页参数
        -- :1 = page_size, :2 = offset
        -- ==========================================
        OPEN p_data_cursor FOR v_page_sql USING p_page_size, v_offset;

        -- 总数游标（单行）
        OPEN p_total_cursor FOR v_count_sql;

    END proc_paginate_with_using;

END pkg_cursor_advanced;
/


-- ============================================
-- 示例三：游标变量作为参数在过程间传递
-- 演示游标变量的完整生命周期
-- ============================================

CREATE OR REPLACE PACKAGE pkg_cursor_lifecycle AS
    -- 游标变量类型
    TYPE cur_var IS REF CURSOR;

    -- 获取原始数据游标
    PROCEDURE proc_get_raw_cursor(
        p_dept_id IN INTEGER,
        p_cursor  OUT SYS_REFCURSOR
    );

    -- 加工游标：添加计算列
    PROCEDURE proc_enhance_cursor(
        p_in_cursor  IN  SYS_REFCURSOR,
        p_out_cursor OUT SYS_REFCURSOR
    );

    -- 最终消费游标
    PROCEDURE proc_consume_cursor(
        p_cursor IN SYS_REFCURSOR,
        p_summary OUT VARCHAR2
    );

    -- 完整流水线：获取->加工->消费
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
            WHERE department_id = :1
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
        v_temp     VARCHAR2(30) := 'tmp_enhanced_' || DBMS_RANDOM.STRING('X', 8);
    BEGIN
        -- 创建临时表存储增强数据
        EXECUTE IMMEDIATE 'CREATE TEMP TABLE ' || v_temp || ' (
            employee_id INTEGER,
            employee_name VARCHAR2(100),
            salary NUMERIC(18,2),
            department_id INTEGER,
            hire_date DATE,
            years_of_service NUMERIC(5,2),
            salary_level VARCHAR2(20),
            bonus_eligible INTEGER
        )';

        -- 消费输入游标
        LOOP
            FETCH p_in_cursor INTO v_id, v_name, v_salary, v_dept_id, v_hire_date;
            EXIT WHEN p_in_cursor%NOTFOUND;

            EXECUTE IMMEDIATE 'INSERT INTO ' || v_temp || ' VALUES (
                :1, :2, :3, :4, :5,
                EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM :6),
                CASE WHEN :7 > 50000 THEN ''SENIOR'' ELSE ''JUNIOR'' END,
                CASE WHEN EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM :8) >= 2 THEN 1 ELSE 0 END
            )' USING v_id, v_name, v_salary, v_dept_id, v_hire_date,
                    v_hire_date, v_salary, v_hire_date;
        END LOOP;

        CLOSE p_in_cursor;

        -- 输出增强游标
        OPEN p_out_cursor FOR 'SELECT * FROM ' || v_temp || ' ORDER BY salary DESC';
    END;

    PROCEDURE proc_consume_cursor(
        p_cursor IN SYS_REFCURSOR,
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
            FETCH p_cursor INTO v_id, v_name, v_salary, v_level, v_eligible;
            -- 实际应匹配列数，这里简化
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

    -- 完整流水线
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
-- 调用示例
-- ============================================

-- 1. FOR动态SQL调用
BEGIN
    pkg_dynamic_for_loop.proc_dynamic_for_processing(
        p_table_name     => 'orders',
        p_select_columns => 'id, name, status, amount, create_time',
        p_where_clause   => 'status IN (''PENDING'',''PROCESSING'') AND amount > 1000',
        p_order_by       => 'create_time DESC',
        p_process_type   => 'UPDATE'
    );
END;
/

-- 2. 游标动态SQL + USING
DECLARE
    v_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR2(100);
BEGIN
    pkg_cursor_advanced.proc_cursor_dynamic_using(
        'orders',           -- p_table_name
        'PENDING,PROCESSING', -- p_status_list
        5000,               -- p_min_amount
        DATE '2024-01-01',  -- p_start_date
        v_cur               -- p_result (OUT)
    );

    LOOP
        FETCH v_cur INTO v_id, v_name;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_id || ' ' || v_name);
    END LOOP;
    CLOSE v_cur;
END;
/

-- 3. 函数返回游标
DECLARE
    v_cur pkg_cursor_advanced.cur_order_typed;
    v_rec pkg_cursor_advanced.rec_order_summary;
BEGIN
    v_cur := pkg_cursor_advanced.func_get_order_cursor(1001, DATE '2024-01-01', DATE '2024-12-31');

    LOOP
        FETCH v_cur INTO v_rec;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_rec.order_id || ': ' || v_rec.total_amount);
    END LOOP;
    CLOSE v_cur;
END;
/

-- 4. 多游标返回
DECLARE
    v_data_cur SYS_REFCURSOR;
    v_meta_cur SYS_REFCURSOR;
    v_total INTEGER;
BEGIN
    pkg_cursor_advanced.proc_multi_cursor_return(12345, v_data_cur, v_meta_cur);

    -- 读取元信息
    FETCH v_meta_cur INTO v_total;
    CLOSE v_meta_cur;

    -- 遍历数据
    LOOP
        FETCH v_data_cur INTO v_total;
        EXIT WHEN v_data_cur%NOTFOUND;
    END LOOP;
    CLOSE v_data_cur;
END;
/

-- 5. 分页游标
DECLARE
    v_data SYS_REFCURSOR;
    v_total SYS_REFCURSOR;
    v_count INTEGER;
BEGIN
    pkg_cursor_advanced.proc_paginate_with_using(
        'SELECT * FROM products WHERE category_id = :1 AND price < :2',
        20,     -- page size
        1,      -- page num
        '[1, 100]',  -- bind values JSON: [category_id, max_price]
        v_data,
        v_total
    );

    FETCH v_total INTO v_count;
    CLOSE v_total;
    DBE_OUTPUT.PRINT_LINE('Total rows: ' || v_count);
END;
/
