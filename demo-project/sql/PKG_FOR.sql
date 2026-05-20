-- 高斯/OpenGauss 游标语法示例
-- 示例一：FOR .. IN SELECT ... LOOP
-- 示例二：OPEN CURSOR FOR SELECT ... (SYS_REFCURSOR)

============================================================

-- ============================================
-- 示例一：FOR .. IN SELECT ... LOOP
-- 遍历查询结果集，逐行处理
-- ============================================

CREATE OR REPLACE PACKAGE pkg_for_in_select AS
    -- 定义记录类型
    TYPE rec_employee IS RECORD (
        emp_id      INTEGER,
        emp_name    VARCHAR2(100),
        dept_id     INTEGER,
        salary      NUMERIC(18,2),
        hire_date   DATE
    );

    -- 主过程：使用 FOR .. IN SELECT 遍历员工数据
    PROCEDURE proc_sync_employee_bonus;

    -- 辅助过程：根据部门计算奖金系数
    FUNCTION func_get_bonus_rate(p_dept_id IN INTEGER) RETURN NUMERIC;
END pkg_for_in_select;
/

CREATE OR REPLACE PACKAGE BODY pkg_for_in_select AS

    -- 根据部门返回奖金系数
    FUNCTION func_get_bonus_rate(p_dept_id IN INTEGER) RETURN NUMERIC IS
        v_rate NUMERIC(5,2);
    BEGIN
        CASE p_dept_id
            WHEN 10 THEN v_rate := 0.15;  -- 销售部
            WHEN 20 THEN v_rate := 0.10;  -- 技术部
            WHEN 30 THEN v_rate := 0.08;  -- 财务部
            ELSE v_rate := 0.05;           -- 其他部门
        END CASE;
        RETURN v_rate;
    END;

    -- 主过程：FOR .. IN SELECT 遍历并处理
    PROCEDURE proc_sync_employee_bonus IS
        v_total_bonus NUMERIC(18,2) := 0;
        v_processed   INTEGER := 0;
        v_log_id      INTEGER;
    BEGIN
        -- 记录批次开始
        INSERT INTO batch_log(batch_id, batch_type, start_time, status)
        VALUES (seq_batch_log.NEXTVAL, 'BONUS_CALC', SYSDATE, 'RUNNING')
        RETURNING batch_id INTO v_log_id;

        -- ==========================================
        -- 核心：FOR .. IN SELECT ... LOOP 语法
        -- 隐式声明循环变量 v_emp，类型自动匹配查询列
        -- ==========================================
        FOR v_emp IN (
            SELECT
                e.employee_id,
                e.employee_name,
                e.department_id,
                e.salary,
                e.hire_date,
                d.department_name,
                -- 子查询：获取该员工本年度已发放奖金总额
                (SELECT NVL(SUM(bonus_amount), 0)
                 FROM employee_bonus eb
                 WHERE eb.emp_id = e.employee_id
                 AND eb.bonus_year = EXTRACT(YEAR FROM SYSDATE)
                ) AS year_bonus_total
            FROM employees e
            JOIN departments d ON e.department_id = d.department_id
            WHERE e.status = 'ACTIVE'
              AND e.hire_date <= ADD_MONTHS(SYSDATE, -6)  -- 入职满6个月
            ORDER BY e.department_id, e.salary DESC
        ) LOOP

            DECLARE
                v_bonus_rate  NUMERIC(5,2);
                v_bonus_amt   NUMERIC(18,2);
                v_max_bonus   NUMERIC(18,2);
                v_insert_id   INTEGER;
            BEGIN
                -- 计算奖金
                v_bonus_rate := func_get_bonus_rate(v_emp.department_id);
                v_bonus_amt  := ROUND(v_emp.salary * v_bonus_rate, 2);

                -- 奖金上限：年薪的20%
                v_max_bonus := v_emp.salary * 12 * 0.20;

                IF v_emp.year_bonus_total + v_bonus_amt > v_max_bonus THEN
                    v_bonus_amt := v_max_bonus - v_emp.year_bonus_total;

                    -- 记录超限日志
                    INSERT INTO bonus_limit_log(log_time, emp_id, limit_reason)
                    VALUES (SYSDATE, v_emp.employee_id,
                            'Bonus capped at annual 20% limit');
                END IF;

                -- 只有当奖金大于0时才插入
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

                    -- 每处理100条提交一次，避免大事务
                    IF MOD(v_processed, 100) = 0 THEN
                        COMMIT;
                    END IF;
                END IF;

            EXCEPTION
                WHEN DUP_VAL_ON_INDEX THEN
                    -- 重复记录，更新而非插入
                    UPDATE employee_bonus
                    SET bonus_amount = v_bonus_amt,
                        update_time = SYSDATE
                    WHERE emp_id = v_emp.employee_id
                      AND bonus_year = EXTRACT(YEAR FROM SYSDATE)
                      AND bonus_month = EXTRACT(MONTH FROM SYSDATE);
                WHEN OTHERS THEN
                    -- 记录错误但继续处理下一条
                    INSERT INTO error_log(error_time, procedure_name,
                                         error_code, error_message, context)
                    VALUES (SYSDATE, 'proc_sync_employee_bonus',
                           SQLCODE, SQLERRM,
                           'EmpID=' || v_emp.employee_id);
                    CONTINUE;  -- 高斯支持 CONTINUE 跳过当前迭代
            END;

        END LOOP;

        -- 最终提交剩余数据
        COMMIT;

        -- 更新批次日志
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

