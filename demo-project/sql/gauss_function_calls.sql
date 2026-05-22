
-- ============================================================
-- 高斯/OpenGauss 函数被各种方式调用的完整示例
-- ============================================================

-- ============================================
-- 第一部分：DDL 建表
-- ============================================

DROP TABLE IF EXISTS employees CASCADE;
CREATE TABLE employees (
    emp_id          INTEGER PRIMARY KEY,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2),
    bonus_pct       NUMERIC(5,2),
    hire_date       DATE,
    status          VARCHAR2(20) DEFAULT 'ACTIVE',
    manager_id      INTEGER
);

DROP TABLE IF EXISTS departments CASCADE;
CREATE TABLE departments (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    location        VARCHAR2(100),
    budget          NUMERIC(18,2)
);

DROP TABLE IF EXISTS emp_bonus CASCADE;
CREATE TABLE emp_bonus (
    bonus_id        INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    bonus_amount    NUMERIC(18,2),
    calc_date       TIMESTAMP,
    calc_method     VARCHAR2(50)
);

DROP TABLE IF EXISTS salary_log CASCADE;
CREATE TABLE salary_log (
    log_id          INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_val         VARCHAR2(100),
    new_val         VARCHAR2(100),
    calc_detail     VARCHAR2(500),
    log_time        TIMESTAMP
);

DROP SEQUENCE IF EXISTS seq_bonus;
CREATE SEQUENCE seq_bonus START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_log;
CREATE SEQUENCE seq_log START WITH 1 INCREMENT BY 1;

INSERT INTO departments (dept_id, dept_name, location, budget) VALUES
(10, '销售部', '上海', 5000000),
(20, '技术部', '北京', 8000000),
(30, '财务部', '深圳', 3000000);

INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id) VALUES
(1001, '张三', 10,  8000, 0.10, '2020-03-15', 'ACTIVE', NULL),
(1002, '李四', 20, 12000, 0.08, '2019-06-20', 'ACTIVE', NULL),
(1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001),
(1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003),
(1005, '孙七', 20, 15000, 0.15, '2018-11-01', 'ACTIVE', 1002),
(1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001),
(1007, '吴九', 20, 11000, 0.11, '2020-09-15', 'ACTIVE', 1002),
(1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003);

COMMIT;

-- ============================================
-- 第二部分：各种函数定义
-- ============================================

-- 1. 无参数函数（返回常量或系统值）
CREATE OR REPLACE FUNCTION fn_get_company_name()
RETURN VARCHAR2 IS
BEGIN
    RETURN '华夏科技有限公司';
END;
/

-- 2. 单参数函数
CREATE OR REPLACE FUNCTION fn_calc_years_of_service(p_hire_date IN DATE)
RETURN INTEGER IS
BEGIN
    RETURN EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM p_hire_date);
END;
/

-- 3. 多参数函数
CREATE OR REPLACE FUNCTION fn_calc_bonus(
    p_salary        IN NUMERIC,
    p_bonus_pct     IN NUMERIC,
    p_years         IN INTEGER DEFAULT 1
) RETURN NUMERIC IS
BEGIN
    RETURN ROUND(p_salary * p_bonus_pct * p_years, 2);
END;
/

-- 4. 带 OUT 参数的函数（类似过程）
CREATE OR REPLACE FUNCTION fn_get_emp_details(
    p_emp_id        IN  INTEGER,
    p_name          OUT VARCHAR2,
    p_dept          OUT VARCHAR2,
    p_salary        OUT NUMERIC
) RETURN INTEGER IS
BEGIN
    SELECT e.emp_name, d.dept_name, e.base_salary
    INTO p_name, p_dept, p_salary
    FROM employees e
    JOIN departments d ON e.dept_id = d.dept_id
    WHERE e.emp_id = p_emp_id;
    RETURN 0; -- 成功
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        p_name := NULL; p_dept := NULL; p_salary := NULL;
        RETURN -1; -- 失败
END;
/

CREATE OR REPLACE FUNCTION boyfriend.func_get_frame_date return varchar2 is
  v_sysdate varchar2(10) := '';
BEGIN
select to_char(now(), 'YYYY-MM-DD') INTO v_sysdate FROM sys_dummy;
return v_sysdate;
END ;
/

-- 5. 返回 RECORD 的函数（多行结构）
CREATE OR REPLACE FUNCTION fn_get_dept_summary(p_dept_id IN INTEGER)
RETURN RECORD IS
    TYPE rec_summary IS RECORD (
        dept_name       VARCHAR2(100),
        emp_count       INTEGER,
        total_salary    NUMERIC(18,2),
        avg_salary      NUMERIC(18,2)
    );
    v_rec rec_summary;
BEGIN
    IF '20260110' < func_get_frame_date THEN


        SELECT d.dept_name, COUNT(*), SUM(e.base_salary), AVG(e.base_salary)
        INTO v_rec.dept_name, v_rec.emp_count, v_rec.total_salary, v_rec.avg_salary
        FROM departments d
        LEFT JOIN employees e ON d.dept_id = e.dept_id AND e.status = 'ACTIVE'
        WHERE d.dept_id = p_dept_id
        GROUP BY d.dept_name;
    END IF;
    RETURN v_rec;
END;
/

-- 6. 返回 TABLE 的函数（表值函数 / 集合函数）
CREATE OR REPLACE FUNCTION fn_get_team_members(p_manager_id IN INTEGER)
RETURN TABLE(emp_id INTEGER, emp_name VARCHAR2(100), base_salary NUMERIC(18,2))
IS
BEGIN
    RETURN QUERY
    SELECT e.emp_id, e.emp_name, e.base_salary
    FROM employees e
    WHERE e.manager_id = p_manager_id AND e.status = 'ACTIVE'
    ORDER BY e.base_salary DESC;
END;
/

-- 7. 递归函数（计算阶乘）
CREATE OR REPLACE FUNCTION fn_factorial(p_n IN INTEGER)
RETURN INTEGER IS
BEGIN
    IF p_n <= 1 THEN
        RETURN 1;
    ELSE
        RETURN p_n * fn_factorial(p_n - 1);
    END IF;
END;
/

-- 8. 重载函数（同名不同参数）
CREATE OR REPLACE FUNCTION fn_format_salary(p_salary IN NUMERIC)
RETURN VARCHAR2 IS
BEGIN
    RETURN '¥' || TO_CHAR(p_salary, 'FM999,999,999.00');
END;
/

CREATE OR REPLACE FUNCTION fn_format_salary(p_salary IN NUMERIC, p_currency IN VARCHAR2)
RETURN VARCHAR2 IS
BEGIN
    RETURN p_currency || TO_CHAR(p_salary, 'FM999,999,999.00');
END;
/