============================================================

-- ============================================
-- 示例二：OPEN CURSOR FOR SELECT ... (动态游标)
-- 使用 SYS_REFCURSOR / REF CURSOR 实现动态查询
-- ============================================

CREATE OR REPLACE PACKAGE pkg_open_cursor AS
    -- 定义强类型游标（可选，用于已知结构）
    TYPE cur_employee IS REF CURSOR RETURN employees%ROWTYPE;

    -- 定义弱类型游标（更灵活，高斯/OpenGauss 通用）
    TYPE cur_weak IS REF CURSOR;

    -- 过程1：返回游标给调用者（OUT参数）
    PROCEDURE proc_get_employee_cursor(
        p_dept_id   IN  INTEGER,
        p_min_salary IN  NUMERIC,
        p_result     OUT SYS_REFCURSOR
    );

    -- 过程2：在包内部打开游标并逐行处理
    PROCEDURE proc_process_dynamic_query(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2,
        p_order_by     IN VARCHAR2
    );

    -- 过程3：分页查询，返回多个游标
    PROCEDURE proc_paginated_query(
        p_page_size   IN  INTEGER,
        p_page_num    IN  INTEGER,
        p_data_cursor OUT SYS_REFCURSOR,
        p_count_cursor OUT SYS_REFCURSOR
    );
END pkg_open_cursor;
/