-- 9. 确定性函数（标记 DETERMINISTIC，结果可缓存）
CREATE OR REPLACE FUNCTION fn_get_tax_rate(p_salary IN NUMERIC)
RETURN NUMERIC DETERMINISTIC IS
BEGIN
    IF p_salary <= 5000 THEN
        RETURN 0.03;
    ELSIF p_salary <= 10000 THEN
        RETURN 0.10;
    ELSIF p_salary <= 30000 THEN
        RETURN 0.20;
    ELSE
        RETURN 0.25;
    END IF;
END;
/

-- 10. 自治事务函数（日志记录，独立提交）
CREATE OR REPLACE FUNCTION fn_log_salary_change(
    p_emp_id        IN INTEGER,
    p_old_val       IN VARCHAR2,
    p_new_val       IN VARCHAR2,
    p_detail        IN VARCHAR2
) RETURN INTEGER IS
    PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
    INSERT INTO salary_log (log_id, emp_id, old_val, new_val, calc_detail, log_time)
    VALUES (seq_log.NEXTVAL, p_emp_id, p_old_val, p_new_val, p_detail, CURRENT_TIMESTAMP);
    COMMIT;
    RETURN seq_log.CURRVAL;
END;
/

-- 11. 管道函数（逐行返回，适合大数据量）
CREATE OR REPLACE FUNCTION fn_pipe_emp_list(p_dept_id IN INTEGER)
RETURN TABLE(emp_id INTEGER, emp_name VARCHAR2(100), salary_info VARCHAR2(100))
IS
    v_rec RECORD;
BEGIN
    FOR v_rec IN (
        SELECT emp_id, emp_name, base_salary, bonus_pct
        FROM employees
        WHERE dept_id = p_dept_id AND status = 'ACTIVE'
        ORDER BY base_salary DESC
    ) LOOP
        RETURN QUERY SELECT
            v_rec.emp_id,
            v_rec.emp_name,
            fn_format_salary(v_rec.base_salary * (1 + v_rec.bonus_pct));
    END LOOP;
END;
/

-- 12. 聚合函数辅助（配合 GROUP BY 使用）
CREATE OR REPLACE FUNCTION fn_dept_avg_salary(p_dept_id IN INTEGER)
RETURN NUMERIC IS
    v_avg NUMERIC(18,2);
BEGIN
    SELECT AVG(base_salary) INTO v_avg
    FROM employees WHERE dept_id = p_dept_id AND status = 'ACTIVE';
    RETURN v_avg;
END;
/

-- ============================================
-- 第三部分：函数被各种方式调用的演示包
-- ============================================

CREATE OR REPLACE PACKAGE pkg_function_calls AS
    PROCEDURE demo_01_sql_select;           -- 1. SQL SELECT 中调用
    PROCEDURE demo_02_sql_where;            -- 2. SQL WHERE 中调用
    PROCEDURE demo_03_sql_order_by;        -- 3. SQL ORDER BY 中调用
    PROCEDURE demo_04_sql_join;            -- 4. SQL JOIN ON 中调用
    PROCEDURE demo_05_sql_group_by;        -- 5. SQL GROUP BY / HAVING 中调用
    PROCEDURE demo_06_sql_insert_values;   -- 6. INSERT VALUES 中调用
    PROCEDURE demo_07_sql_insert_select;   -- 7. INSERT SELECT 中调用
    PROCEDURE demo_08_sql_update_set;      -- 8. UPDATE SET 中调用
    PROCEDURE demo_09_sql_update_where;    -- 9. UPDATE WHERE 中调用
    PROCEDURE demo_10_sql_delete_where;    -- 10. DELETE WHERE 中调用
    PROCEDURE demo_11_sql_merge;           -- 11. MERGE INTO 中调用
    PROCEDURE demo_12_sql_case_when;       -- 12. CASE WHEN 中调用
    PROCEDURE demo_13_sql_decode_nvl;      -- 13. DECODE / NVL 中调用
    PROCEDURE demo_14_sql_window_func;     -- 14. 窗口函数中调用
    PROCEDURE demo_15_sql_subquery;        -- 15. 子查询中调用
    PROCEDURE demo_16_sql_cte;             -- 16. CTE (WITH) 中调用
    PROCEDURE demo_17_sql_create_table_as; -- 17. CREATE TABLE AS 中调用
    PROCEDURE demo_18_plsql_assignment;    -- 18. PL/SQL 变量赋值
    PROCEDURE demo_19_plsql_if_condition;  -- 19. PL/SQL IF 条件
    PROCEDURE demo_20_plsql_loop;          -- 20. PL/SQL 循环条件
    PROCEDURE demo_21_plsql_for_cursor;    -- 21. FOR 游标中调用
    PROCEDURE demo_22_plsql_exception;     -- 22. EXCEPTION 中调用
    PROCEDURE demo_23_plsql_execute_imm;   -- 23. EXECUTE IMMEDIATE 中调用
    PROCEDURE demo_24_plsql_dynamic_sql;   -- 24. 动态 SQL 构建中调用
    PROCEDURE demo_25_plsql_returning;     -- 25. RETURNING INTO 中调用
    PROCEDURE demo_26_plsql_out_params;    -- 26. 带 OUT 参数的函数
    PROCEDURE demo_27_plsql_nested_call;   -- 27. 函数嵌套调用
    PROCEDURE demo_28_plsql_recursion;     -- 28. 递归调用
    PROCEDURE demo_29_plsql_autonomous;    -- 29. 自治事务函数
    PROCEDURE demo_30_plsql_pipe_table;    -- 30. 管道表函数遍历
    PROCEDURE demo_31_sql_overload;        -- 31. 重载函数调用
    PROCEDURE demo_32_sql_deterministic;   -- 32. 确定性函数缓存效果
    PROCEDURE demo_33_sql_table_function;  -- 33. 表值函数在 FROM 中
    PROCEDURE demo_34_complex_combined;    -- 34. 综合复杂调用链
END pkg_function_calls;
/