CREATE OR REPLACE PACKAGE BODY pkg_open_cursor AS

    -- ==========================================
    -- 过程1：打开游标并返回给调用者
    -- ==========================================
    PROCEDURE proc_get_employee_cursor(
        p_dept_id    IN  INTEGER,
        p_min_salary IN  NUMERIC,
        p_result     OUT SYS_REFCURSOR
    ) IS
    BEGIN
        -- ==========================================
        -- 核心：OPEN cursor FOR SELECT ...
        -- 动态构造查询，游标在调用者处遍历
        -- ==========================================
        OPEN p_result FOR
            SELECT
                e.employee_id,
                e.employee_name,
                e.salary,
                e.email,
                d.department_name,
                p.project_name,
                -- 分析函数：薪资部门内排名
                RANK() OVER (PARTITION BY e.department_id
                             ORDER BY e.salary DESC) AS dept_salary_rank,
                -- 分析函数：薪资全公司百分位
                PERCENT_RANK() OVER (ORDER BY e.salary) AS salary_percentile
            FROM employees e
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            LEFT JOIN projects p
                ON e.current_project_id = p.project_id
            WHERE e.department_id = p_dept_id
              AND e.salary >= p_min_salary
              AND e.status = 'ACTIVE'
            ORDER BY e.salary DESC;

        -- 注意：游标在此过程内不关闭，由调用者关闭
    END proc_get_employee_cursor;

    -- ==========================================
    -- 过程2：内部打开游标并完全处理
    -- 演示：动态SQL + OPEN FOR + FETCH + CLOSE
    -- ==========================================
    PROCEDURE proc_process_dynamic_query(
        p_table_name   IN VARCHAR2,
        p_where_clause IN VARCHAR2,
        p_order_by     IN VARCHAR2
    ) IS
        -- 弱类型游标变量
        v_cursor      SYS_REFCURSOR;

        -- 动态结果需要显式定义接收变量
        v_rec_id      INTEGER;
        v_rec_name    VARCHAR2(200);
        v_rec_value   NUMERIC(18,2);
        v_rec_status  VARCHAR2(20);
        v_rec_time    TIMESTAMP;

        v_sql         VARCHAR2(4000);
        v_row_count   INTEGER := 0;
        v_batch_size  INTEGER := 500;  -- 批量处理阈值
        v_start_time  TIMESTAMP;
    BEGIN
        v_start_time := SYSTIMESTAMP;

        -- 构造动态SQL（注意防注入，实际生产应使用DBMS_ASSERT）
        v_sql := 'SELECT id, name, amount, status, create_time FROM '
                 || p_table_name;

        IF p_where_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || p_where_clause;
        END IF;

        IF p_order_by IS NOT NULL THEN
            v_sql := v_sql || ' ORDER BY ' || p_order_by;
        END IF;

        -- 记录执行的SQL
        INSERT INTO audit_log(log_time, operation, sql_text, user_name)
        VALUES (SYSTIMESTAMP, 'DYNAMIC_QUERY', v_sql, USER);

        -- ==========================================
        -- 核心：OPEN cursor FOR 动态SQL字符串
        -- ==========================================
        OPEN v_cursor FOR v_sql;

        -- 循环FETCH处理
        LOOP
            -- 提取数据到变量
            FETCH v_cursor INTO v_rec_id, v_rec_name, v_rec_value,
                              v_rec_status, v_rec_time;

            -- 退出条件
            EXIT WHEN v_cursor%NOTFOUND;

            v_row_count := v_row_count + 1;

            -- 业务处理逻辑
            BEGIN
                -- 示例：根据状态做不同处理
                CASE v_rec_status
                    WHEN 'PENDING' THEN
                        -- 更新为处理中
                        EXECUTE IMMEDIATE
                            'UPDATE ' || p_table_name ||
                            ' SET status = ''PROCESSING'', process_time = :1
                             WHERE id = :2'
                        USING SYSTIMESTAMP, v_rec_id;

                    WHEN 'PROCESSING' THEN
                        -- 检查是否超时（超过2小时）
                        IF v_rec_time < SYSTIMESTAMP - INTERVAL '2' HOUR THEN
                            EXECUTE IMMEDIATE
                                'UPDATE ' || p_table_name ||
                                ' SET status = ''TIMEOUT'', retry_count = NVL(retry_count,0)+1
                                 WHERE id = :1'
                            USING v_rec_id;
                        END IF;

                    WHEN 'COMPLETED' THEN
                        -- 归档到历史表
                        INSERT INTO archive_table (id, name, amount, status, archived_time)
                        VALUES (v_rec_id, v_rec_name, v_rec_value, v_rec_status, SYSTIMESTAMP);

                        -- 删除原记录
                        EXECUTE IMMEDIATE
                            'DELETE FROM ' || p_table_name || ' WHERE id = :1'
                        USING v_rec_id;

                    ELSE
                        -- 未知状态，记录异常
                        INSERT INTO exception_log(exception_time, record_id,
                                                 exception_type, detail)
                        VALUES (SYSTIMESTAMP, v_rec_id, 'UNKNOWN_STATUS', v_rec_status);
                END CASE;

                -- 批量提交控制
                IF MOD(v_row_count, v_batch_size) = 0 THEN
                    COMMIT;
                END IF;

            EXCEPTION
                WHEN OTHERS THEN
                    -- 单条记录错误不影响整体
                    INSERT INTO error_log(error_time, context, sqlcode, sqlerrm)
                    VALUES (SYSTIMESTAMP, 'Record ID=' || v_rec_id, SQLCODE, SQLERRM);
                    CONTINUE;
            END;

        END LOOP;

        -- 最终提交
        COMMIT;

        -- ==========================================
        -- 必须关闭游标
        -- ==========================================
        CLOSE v_cursor;

        -- 记录执行统计
        INSERT INTO performance_log(log_time, procedure_name,
                                   rows_processed, elapsed_ms)
        VALUES (SYSTIMESTAMP, 'proc_process_dynamic_query', v_row_count,
                EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start_time)) * 1000);

    EXCEPTION
        WHEN OTHERS THEN
            -- 确保游标被关闭，避免资源泄漏
            IF v_cursor%ISOPEN THEN
                CLOSE v_cursor;
            END IF;
            ROLLBACK;
            RAISE;
    END proc_process_dynamic_query;

    -- ==========================================
    -- 过程3：分页查询，返回两个游标
    -- ==========================================
    PROCEDURE proc_paginated_query(
        p_page_size   IN  INTEGER,
        p_page_num    IN  INTEGER,
        p_data_cursor OUT SYS_REFCURSOR,
        p_count_cursor OUT SYS_REFCURSOR
    ) IS
        v_offset INTEGER;
    BEGIN
        v_offset := (p_page_num - 1) * p_page_size;

        -- 第一个游标：分页数据
        OPEN p_data_cursor FOR
            SELECT
                t.*,
                COUNT(*) OVER () AS total_rows  -- 每行带总数，方便前端
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
            WHERE rn > v_offset
              AND rn <= v_offset + p_page_size
            ORDER BY rn;

        -- 第二个游标：聚合统计
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
-- 调用示例（在Java/应用层或另一个存过中）
-- ============================================