CREATE OR REPLACE PACKAGE BODY pkg_function_calls AS

    -- ========== 辅助：显示结果 ==========
    PROCEDURE show_title(p_title VARCHAR2) IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('=== ' || p_title || ' ===');
    END;

    -- ========== 1. SQL SELECT 中调用函数 ==========
    PROCEDURE demo_01_sql_select IS
    BEGIN
        show_title('Demo 1: Function in SQL SELECT');

        -- 直接调用无参函数
        DBE_OUTPUT.PRINT_LINE('Company: ' || fn_get_company_name());

        -- SELECT 列表中调用函数
        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                base_salary,
                fn_calc_years_of_service(hire_date) AS years,
                fn_calc_bonus(base_salary, bonus_pct, 1) AS annual_bonus,
                fn_format_salary(base_salary) AS fmt_salary,
                fn_get_tax_rate(base_salary) AS tax_rate
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Salary:' || RPAD(r.fmt_salary, 15) ||
                ' | Years:' || r.years || ' | Bonus:' || r.annual_bonus ||
                ' | Tax:' || r.tax_rate
            );
        END LOOP;
    END demo_01_sql_select;

    -- ========== 2. SQL WHERE 中调用函数 ==========
    PROCEDURE demo_02_sql_where IS
    BEGIN
        show_title('Demo 2: Function in SQL WHERE');

        -- WHERE 条件中调用函数过滤
        FOR r IN (
            SELECT emp_id, emp_name, hire_date
            FROM employees
            WHERE fn_calc_years_of_service(hire_date) >= 4
              AND status = 'ACTIVE'
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || r.emp_name || ' | Hired:' || r.hire_date ||
                ' | Years:' || fn_calc_years_of_service(r.hire_date)
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('(Above: employees with >= 4 years service)');
    END demo_02_sql_where;

    -- ========== 3. SQL ORDER BY 中调用函数 ==========
    PROCEDURE demo_03_sql_order_by IS
    BEGIN
        show_title('Demo 3: Function in SQL ORDER BY');

        -- ORDER BY 中调用函数排序
        FOR r IN (
            SELECT emp_id, emp_name, base_salary, bonus_pct
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY fn_calc_bonus(base_salary, bonus_pct, 1) DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_name || ' | Bonus:' ||
                fn_calc_bonus(r.base_salary, r.bonus_pct, 1)
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('(Sorted by calculated bonus amount)');
    END demo_03_sql_order_by;

    -- ========== 4. SQL JOIN ON 中调用函数 ==========
    PROCEDURE demo_04_sql_join IS

    BEGIN
        show_title('Demo 4: Function in SQL JOIN ON');

        -- JOIN 条件中调用函数（模拟：按工龄段分组统计）
        FOR r IN (
            SELECT
                e.emp_name,
                e.hire_date,
                CASE
                    WHEN fn_calc_years_of_service(func_get_frame_date) < 2 THEN 'Junior'
                    WHEN fn_calc_years_of_service(e.hire_date) < 5 THEN 'Mid'
                    ELSE 'Senior'
                END AS level
            FROM employees e
            WHERE e.status = 'ACTIVE'
            ORDER BY fn_calc_years_of_service(e.hire_date) DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(r.emp_name || ' | ' || r.hire_date || ' | Level:' || r.level);
        END LOOP;
    END demo_04_sql_join;

    -- ========== 5. SQL GROUP BY / HAVING 中调用函数 ==========
    PROCEDURE demo_05_sql_group_by IS
    BEGIN
        show_title('Demo 5: Function in SQL GROUP BY / HAVING');

        -- GROUP BY 中使用函数分组
        FOR r IN (
            SELECT
                fn_calc_years_of_service(hire_date) AS service_years,
                COUNT(*) AS emp_count,
                AVG(base_salary) AS avg_salary
            FROM employees
            WHERE status = 'ACTIVE'
            GROUP BY fn_calc_years_of_service(hire_date)
            HAVING COUNT(*) >= 1
            ORDER BY service_years
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Years:' || r.service_years || ' | Count:' || r.emp_count ||
                ' | Avg Salary:' || ROUND(r.avg_salary, 2)
            );
        END LOOP;
    END demo_05_sql_group_by;

    -- ========== 6. INSERT VALUES 中调用函数 ==========
    PROCEDURE demo_06_sql_insert_values IS
        v_new_id INTEGER := 1100;
    BEGIN
        show_title('Demo 6: Function in INSERT VALUES');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status)
        VALUES (
            v_new_id,
            'FuncInsert',
            20,
            fn_calc_bonus(10000, 0.10, 12),  -- 函数计算薪资
            fn_get_tax_rate(12000),          -- 函数计算税率作为奖金比例
            CURRENT_DATE - fn_calc_years_of_service('2020-01-01') * 365,  -- 函数计算入职日期
            'ACTIVE'
        );

        DBE_OUTPUT.PRINT_LINE('Inserted emp ' || v_new_id || ' with function-calculated values');

        -- 验证
        SELECT emp_name || ' | Salary:' || base_salary || ' | Bonus%:' || bonus_pct || ' | Hire:' || hire_date
        INTO v_new_id FROM employees WHERE emp_id = v_new_id;
        DBE_OUTPUT.PRINT_LINE(v_new_id);

        ROLLBACK; -- 清理测试数据
    END demo_06_sql_insert_values;

    -- ========== 7. INSERT SELECT 中调用函数 ==========
    PROCEDURE demo_07_sql_insert_select IS
    BEGIN
        show_title('Demo 7: Function in INSERT ... SELECT');

        -- 先清空测试表
        DELETE FROM emp_bonus WHERE calc_method LIKE 'INSERT_SELECT%';

        INSERT INTO emp_bonus (bonus_id, emp_id, bonus_amount, calc_date, calc_method)
        SELECT
            seq_bonus.NEXTVAL,
            emp_id,
            fn_calc_bonus(base_salary, bonus_pct,
                fn_calc_years_of_service(hire_date)),  -- 嵌套函数调用
            CURRENT_TIMESTAMP,
            'INSERT_SELECT: years=' || fn_calc_years_of_service(hire_date)
        FROM employees
        WHERE status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' bonus records via INSERT...SELECT with functions');

        -- 显示结果
        FOR r IN (SELECT emp_id, bonus_amount, calc_method FROM emp_bonus
                  WHERE calc_method LIKE 'INSERT_SELECT%' ORDER BY bonus_id) LOOP
            DBE_OUTPUT.PRINT_LINE('Emp:' || r.emp_id || ' | Bonus:' || r.bonus_amount || ' | ' || r.calc_method);
        END LOOP;
    END demo_07_sql_insert_select;

    -- ========== 8. UPDATE SET 中调用函数 ==========
    PROCEDURE demo_08_sql_update_set IS
    BEGIN
        show_title('Demo 8: Function in UPDATE SET');

        -- 先备份
        UPDATE employees SET base_salary =
            CASE emp_id
                WHEN 1001 THEN 8000 WHEN 1002 THEN 12000 WHEN 1003 THEN 9000
                WHEN 1004 THEN 7000 WHEN 1005 THEN 15000 WHEN 1006 THEN 6500
                WHEN 1007 THEN 11000 WHEN 1008 THEN 8500
            END
        WHERE emp_id BETWEEN 1001 AND 1008;

        -- UPDATE SET 中调用函数
        UPDATE employees
        SET base_salary = base_salary * (1 + fn_get_tax_rate(base_salary)),  -- 函数计算涨幅
            bonus_pct = LEAST(bonus_pct + 0.01 * fn_calc_years_of_service(hire_date), 0.30)
        WHERE status = 'ACTIVE'
          AND fn_calc_years_of_service(hire_date) >= 3;  -- WHERE 中也调用函数

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: SET with function calls');

        -- 显示变化
        FOR r IN (SELECT emp_id, emp_name, base_salary, bonus_pct
                  FROM employees WHERE emp_id BETWEEN 1001 AND 1008 ORDER BY emp_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Salary:' || r.base_salary || ' | Bonus%:' || r.bonus_pct
            );
        END LOOP;
    END demo_08_sql_update_set;

    -- ========== 9. UPDATE WHERE 中调用函数 ==========
    PROCEDURE demo_09_sql_update_where IS
    BEGIN
        show_title('Demo 9: Function in UPDATE WHERE');

        -- 先恢复数据
        UPDATE employees SET status =
            CASE emp_id WHEN 1006 THEN 'INACTIVE' ELSE 'ACTIVE' END
        WHERE emp_id BETWEEN 1001 AND 1008;

        -- WHERE 中调用函数筛选
        UPDATE employees
        SET status = 'PENDING_REVIEW',
            bonus_pct = bonus_pct * 0.5
        WHERE fn_calc_years_of_service(hire_date) < 2
          AND base_salary > fn_dept_avg_salary(dept_id);  -- 函数对比部门平均

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: WHERE with function calls');
    END demo_09_sql_update_where;

    -- ========== 10. DELETE WHERE 中调用函数 ==========
    PROCEDURE demo_10_sql_delete_where IS
    BEGIN
        show_title('Demo 10: Function in DELETE WHERE');

        -- DELETE WHERE 中调用函数
        DELETE FROM emp_bonus
        WHERE calc_method LIKE 'INSERT_SELECT%'
          AND bonus_amount < fn_calc_bonus(5000, 0.05, 1);  -- 函数计算阈值

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' bonus records: WHERE with function');
    END demo_10_sql_delete_where;

    -- ========== 11. MERGE INTO 中调用函数 ==========
    PROCEDURE demo_11_sql_merge IS
    BEGIN
        show_title('Demo 11: Function in MERGE INTO');

        MERGE INTO emp_bonus tgt
        USING (
            SELECT emp_id, base_salary, bonus_pct, hire_date
            FROM employees
            WHERE status = 'ACTIVE'
        ) src
        ON (tgt.emp_id = src.emp_id AND tgt.calc_method = 'MERGE_UPDATE')
        WHEN MATCHED THEN
            UPDATE SET
                bonus_amount = fn_calc_bonus(src.base_salary, src.bonus_pct,
                    fn_calc_years_of_service(src.hire_date)),
                calc_date = CURRENT_TIMESTAMP,
                calc_method = 'MERGE_UPDATE: years=' || fn_calc_years_of_service(src.hire_date)
        WHEN NOT MATCHED THEN
            INSERT (bonus_id, emp_id, bonus_amount, calc_date, calc_method)
            VALUES (
                seq_bonus.NEXTVAL,
                src.emp_id,
                fn_calc_bonus(src.base_salary, src.bonus_pct, 1),
                CURRENT_TIMESTAMP,
                'MERGE_INSERT'
            );

        DBE_OUTPUT.PRINT_LINE('MERGE affected ' || SQL%ROWCOUNT || ' rows with function calls');
    END demo_11_sql_merge;

    -- ========== 12. CASE WHEN 中调用函数 ==========
    PROCEDURE demo_12_sql_case_when IS
    BEGIN
        show_title('Demo 12: Function in CASE WHEN');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                base_salary,
                CASE
                    WHEN fn_calc_years_of_service(hire_date) < 2 THEN fn_calc_bonus(base_salary, 0.05, 1)
                    WHEN fn_calc_years_of_service(hire_date) < 5 THEN fn_calc_bonus(base_salary, 0.10, 1)
                    ELSE fn_calc_bonus(base_salary, 0.15, fn_calc_years_of_service(hire_date))
                END AS tiered_bonus,
                CASE fn_get_tax_rate(base_salary)
                    WHEN 0.03 THEN 'Low'
                    WHEN 0.10 THEN 'Medium'
                    WHEN 0.20 THEN 'High'
                    ELSE 'Very High'
                END AS tax_tier
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Bonus:' || LPAD(TO_CHAR(r.tiered_bonus, 'FM999,999.00'), 10) ||
                ' | Tax Tier:' || r.tax_tier
            );
        END LOOP;
    END demo_12_sql_case_when;

    -- ========== 13. DECODE / NVL 中调用函数 ==========
    PROCEDURE demo_13_sql_decode_nvl IS
    BEGIN
        show_title('Demo 13: Function in DECODE / NVL / COALESCE');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                NVL(fn_format_salary(base_salary), 'N/A') AS fmt_salary,
                DECODE(fn_get_tax_rate(base_salary),
                    0.03, 'Entry Level',
                    0.10, 'Standard',
                    0.20, 'Senior',
                    'Executive') AS level_desc,
                COALESCE(
                    fn_format_salary(base_salary * bonus_pct),
                    'No Bonus'
                ) AS bonus_desc
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | ' || RPAD(r.fmt_salary, 18) ||
                ' | Level:' || RPAD(r.level_desc, 12) ||
                ' | Bonus:' || r.bonus_desc
            );
        END LOOP;
    END demo_13_sql_decode_nvl;

    -- ========== 14. 窗口函数中调用函数 ==========
    PROCEDURE demo_14_sql_window_func IS
    BEGIN
        show_title('Demo 14: Function in Window Functions');

        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                base_salary,
                fn_format_salary(base_salary) AS fmt_sal,
                fn_calc_bonus(base_salary, bonus_pct, 1) AS bonus,
                -- 窗口函数中调用函数
                RANK() OVER (ORDER BY fn_calc_bonus(base_salary, bonus_pct, 1) DESC) AS bonus_rank,
                fn_format_salary(
                    AVG(base_salary) OVER (PARTITION BY dept_id)
                ) AS dept_avg_fmt,
                base_salary - AVG(base_salary) OVER (PARTITION BY dept_id) AS diff_from_avg,
                -- 累计函数中调用
                SUM(fn_calc_bonus(base_salary, bonus_pct, 1)) OVER (
                    ORDER BY hire_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_bonus
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY bonus_rank
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                'Rank:' || LPAD(r.bonus_rank, 2) || ' | ' ||
                RPAD(r.emp_name, 8) || ' | Bonus:' || LPAD(TO_CHAR(r.bonus, 'FM999,999'), 10) ||
                ' | Cumul:' || LPAD(TO_CHAR(r.cumulative_bonus, 'FM999,999'), 12)
            );
        END LOOP;
    END demo_14_sql_window_func;

    -- ========== 15. 子查询中调用函数 ==========
    PROCEDURE demo_15_sql_subquery IS
    BEGIN
        show_title('Demo 15: Function in Subquery');

        -- 标量子查询中调用函数
        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                (SELECT fn_format_salary(AVG(base_salary)) FROM employees e2
                 WHERE e2.dept_id = e1.dept_id) AS dept_avg_fmt
            FROM employees e1
            WHERE status = 'ACTIVE'
              AND base_salary > (
                  SELECT fn_dept_avg_salary(dept_id) FROM employees e3
                  WHERE e3.emp_id = e1.emp_id
              )
            ORDER BY emp_id
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || r.emp_name || ' | Above dept avg | Dept Avg:' || r.dept_avg_fmt
            );
        END LOOP;
    END demo_15_sql_subquery;

    -- ========== 16. CTE (WITH) 中调用函数 ==========
    PROCEDURE demo_16_sql_cte IS
    BEGIN
        show_title('Demo 16: Function in CTE (WITH)');

        FOR r IN (
            WITH emp_with_bonus AS (
                SELECT
                    emp_id,
                    emp_name,
                    dept_id,
                    base_salary,
                    fn_calc_bonus(base_salary, bonus_pct, 1) AS annual_bonus,
                    fn_calc_years_of_service(hire_date) AS years
                FROM employees
                WHERE status = 'ACTIVE'
            ),
            dept_summary AS (
                SELECT
                    dept_id,
                    fn_dept_avg_salary(dept_id) AS avg_sal,
                    COUNT(*) AS emp_count
                FROM employees
                WHERE status = 'ACTIVE'
                GROUP BY dept_id
            )
            SELECT
                e.emp_id,
                e.emp_name,
                d.dept_name,
                e.annual_bonus,
                e.years,
                fn_format_salary(d2.avg_sal) AS dept_avg,
                CASE WHEN e.base_salary > d2.avg_sal THEN 'Above' ELSE 'Below' END AS compare
            FROM emp_with_bonus e
            JOIN departments d ON e.dept_id = d.dept_id
            JOIN dept_summary d2 ON e.dept_id = d2.dept_id
            ORDER BY e.annual_bonus DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | ' || RPAD(r.dept_name, 8) ||
                ' | Bonus:' || LPAD(TO_CHAR(r.annual_bonus, 'FM999,999'), 10) ||
                ' | Years:' || r.years || ' | ' || r.compare || ' Avg'
            );
        END LOOP;
    END demo_16_sql_cte;

    -- ========== 17. CREATE TABLE AS 中调用函数 ==========
    PROCEDURE demo_17_sql_create_table_as IS
    BEGIN
        show_title('Demo 17: Function in CREATE TABLE AS');

        DROP TABLE IF EXISTS tmp_emp_report;

        CREATE TABLE tmp_emp_report AS
        SELECT
            emp_id,
            emp_name,
            dept_id,
            base_salary,
            fn_calc_years_of_service(hire_date) AS service_years,
            fn_calc_bonus(base_salary, bonus_pct, 1) AS annual_bonus,
            fn_get_tax_rate(base_salary) AS tax_rate,
            fn_format_salary(base_salary) AS fmt_salary,
            CASE
                WHEN fn_calc_years_of_service(hire_date) >= 5 THEN 'Senior'
                WHEN fn_calc_years_of_service(hire_date) >= 2 THEN 'Mid'
                ELSE 'Junior'
            END AS emp_level
        FROM employees
        WHERE status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Created tmp_emp_report with ' ||
            (SELECT COUNT(*) FROM tmp_emp_report) || ' rows using functions');

        -- 查看结果
        FOR r IN (SELECT * FROM tmp_emp_report ORDER BY emp_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Level:' || RPAD(r.emp_level, 8) ||
                ' | Bonus:' || LPAD(TO_CHAR(r.annual_bonus, 'FM999,999'), 10)
            );
        END LOOP;

        DROP TABLE IF EXISTS tmp_emp_report;
    END demo_17_sql_create_table_as;

    -- ========== 18. PL/SQL 变量赋值中调用函数 ==========
    PROCEDURE demo_18_plsql_assignment IS
        v_company   VARCHAR2(100);
        v_years     INTEGER;
        v_bonus     NUMERIC(18,2);
        v_tax       NUMERIC(5,2);
        v_fmt       VARCHAR2(50);
    BEGIN
        show_title('Demo 18: Function in PL/SQL Assignment');

        -- 直接赋值
        v_company := fn_get_company_name();
        DBE_OUTPUT.PRINT_LINE('Company: ' || v_company);

        -- 带参数赋值
        v_years := fn_calc_years_of_service(DATE '2019-06-20');
        DBE_OUTPUT.PRINT_LINE('Years of service: ' || v_years);

        -- 多参数赋值
        v_bonus := fn_calc_bonus(12000, 0.10, 2);
        DBE_OUTPUT.PRINT_LINE('Bonus (2 years): ' || v_bonus);

        -- 嵌套函数赋值
        v_bonus := fn_calc_bonus(15000, 0.15, fn_calc_years_of_service(DATE '2018-01-01'));
        DBE_OUTPUT.PRINT_LINE('Nested bonus: ' || v_bonus);

        -- 确定性函数赋值
        v_tax := fn_get_tax_rate(25000);
        DBE_OUTPUT.PRINT_LINE('Tax rate for 25000: ' || v_tax);

        -- 格式化函数赋值
        v_fmt := fn_format_salary(88888.88);
        DBE_OUTPUT.PRINT_LINE('Formatted: ' || v_fmt);

        -- 重载函数赋值
        v_fmt := fn_format_salary(88888.88, '$');
        DBE_OUTPUT.PRINT_LINE('Formatted USD: ' || v_fmt);
    END demo_18_plsql_assignment;

    -- ========== 19. PL/SQL IF 条件中调用函数 ==========
    PROCEDURE demo_19_plsql_if_condition IS
    BEGIN
        show_title('Demo 19: Function in PL/SQL IF Condition');

        FOR r IN (SELECT * FROM employees WHERE status = 'ACTIVE' ORDER BY emp_id) LOOP
            IF fn_calc_years_of_service(r.hire_date) >= 5 THEN
                DBE_OUTPUT.PRINT_LINE(r.emp_name || ' | Senior (>=5 years) | Bonus:' ||
                    fn_calc_bonus(r.base_salary, 0.20, fn_calc_years_of_service(r.hire_date)));
            ELSIF fn_calc_years_of_service(r.hire_date) >= 2 THEN
                DBE_OUTPUT.PRINT_LINE(r.emp_name || ' | Mid (2-5 years) | Bonus:' ||
                    fn_calc_bonus(r.base_salary, 0.10, 1));
            ELSE
                DBE_OUTPUT.PRINT_LINE(r.emp_name || ' | Junior (<2 years) | Bonus:' ||
                    fn_calc_bonus(r.base_salary, 0.05, 1));
            END IF;
        END LOOP;
    END demo_19_plsql_if_condition;

    -- ========== 20. PL/SQL 循环条件中调用函数 ==========
    PROCEDURE demo_20_plsql_loop IS
        v_count     INTEGER := 0;
        v_total     NUMERIC(18,2) := 0;
        v_idx       INTEGER := 1;
        v_fact      INTEGER;
    BEGIN
        show_title('Demo 20: Function in PL/SQL Loop');

        -- WHILE 循环中使用函数作为条件
        DBE_OUTPUT.PRINT_LINE('WHILE loop with function condition:');
        WHILE v_idx <= 5 LOOP
            v_fact := fn_factorial(v_idx);
            DBE_OUTPUT.PRINT_LINE('Factorial(' || v_idx || ') = ' || v_fact);
            v_idx := v_idx + 1;
        END LOOP;

        -- FOR 循环中使用函数计算范围
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('FOR loop with function range:');
        FOR i IN 1..fn_calc_years_of_service(DATE '2018-01-01') LOOP
            DBE_OUTPUT.PRINT_LINE('Year ' || i || ' bonus calc');
        END LOOP;

        -- 游标循环中累计函数结果
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Cursor loop with function accumulation:');
        FOR r IN (SELECT * FROM employees WHERE status = 'ACTIVE') LOOP
            v_total := v_total + fn_calc_bonus(r.base_salary, r.bonus_pct, 1);
            v_count := v_count + 1;
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('Total bonus for ' || v_count || ' employees: ' || v_total);
    END demo_20_plsql_loop;

    -- ========== 21. FOR 游标中调用函数 ==========
    PROCEDURE demo_21_plsql_for_cursor IS
    BEGIN
        show_title('Demo 21: Function in FOR Cursor');

        -- FOR 游标定义中使用函数
        FOR r IN (
            SELECT
                emp_id,
                emp_name,
                fn_calc_years_of_service(hire_date) AS years,
                fn_calc_bonus(base_salary, bonus_pct, 1) AS bonus,
                fn_get_tax_rate(base_salary) AS tax
            FROM employees
            WHERE status = 'ACTIVE'
            ORDER BY fn_calc_bonus(base_salary, bonus_pct, 1) DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | Years:' || r.years || ' | Bonus:' || r.bonus || ' | Tax:' || r.tax
            );
        END LOOP;
    END demo_21_plsql_for_cursor;

    -- ========== 22. EXCEPTION 中调用函数 ==========
    PROCEDURE demo_22_plsql_exception IS
        v_result    NUMERIC;
    BEGIN
        show_title('Demo 22: Function in EXCEPTION Handler');

        BEGIN
            -- 故意触发异常
            SELECT base_salary INTO v_result FROM employees WHERE emp_id = 9999;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                -- EXCEPTION 中调用函数记录日志
                v_result := fn_log_salary_change(9999, 'N/A', 'ERROR',
                    'No employee found | Time:' || TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'));
                DBE_OUTPUT.PRINT_LINE('Logged error via autonomous function, log_id=' || v_result);
            WHEN OTHERS THEN
                v_result := fn_log_salary_change(9999, 'N/A', 'ERROR', SQLERRM);
                DBE_OUTPUT.PRINT_LINE('Logged error: ' || SQLERRM);
        END;

        -- 验证日志已写入（自治事务已提交）
        FOR r IN (SELECT * FROM salary_log WHERE emp_id = 9999 ORDER BY log_id DESC) LOOP
            DBE_OUTPUT.PRINT_LINE('Log: ' || r.calc_detail);
        END LOOP;
    END demo_22_plsql_exception;

    -- ========== 23. EXECUTE IMMEDIATE 中调用函数 ==========
    PROCEDURE demo_23_plsql_execute_imm IS
        v_sql       VARCHAR2(500);
        v_table     VARCHAR2(30) := 'employees';
        v_count     INTEGER;
        v_avg       NUMERIC(18,2);
    BEGIN
        show_title('Demo 23: Function in EXECUTE IMMEDIATE');

        -- 动态 SQL 中嵌入函数调用
        v_sql := 'SELECT COUNT(*), AVG(fn_calc_bonus(base_salary, bonus_pct, 1)) ' ||
                 'FROM ' || v_table || ' WHERE status = :1';

        EXECUTE IMMEDIATE v_sql INTO v_count, v_avg USING 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Dynamic SQL result: Count=' || v_count || ', Avg Bonus=' || ROUND(v_avg, 2));

        -- 动态 SQL 中函数作为参数
        v_sql := 'SELECT emp_name FROM employees WHERE base_salary > :1';
        FOR r IN (
            SELECT emp_name FROM employees WHERE base_salary > fn_dept_avg_salary(20)
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Above dept 20 avg: ' || r.emp_name);
        END LOOP;
    END demo_23_plsql_execute_imm;

    -- ========== 24. 动态 SQL 构建中调用函数 ==========
    PROCEDURE demo_24_plsql_dynamic_sql IS
        v_sql       VARCHAR2(1000);
        v_threshold NUMERIC(18,2);
        v_dept      INTEGER := 20;
    BEGIN
        show_title('Demo 24: Function in Dynamic SQL Building');

        -- 函数计算阈值后拼接 SQL
        v_threshold := fn_dept_avg_salary(v_dept) * 1.2;

        v_sql := 'SELECT emp_id, emp_name, base_salary, ' ||
                 'fn_calc_bonus(base_salary, bonus_pct, 1) AS bonus ' ||
                 'FROM employees ' ||
                 'WHERE dept_id = ' || v_dept || ' ' ||
                 'AND base_salary > ' || v_threshold || ' ' ||
                 'ORDER BY base_salary DESC';

        DBE_OUTPUT.PRINT_LINE('Built SQL: ' || SUBSTR(v_sql, 1, 80) || '...');
        DBE_OUTPUT.PRINT_LINE('Threshold: ' || fn_format_salary(v_threshold));

        -- 执行动态 SQL
        FOR r IN (
            SELECT emp_id, emp_name, base_salary,
                   fn_calc_bonus(base_salary, bonus_pct, 1) AS bonus
            FROM employees
            WHERE dept_id = v_dept AND base_salary > v_threshold
            ORDER BY base_salary DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | Salary:' || r.base_salary ||
                ' | Bonus:' || r.bonus
            );
        END LOOP;
    END demo_24_plsql_dynamic_sql;

    -- ========== 25. RETURNING INTO 中调用函数 ==========
    PROCEDURE demo_25_plsql_returning IS
        v_emp_id    INTEGER := 1200;
        v_bonus     NUMERIC(18,2);
        v_fmt_sal   VARCHAR2(50);
    BEGIN
        show_title('Demo 25: Function in RETURNING INTO');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date)
        VALUES (v_emp_id, 'ReturningTest', 20, 20000, 0.12, CURRENT_DATE)
        RETURNING
            fn_calc_bonus(base_salary, bonus_pct, 1),
            fn_format_salary(base_salary)
        INTO v_bonus, v_fmt_sal;

        DBE_OUTPUT.PRINT_LINE('Inserted emp ' || v_emp_id);
        DBE_OUTPUT.PRINT_LINE('  Bonus (via function): ' || v_bonus);
        DBE_OUTPUT.PRINT_LINE('  Formatted salary: ' || v_fmt_sal);

        ROLLBACK;
    END demo_25_plsql_returning;

    -- ========== 26. 带 OUT 参数的函数 ==========
    PROCEDURE demo_26_plsql_out_params IS
        v_ret       INTEGER;
        v_name      VARCHAR2(100);
        v_dept      VARCHAR2(100);
        v_salary    NUMERIC(18,2);
    BEGIN
        show_title('Demo 26: Function with OUT Parameters');

        -- 调用带 OUT 参数的函数
        v_ret := fn_get_emp_details(1002, v_name, v_dept, v_salary);

        IF v_ret = 0 THEN
            DBE_OUTPUT.PRINT_LINE('Emp 1002: Name=' || v_name || ', Dept=' || v_dept || ', Salary=' || v_salary);
        ELSE
            DBE_OUTPUT.PRINT_LINE('Emp 1002 not found');
        END IF;

        -- 调用不存在的员工
        v_ret := fn_get_emp_details(9999, v_name, v_dept, v_salary);
        IF v_ret = -1 THEN
            DBE_OUTPUT.PRINT_LINE('Emp 9999: Not found (return code -1)');
        END IF;
    END demo_26_plsql_out_params;

    -- ========== 27. 函数嵌套调用 ==========
    PROCEDURE demo_27_plsql_nested_call IS
        v_result    VARCHAR2(200);
        v_bonus     NUMERIC(18,2);
    BEGIN
        show_title('Demo 27: Nested Function Calls');

        -- 3层嵌套：format(calc(years(service_date)))
        v_result := fn_format_salary(
            fn_calc_bonus(
                15000,
                0.10,
                fn_calc_years_of_service(DATE '2018-01-01')
            )
        );
        DBE_OUTPUT.PRINT_LINE('Nested (format(calc(years()))): ' || v_result);

        -- 4层嵌套
        v_result := fn_format_salary(
            fn_calc_bonus(
                20000,
                fn_get_tax_rate(20000),
                fn_calc_years_of_service(DATE '2015-06-01')
            ),
            '$'
        );
        DBE_OUTPUT.PRINT_LINE('4-level nested with overload: ' || v_result);

        -- SQL 中嵌套
        SELECT fn_format_salary(fn_calc_bonus(base_salary, bonus_pct,
            fn_calc_years_of_service(hire_date)))
        INTO v_result
        FROM employees WHERE emp_id = 1005;

        DBE_OUTPUT.PRINT_LINE('SQL nested for emp 1005: ' || v_result);
    END demo_27_plsql_nested_call;

    -- ========== 28. 递归调用 ==========
    PROCEDURE demo_28_plsql_recursion IS
    BEGIN
        show_title('Demo 28: Recursive Function Call');

        DBE_OUTPUT.PRINT_LINE('Factorial calculations:');
        FOR i IN 1..7 LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || i || '! = ' || fn_factorial(i));
        END LOOP;
    END demo_28_plsql_recursion;

    -- ========== 29. 自治事务函数 ==========
    PROCEDURE demo_29_plsql_autonomous IS
        v_log_id    INTEGER;
    BEGIN
        show_title('Demo 29: Autonomous Transaction Function');

        -- 自治事务函数在主事务中调用，独立提交
        v_log_id := fn_log_salary_change(1001, '8000', '9000', 'Main transaction pending');
        DBE_OUTPUT.PRINT_LINE('Autonomous log_id=' || v_log_id || ' committed independently');

        -- 即使主事务回滚，日志仍然保留
        ROLLBACK;
        DBE_OUTPUT.PRINT_LINE('Main transaction rolled back');

        -- 验证日志仍然存在
        FOR r IN (SELECT * FROM salary_log WHERE log_id = v_log_id) LOOP
            DBE_OUTPUT.PRINT_LINE('Log still exists: ' || r.calc_detail || ' at ' || r.log_time);
        END LOOP;
    END demo_29_plsql_autonomous;

    -- ========== 30. 管道表函数遍历 ==========
    PROCEDURE demo_30_plsql_pipe_table IS
    BEGIN
        show_title('Demo 30: Pipe Table Function Iteration');

        -- 遍历表值函数返回的结果集
        DBE_OUTPUT.PRINT_LINE('Manager 1001 team members:');
        FOR r IN (SELECT * FROM TABLE(fn_get_team_members(1001))) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | Salary:' || r.base_salary);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Manager 1002 team members:');
        FOR r IN (SELECT * FROM TABLE(fn_get_team_members(1002))) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | Salary:' || r.base_salary);
        END LOOP;

        -- 管道函数遍历
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Pipe function for dept 20:');
        FOR r IN (SELECT * FROM TABLE(fn_pipe_emp_list(20))) LOOP
            DBE_OUTPUT.PRINT_LINE('  ' || r.emp_id || ' | ' || RPAD(r.emp_name, 8) || ' | ' || r.salary_info);
        END LOOP;
    END demo_30_plsql_pipe_table;

    -- ========== 31. 重载函数调用 ==========
    PROCEDURE demo_31_sql_overload IS
        v_fmt1      VARCHAR2(50);
        v_fmt2      VARCHAR2(50);
    BEGIN
        show_title('Demo 31: Overloaded Function Calls');

        -- 单参数版本
        v_fmt1 := fn_format_salary(12345.67);
        DBE_OUTPUT.PRINT_LINE('Single param: ' || v_fmt1);

        -- 双参数版本（不同签名）
        v_fmt2 := fn_format_salary(12345.67, '€');
        DBE_OUTPUT.PRINT_LINE('Two params: ' || v_fmt2);

        -- SQL 中调用不同重载
        FOR r IN (
            SELECT
                emp_id,
                fn_format_salary(base_salary) AS cny,
                fn_format_salary(base_salary, '$') AS usd
            FROM employees
            WHERE status = 'ACTIVE' AND emp_id <= 1003
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | CNY:' || RPAD(r.cny, 18) || ' | USD:' || r.usd
            );
        END LOOP;
    END demo_31_sql_overload;

    -- ========== 32. 确定性函数缓存效果 ==========
    PROCEDURE demo_32_sql_deterministic IS
        v_start     TIMESTAMP;
        v_end       TIMESTAMP;
        v_result    NUMERIC;
    BEGIN
        show_title('Demo 32: Deterministic Function (Cached)');

        -- 第一次调用（计算）
        v_start := SYSTIMESTAMP;
        FOR i IN 1..1000 LOOP
            v_result := fn_get_tax_rate(15000);
        END LOOP;
        v_end := SYSTIMESTAMP;
        DBE_OUTPUT.PRINT_LINE('1000 calls (first): ' ||
            ROUND(EXTRACT(EPOCH FROM (v_end - v_start)) * 1000, 2) || ' ms');

        -- 第二次调用（缓存命中）
        v_start := SYSTIMESTAMP;
        FOR i IN 1..1000 LOOP
            v_result := fn_get_tax_rate(15000);
        END LOOP;
        v_end := SYSTIMESTAMP;
        DBE_OUTPUT.PRINT_LINE('1000 calls (cached): ' ||
            ROUND(EXTRACT(EPOCH FROM (v_end - v_start)) * 1000, 2) || ' ms');

        DBE_OUTPUT.PRINT_LINE('Tax rate for 15000: ' || v_result);
    END demo_32_sql_deterministic;

    -- ========== 33. 表值函数在 FROM 中 ==========
    PROCEDURE demo_33_sql_table_function IS
    BEGIN
        show_title('Demo 33: Table Function in FROM Clause');

        -- 表值函数作为数据源
        FOR r IN (
            SELECT t.emp_id, t.emp_name, e.base_salary, d.dept_name
            FROM TABLE(fn_get_team_members(1002)) t
            JOIN employees e ON t.emp_id = e.emp_id
            JOIN departments d ON e.dept_id = d.dept_id
            ORDER BY t.base_salary DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.emp_id || ' | ' || RPAD(r.emp_name, 8) ||
                ' | ' || RPAD(r.dept_name, 8) || ' | Salary:' || r.base_salary
            );
        END LOOP;

        -- 表值函数与聚合
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Team 1002 aggregate:');
        FOR r IN (
            SELECT COUNT(*) AS cnt, SUM(base_salary) AS total, AVG(base_salary) AS avg_sal
            FROM TABLE(fn_get_team_members(1002))
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('Count:' || r.cnt || ' | Total:' || r.total || ' | Avg:' || ROUND(r.avg_sal, 2));
        END LOOP;
    END demo_33_sql_table_function;

    -- ========== 34. 综合复杂调用链 ==========
    PROCEDURE demo_34_complex_combined IS
        v_total_bonus   NUMERIC(18,2) := 0;
        v_log_id        INTEGER;
        v_rec           RECORD;
    BEGIN
        show_title('Demo 34: Complex Combined Function Call Chain');

        -- 链式操作：游标 -> 函数计算 -> 自治日志 -> 动态 SQL -> 表值函数
        FOR r IN (
            SELECT
                e.emp_id,
                e.emp_name,
                e.base_salary,
                e.bonus_pct,
                e.hire_date,
                d.dept_name,
                fn_calc_years_of_service(e.hire_date) AS years,
                fn_calc_bonus(e.base_salary, e.bonus_pct,
                    fn_calc_years_of_service(e.hire_date)) AS total_bonus,
                fn_get_tax_rate(e.base_salary) AS tax_rate
            FROM employees e
            JOIN departments d ON e.dept_id = d.dept_id
            WHERE e.status = 'ACTIVE'
            ORDER BY fn_calc_bonus(e.base_salary, e.bonus_pct, 1) DESC
        ) LOOP
            -- 累计奖金
            v_total_bonus := v_total_bonus + r.total_bonus;

            -- 自治事务记录日志
            v_log_id := fn_log_salary_change(r.emp_id,
                TO_CHAR(r.base_salary),
                TO_CHAR(r.base_salary + r.total_bonus),
                'Bonus=' || r.total_bonus || ', Tax=' || r.tax_rate || ', Years=' || r.years);

            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_name, 8) || ' | ' || RPAD(r.dept_name, 8) ||
                ' | Years:' || r.years || ' | Bonus:' || LPAD(TO_CHAR(r.total_bonus, 'FM999,999'), 10) ||
                ' | Log:' || v_log_id
            );
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Total bonus payout: ' || fn_format_salary(v_total_bonus));

        -- 使用表值函数验证团队结构
        DBE_OUTPUT.PRINT_LINE('');
        DBE_OUTPUT.PRINT_LINE('Team structure verification:');
        FOR r IN (
            SELECT m.emp_name AS manager, t.emp_name AS member, t.base_salary
            FROM employees m
            JOIN TABLE(fn_get_team_members(m.emp_id)) t ON 1=1
            WHERE m.emp_id IN (1001, 1002, 1003)
            ORDER BY m.emp_id, t.base_salary DESC
        ) LOOP
            DBE_OUTPUT.PRINT_LINE('  Manager:' || RPAD(r.manager, 8) || ' -> Member:' || r.member || ' | Salary:' || r.base_salary);
        END LOOP;
    END demo_34_complex_combined;

END pkg_function_calls;
/

-- ============================================
-- 第四部分：批量调用所有演示
-- ============================================

BEGIN pkg_function_calls.demo_01_sql_select; END;
/
BEGIN pkg_function_calls.demo_02_sql_where; END;
/
BEGIN pkg_function_calls.demo_03_sql_order_by; END;
/
BEGIN pkg_function_calls.demo_04_sql_join; END;
/
BEGIN pkg_function_calls.demo_05_sql_group_by; END;
/
BEGIN pkg_function_calls.demo_06_sql_insert_values; END;
/
BEGIN pkg_function_calls.demo_07_sql_insert_select; END;
/
BEGIN pkg_function_calls.demo_08_sql_update_set; END;
/
BEGIN pkg_function_calls.demo_09_sql_update_where; END;
/
BEGIN pkg_function_calls.demo_10_sql_delete_where; END;
/
BEGIN pkg_function_calls.demo_11_sql_merge; END;
/
BEGIN pkg_function_calls.demo_12_sql_case_when; END;
/
BEGIN pkg_function_calls.demo_13_sql_decode_nvl; END;
/
BEGIN pkg_function_calls.demo_14_sql_window_func; END;
/
BEGIN pkg_function_calls.demo_15_sql_subquery; END;
/
BEGIN pkg_function_calls.demo_16_sql_cte; END;
/
BEGIN pkg_function_calls.demo_17_sql_create_table_as; END;
/
BEGIN pkg_function_calls.demo_18_plsql_assignment; END;
/
BEGIN pkg_function_calls.demo_19_plsql_if_condition; END;
/
BEGIN pkg_function_calls.demo_20_plsql_loop; END;
/
BEGIN pkg_function_calls.demo_21_plsql_for_cursor; END;
/
BEGIN pkg_function_calls.demo_22_plsql_exception; END;
/
BEGIN pkg_function_calls.demo_23_plsql_execute_imm; END;
/
BEGIN pkg_function_calls.demo_24_plsql_dynamic_sql; END;
/
BEGIN pkg_function_calls.demo_25_plsql_returning; END;
/
BEGIN pkg_function_calls.demo_26_plsql_out_params; END;
/
BEGIN pkg_function_calls.demo_27_plsql_nested_call; END;
/
BEGIN pkg_function_calls.demo_28_plsql_recursion; END;
/
BEGIN pkg_function_calls.demo_29_plsql_autonomous; END;
/
BEGIN pkg_function_calls.demo_30_plsql_pipe_table; END;
/
BEGIN pkg_function_calls.demo_31_sql_overload; END;
/
BEGIN pkg_function_calls.demo_32_sql_deterministic; END;
/
BEGIN pkg_function_calls.demo_33_sql_table_function; END;
/
BEGIN pkg_function_calls.demo_34_complex_combined; END;
/

-- 查看最终日志表
SELECT * FROM salary_log ORDER BY log_id;