-- 调用示例1：FOR .. IN SELECT
BEGIN
    pkg_for_in_select.proc_sync_employee_bonus();
END;
/

-- 调用示例2：获取游标并在PL/SQL中遍历
DECLARE
    v_cur SYS_REFCURSOR;
    v_emp employees%ROWTYPE;
BEGIN
    pkg_open_cursor.proc_get_employee_cursor(10, 5000, v_cur);

    LOOP
        FETCH v_cur INTO v_emp;
        EXIT WHEN v_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_emp.employee_name || ': ' || v_emp.salary);
    END LOOP;

    CLOSE v_cur;
END;
/

-- 调用示例3：分页查询
DECLARE
    v_data_cur SYS_REFCURSOR;
    v_count_cur SYS_REFCURSOR;
    v_id INTEGER;
    v_name VARCHAR2(100);
    v_total INTEGER;
BEGIN
    pkg_open_cursor.proc_paginated_query(20, 1, v_data_cur, v_count_cur);

    -- 处理数据游标
    LOOP
        FETCH v_data_cur INTO v_id, v_name, v_total;
        EXIT WHEN v_data_cur%NOTFOUND;
        DBE_OUTPUT.PRINT_LINE(v_id || ' ' || v_name);
    END LOOP;
    CLOSE v_data_cur;

    -- 处理统计游标
    -- ...
    CLOSE v_count_cur;
END;
/
