
-- ============================================================
-- 高斯/OpenGauss UPDATE 语句各种写法汇总存储过程
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
    allowance       NUMERIC(18,2),
    total_salary    NUMERIC(18,2),
    status          VARCHAR2(20) DEFAULT 'ACTIVE',
    manager_id      INTEGER,
    hire_date       DATE,
    last_update     TIMESTAMP,
    update_reason   VARCHAR2(200)
);

DROP TABLE IF EXISTS departments CASCADE;
CREATE TABLE departments (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    location        VARCHAR2(100),
    budget          NUMERIC(18,2),
    manager_id      INTEGER
);

DROP TABLE IF EXISTS salary_history CASCADE;
CREATE TABLE salary_history (
    history_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_salary      NUMERIC(18,2),
    new_salary      NUMERIC(18,2),
    change_date     TIMESTAMP,
    change_reason   VARCHAR2(200)
);

DROP TABLE IF EXISTS emp_performance CASCADE;
CREATE TABLE emp_performance (
    emp_id          INTEGER PRIMARY KEY,
    perf_score      NUMERIC(5,2),
    perf_grade      VARCHAR2(10),
    eval_year       INTEGER
);

DROP SEQUENCE IF EXISTS seq_history;
CREATE SEQUENCE seq_history START WITH 1 INCREMENT BY 1;

INSERT INTO departments (dept_id, dept_name, location, budget, manager_id) VALUES
(10, '销售部', '上海', 5000000, 1001),
(20, '技术部', '北京', 8000000, 1002),
(30, '财务部', '深圳', 3000000, 1003);

INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, total_salary, status, manager_id, hire_date, last_update) VALUES
(1001, '张三', 10,  8000, 0.10,  500,  NULL, 'ACTIVE', NULL, '2020-03-15', '2024-01-01'),
(1002, '李四', 20, 12000, 0.08, 1000,  NULL, 'ACTIVE', NULL, '2019-06-20', '2024-01-01'),
(1003, '王五', 10,  9000, 0.12,  800,  NULL, 'ACTIVE', 1001, '2021-01-10', '2024-01-01'),
(1004, '赵六', 30,  7000, 0.06,  600,  NULL, 'ACTIVE', 1003, '2022-05-08', '2024-01-01'),
(1005, '孙七', 20, 15000, 0.15, 1200,  NULL, 'ACTIVE', 1002, '2018-11-01', '2024-01-01'),
(1006, '周八', 10,  6500, 0.05,  400,  NULL, 'INACTIVE', 1001, '2023-03-20', '2024-01-01'),
(1007, '吴九', 20, 11000, 0.11,  900,  NULL, 'ACTIVE', 1002, '2020-09-15', '2024-01-01'),
(1008, '郑十', 30,  8500, 0.09,  700,  NULL, 'ACTIVE', 1003, '2022-08-01', '2024-01-01');

INSERT INTO emp_performance (emp_id, perf_score, perf_grade, eval_year) VALUES
(1001, 92.5, 'A', 2024),
(1002, 85.0, 'B', 2024),
(1003, 78.0, 'C', 2024),
(1004, 65.0, 'D', 2024),
(1005, 95.0, 'A', 2024),
(1006, 72.0, 'C', 2024),
(1007, 88.0, 'B', 2024),
(1008, 91.0, 'A', 2024);

COMMIT;

-- ============================================
-- 第二部分：UPDATE 各种写法存储过程包
-- ============================================

CREATE OR REPLACE PACKAGE pkg_update_styles AS
    PROCEDURE demo_01_simple_set;                           -- 1. 简单单字段更新
    PROCEDURE demo_02_multi_field;                          -- 2. 多字段逗号分隔更新
    PROCEDURE demo_03_select_subquery;                      -- 3. SET (a,b) = (SELECT ...)
    PROCEDURE demo_04_where_subquery;                       -- 4. WHERE 带子查询
    PROCEDURE demo_05_where_exists;                         -- 5. WHERE EXISTS
    PROCEDURE demo_06_where_in;                             -- 6. WHERE IN 子查询
    PROCEDURE demo_07_correlated_subquery;                  -- 7. 关联子查询
    PROCEDURE demo_08_case_expression;                      -- 8. CASE 表达式更新
    PROCEDURE demo_09_decode_nvl;                           -- 9. DECODE / NVL 函数
    PROCEDURE demo_10_from_clause;                          -- 10. UPDATE ... FROM（高斯扩展）
    PROCEDURE demo_11_join_update;                          -- 11. JOIN 更新
    PROCEDURE demo_12_with_cte;                             -- 12. WITH (CTE) 更新
    PROCEDURE demo_13_returning_into;                       -- 13. RETURNING INTO
    PROCEDURE demo_14_dynamic_sql;                          -- 14. EXECUTE IMMEDIATE 动态更新
    PROCEDURE demo_15_bulk_collect;                         -- 15. BULK COLLECT 批量更新
    PROCEDURE demo_16_rowtype_update;                       -- 16. %ROWTYPE 更新
    PROCEDURE demo_17_merger_style;                         -- 17. MERGE INTO 模拟 UPSERT
    PROCEDURE demo_18_partition_update;                     -- 18. 分区表更新
    PROCEDURE demo_19_window_function;                      -- 19. 窗口函数子查询更新
    PROCEDURE demo_20_complex_combined;                     -- 20. 综合复杂更新

    -- 辅助：重置数据
    PROCEDURE proc_reset_data;
    -- 辅助：显示当前数据
    PROCEDURE proc_show_data(p_title IN VARCHAR2);
END pkg_update_styles;
/

CREATE OR REPLACE PACKAGE BODY pkg_update_styles AS

    -- ========== 辅助过程 ==========
    PROCEDURE proc_reset_data IS
    BEGIN
        UPDATE employees SET
            base_salary = CASE emp_id
                WHEN 1001 THEN 8000  WHEN 1002 THEN 12000 WHEN 1003 THEN 9000  WHEN 1004 THEN 7000
                WHEN 1005 THEN 15000 WHEN 1006 THEN 6500  WHEN 1007 THEN 11000 WHEN 1008 THEN 8500
            END,
            bonus_pct = CASE emp_id
                WHEN 1001 THEN 0.10 WHEN 1002 THEN 0.08 WHEN 1003 THEN 0.12 WHEN 1004 THEN 0.06
                WHEN 1005 THEN 0.15 WHEN 1006 THEN 0.05 WHEN 1007 THEN 0.11 WHEN 1008 THEN 0.09
            END,
            allowance = CASE emp_id
                WHEN 1001 THEN 500  WHEN 1002 THEN 1000 WHEN 1003 THEN 800  WHEN 1004 THEN 600
                WHEN 1005 THEN 1200 WHEN 1006 THEN 400  WHEN 1007 THEN 900  WHEN 1008 THEN 700
            END,
            total_salary = NULL,
            status = CASE emp_id WHEN 1006 THEN 'INACTIVE' ELSE 'ACTIVE' END,
            last_update = '2024-01-01',
            update_reason = 'Initial';
        COMMIT;
        DBE_OUTPUT.PRINT_LINE('Data reset.');
    END proc_reset_data;

    PROCEDURE proc_show_data(p_title IN VARCHAR2) IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== ' || p_title || ' ===');
        DBE_OUTPUT.PRINT_LINE(RPAD('ID', 6) || RPAD('Name', 10) || RPAD('Dept', 6) ||
                             RPAD('Base', 12) || RPAD('Bonus%', 8) || RPAD('Allow', 10) ||
                             RPAD('Total', 12) || RPAD('Status', 10) || 'Reason');
        DBE_OUTPUT.PRINT_LINE('--------------------------------------------------------------------------------');
        FOR r IN (SELECT * FROM employees ORDER BY emp_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_id, 6) || RPAD(r.emp_name, 10) || RPAD(r.dept_id, 6) ||
                RPAD(TO_CHAR(r.base_salary, 'FM999,999'), 12) ||
                RPAD(TO_CHAR(r.bonus_pct, 'FM0.00'), 8) ||
                RPAD(TO_CHAR(r.allowance, 'FM999,999'), 10) ||
                RPAD(NVL(TO_CHAR(r.total_salary, 'FM999,999'), 'NULL'), 12) ||
                RPAD(r.status, 10) || SUBSTR(r.update_reason, 1, 30)
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('');
    END proc_show_data;

    -- ========== 1. 简单单字段更新 ==========
    PROCEDURE demo_01_simple_set IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 1: Simple single field UPDATE ---');

        UPDATE employees
        SET last_update = CURRENT_TIMESTAMP
        WHERE status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: last_update = CURRENT_TIMESTAMP');
    END demo_01_simple_set;

    -- ========== 2. 多字段逗号分隔更新 ==========
    PROCEDURE demo_02_multi_field IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 2: Multi-field comma separated UPDATE ---');

        UPDATE employees
        SET base_salary = base_salary * 1.10,
            bonus_pct = bonus_pct + 0.02,
            allowance = allowance + 500,
            last_update = CURRENT_TIMESTAMP,
            update_reason = 'Annual raise: base+10%, bonus+2%, allow+500'
        WHERE status = 'ACTIVE' AND dept_id = 10;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: multi-field comma style');
    END demo_02_multi_field;

    -- ========== 3. SET (a,b,c) = (SELECT ...) ==========
    PROCEDURE demo_03_select_subquery IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 3: SET (a,b,c) = (SELECT a1,b1,c1 FROM ...) ---');

        UPDATE employees
        SET (base_salary, bonus_pct, allowance) = (
            SELECT
                e.base_salary * 1.15,
                LEAST(e.bonus_pct + 0.03, 0.30),
                e.allowance + 1000
            FROM employees e
            WHERE e.emp_id = employees.emp_id
        )
        WHERE dept_id = 20
          AND EXISTS (SELECT 1 FROM emp_performance p
                      WHERE p.emp_id = employees.emp_id AND p.perf_grade IN ('A', 'B'));

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: SET = (SELECT ...)');
    END demo_03_select_subquery;

    -- ========== 4. WHERE 带子查询 ==========
    PROCEDURE demo_04_where_subquery IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 4: WHERE with scalar subquery ---');

        UPDATE employees
        SET base_salary = base_salary * 1.20,
            update_reason = 'Above dept avg raise: base+20%'
        WHERE base_salary > (
            SELECT AVG(base_salary) FROM employees e2 WHERE e2.dept_id = employees.dept_id
        );

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: WHERE > scalar subquery');
    END demo_04_where_subquery;

    -- ========== 5. WHERE EXISTS ==========
    PROCEDURE demo_05_where_exists IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 5: WHERE EXISTS subquery ---');

        UPDATE employees
        SET status = 'PENDING_REVIEW',
            update_reason = 'Low performance review required'
        WHERE EXISTS (
            SELECT 1 FROM emp_performance p
            WHERE p.emp_id = employees.emp_id
              AND p.perf_score < 75
              AND p.eval_year = 2024
        )
        AND status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: WHERE EXISTS');
    END demo_05_where_exists;

    -- ========== 6. WHERE IN 子查询 ==========
    PROCEDURE demo_06_where_in IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 6: WHERE IN (subquery) ---');

        UPDATE employees
        SET allowance = allowance + 2000,
            update_reason = 'Top performer bonus allowance'
        WHERE emp_id IN (
            SELECT emp_id FROM emp_performance
            WHERE perf_grade = 'A' AND eval_year = 2024
        );

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: WHERE IN (subquery)');
    END demo_06_where_in;

    -- ========== 7. 关联子查询（Correlated Subquery） ==========
    PROCEDURE demo_07_correlated_subquery IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 7: Correlated subquery in SET ---');

        UPDATE employees e1
        SET total_salary = (
            SELECT e2.base_salary * (1 + e2.bonus_pct) + e2.allowance
            FROM employees e2
            WHERE e2.emp_id = e1.emp_id
        ) * (
            SELECT CASE WHEN p.perf_grade = 'A' THEN 1.10
                        WHEN p.perf_grade = 'B' THEN 1.05
                        ELSE 1.00 END
            FROM emp_performance p
            WHERE p.emp_id = e1.emp_id
        )
        WHERE e1.status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: correlated subquery in SET');
    END demo_07_correlated_subquery;

    -- ========== 8. CASE 表达式更新 ==========
    PROCEDURE demo_08_case_expression IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 8: CASE expression in SET ---');

        UPDATE employees
        SET base_salary = CASE
                WHEN dept_id = 10 THEN base_salary * 1.15
                WHEN dept_id = 20 THEN base_salary * 1.12
                WHEN dept_id = 30 THEN base_salary * 1.08
                ELSE base_salary * 1.05
            END,
            bonus_pct = CASE perf_grade
                WHEN 'A' THEN LEAST(bonus_pct + 0.05, 0.30)
                WHEN 'B' THEN LEAST(bonus_pct + 0.03, 0.25)
                WHEN 'C' THEN bonus_pct + 0.01
                ELSE bonus_pct
            END,
            update_reason = 'CASE-based adjustment by dept and perf'
        FROM (SELECT emp_id, perf_grade FROM emp_performance WHERE eval_year = 2024) p
        WHERE employees.emp_id = p.emp_id;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: CASE expression');
    END demo_08_case_expression;

    -- ========== 9. DECODE / NVL / COALESCE ==========
    PROCEDURE demo_09_decode_nvl IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 9: DECODE / NVL / COALESCE functions ---');

        UPDATE employees
        SET total_salary = COALESCE(total_salary, 0) +
                           NVL(allowance, 0) *
                           DECODE(status, 'ACTIVE', 1.2, 'INACTIVE', 0.8, 1.0),
            update_reason = 'DECODE status multiplier: Active=1.2, Inactive=0.8'
        WHERE total_salary IS NULL OR total_salary = 0;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: DECODE/NVL/COALESCE');
    END demo_09_decode_nvl;

    -- ========== 10. UPDATE ... FROM（高斯扩展语法） ==========
    PROCEDURE demo_10_from_clause IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 10: UPDATE ... FROM (Gauss extension) ---');

        UPDATE employees
        SET base_salary = d.budget * 0.001 + e.base_salary * 0.05,
            update_reason = 'FROM clause: budget-based adjustment'
        FROM employees e, departments d
        WHERE employees.emp_id = e.emp_id
          AND e.dept_id = d.dept_id
          AND d.budget > 4000000;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: UPDATE ... FROM');
    END demo_10_from_clause;

    -- ========== 11. JOIN 更新 ==========
    PROCEDURE demo_11_join_update IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 11: JOIN UPDATE ---');

        UPDATE employees e
        SET e.allowance = e.allowance + p.perf_score * 10,
            e.update_reason = 'JOIN perf: allowance + score*10'
        FROM emp_performance p
        WHERE e.emp_id = p.emp_id
          AND p.eval_year = 2024
          AND p.perf_score >= 85;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: JOIN UPDATE');
    END demo_11_join_update;

    -- ========== 12. WITH (CTE) 更新 ==========
    PROCEDURE demo_12_with_cte IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 12: WITH (CTE) UPDATE ---');

        WITH dept_stats AS (
            SELECT dept_id,
                   AVG(base_salary) AS avg_sal,
                   MAX(base_salary) AS max_sal,
                   COUNT(*) AS emp_count
            FROM employees
            GROUP BY dept_id
        ),
        low_earners AS (
            SELECT e.emp_id, e.emp_name, e.base_salary, d.avg_sal, d.emp_count
            FROM employees e
            JOIN dept_stats d ON e.dept_id = d.dept_id
            WHERE e.base_salary < d.avg_sal
        )
        UPDATE employees
        SET base_salary = base_salary + (SELECT avg_sal - base_salary FROM low_earners l WHERE l.emp_id = employees.emp_id) * 0.5,
            update_reason = 'CTE adjustment: below dept avg'
        WHERE emp_id IN (SELECT emp_id FROM low_earners);

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: WITH (CTE) UPDATE');
    END demo_12_with_cte;

    -- ========== 13. RETURNING INTO ==========
    PROCEDURE demo_13_returning_into IS
        v_emp_id    INTEGER;
        v_old_base  NUMERIC(18,2);
        v_new_base  NUMERIC(18,2);
        v_name      VARCHAR2(100);
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 13: RETURNING INTO ---');

        UPDATE employees
        SET base_salary = base_salary * 1.25,
            update_reason = 'RETURNING demo: +25%'
        WHERE emp_id = 1001
        RETURNING emp_id, emp_name, base_salary / 1.25, base_salary
        INTO v_emp_id, v_name, v_old_base, v_new_base;

        DBE_OUTPUT.PRINT_LINE('Updated emp ' || v_emp_id || ' (' || v_name || ')');
        DBE_OUTPUT.PRINT_LINE('  Old base: ' || v_old_base || ' -> New base: ' || v_new_base);
    END demo_13_returning_into;

    -- ========== 14. EXECUTE IMMEDIATE 动态更新 ==========
    PROCEDURE demo_14_dynamic_sql IS
        v_sql       VARCHAR2(1000);
        v_table     VARCHAR2(30) := 'employees';
        v_col       VARCHAR2(30) := 'base_salary';
        v_factor    NUMERIC(5,2) := 1.08;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 14: EXECUTE IMMEDIATE dynamic UPDATE ---');

        v_sql := 'UPDATE ' || v_table ||
                 ' SET ' || v_col || ' = ' || v_col || ' * :1, ' ||
                 ' update_reason = :2 ' ||
                 ' WHERE dept_id = :3 AND status = :4';

        EXECUTE IMMEDIATE v_sql
            USING v_factor, 'Dynamic SQL: factor=' || v_factor, 30, 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: EXECUTE IMMEDIATE');
    END demo_14_dynamic_sql;

    -- ========== 15. BULK COLLECT 批量更新 ==========
    PROCEDURE demo_15_bulk_collect IS
        TYPE t_emp_ids IS TABLE OF employees.emp_id%TYPE;
        TYPE t_salaries IS TABLE OF employees.base_salary%TYPE;
        v_ids       t_emp_ids;
        v_new_sals  t_salaries;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 15: BULK COLLECT with FORALL UPDATE ---');

        -- 收集需要更新的员工ID和新薪资
        SELECT emp_id, base_salary * 1.05
        BULK COLLECT INTO v_ids, v_new_sals
        FROM employees
        WHERE status = 'ACTIVE' AND perf_score > 80;

        -- 高斯 FORALL 支持批量更新
        FORALL i IN 1..v_ids.COUNT
            UPDATE employees
            SET base_salary = v_new_sals(i),
                update_reason = 'BULK: +5% for score>80'
            WHERE emp_id = v_ids(i);

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: BULK COLLECT + FORALL');
    END demo_15_bulk_collect;

    -- ========== 16. %ROWTYPE 更新 ==========
    PROCEDURE demo_16_rowtype_update IS
        v_emp_rec   employees%ROWTYPE;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 16: %ROWTYPE update ---');

        SELECT * INTO v_emp_rec FROM employees WHERE emp_id = 1005;

        v_emp_rec.base_salary := v_emp_rec.base_salary * 1.30;
        v_emp_rec.bonus_pct := 0.25;
        v_emp_rec.allowance := v_emp_rec.allowance + 3000;
        v_emp_rec.last_update := CURRENT_TIMESTAMP;
        v_emp_rec.update_reason := 'ROWTYPE update: +30%, bonus=25%, allow+3000';

        UPDATE employees
        SET ROW = v_emp_rec
        WHERE emp_id = v_emp_rec.emp_id;

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: SET ROW = %ROWTYPE');
    END demo_16_rowtype_update;

    -- ========== 17. MERGE INTO 模拟 UPSERT ==========
    PROCEDURE demo_17_merger_style IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 17: MERGE INTO (UPSERT style) ---');

        MERGE INTO salary_history h
        USING (SELECT emp_id, base_salary FROM employees WHERE status = 'ACTIVE') e
        ON (h.emp_id = e.emp_id AND h.change_date >= CURRENT_DATE - INTERVAL '1' DAY)
        WHEN MATCHED THEN
            UPDATE SET old_salary = h.new_salary,
                      new_salary = e.base_salary,
                      -- change_date = CURRENT_TIMESTAMP,
                      change_reason = 'MERGE update'
        WHEN NOT MATCHED THEN
            INSERT (history_id, emp_id, old_salary, new_salary, change_date, change_reason)
            VALUES (seq_history.NEXTVAL, e.emp_id, 0, e.base_salary, CURRENT_TIMESTAMP, 'MERGE insert');

        DBE_OUTPUT.PRINT_LINE('MERGE affected ' || SQL%ROWCOUNT || ' rows');
    END demo_17_merger_style;

    -- ========== 18. 分区表更新（模拟） ==========
    PROCEDURE demo_18_partition_update IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 18: Partition-wise UPDATE (simulated) ---');

        -- 模拟按部门分区更新
        UPDATE employees
        SET base_salary = base_salary * 1.10,
            update_reason = 'Partition: dept 10 batch'
        WHERE dept_id = 10
          AND status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Partition 10 updated: ' || SQL%ROWCOUNT || ' rows');

        UPDATE employees
        SET base_salary = base_salary * 1.08,
            update_reason = 'Partition: dept 20 batch'
        WHERE dept_id = 20
          AND status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Partition 20 updated: ' || SQL%ROWCOUNT || ' rows');
    END demo_18_partition_update;

    -- ========== 19. 窗口函数子查询更新 ==========
    PROCEDURE demo_19_window_function IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 19: Window function in subquery UPDATE ---');

        UPDATE employees
        SET bonus_pct = CASE
            WHEN sal_rank <= 2 THEN LEAST(bonus_pct + 0.05, 0.30)
            WHEN sal_rank <= 5 THEN LEAST(bonus_pct + 0.02, 0.20)
            ELSE bonus_pct
        END,
        update_reason = 'Window rank: top2 +5%, top5 +2%'
        FROM (
            SELECT emp_id,
                   RANK() OVER (PARTITION BY dept_id ORDER BY base_salary DESC) AS sal_rank
            FROM employees
        ) r
        WHERE employees.emp_id = r.emp_id
          AND employees.status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Updated ' || SQL%ROWCOUNT || ' rows: window function');
    END demo_19_window_function;

    -- ========== 20. 综合复杂更新 ==========
    PROCEDURE demo_20_complex_combined IS
        v_affected  INTEGER := 0;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 20: Complex combined UPDATE ---');

        -- 步骤1：用子查询计算新值
        UPDATE employees e
        SET (base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) = (
            SELECT
                -- 基本工资：部门标准 * 绩效系数 * 年限系数
                ROUND(
                    e.base_salary *
                    (1 + NVL(d.budget, 0) / 100000000) *  -- 部门预算影响
                    DECODE(p.perf_grade, 'A', 1.20, 'B', 1.10, 'C', 1.00, 0.90) *  -- 绩效
                    (1 + EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM e.hire_date)) * 0.01,  -- 年限
                    2
                ),
                -- 奖金比例
                LEAST(
                    e.bonus_pct +
                    DECODE(p.perf_grade, 'A', 0.05, 'B', 0.03, 'C', 0.01, 0) +
                    CASE WHEN e.dept_id = 10 THEN 0.02 ELSE 0 END,
                    0.30
                ),
                -- 津贴
                e.allowance +
                CASE WHEN p.perf_score >= 90 THEN 3000
                     WHEN p.perf_score >= 80 THEN 1500
                     ELSE 500 END +
                NVL((SELECT COUNT(*) FROM employees m WHERE m.manager_id = e.emp_id), 0) * 500,  -- 管理津贴
                -- 总薪资（重新计算）
                NULL,  -- 稍后计算
                CURRENT_TIMESTAMP,
                'Complex: budget=' || TO_CHAR(d.budget) || ', perf=' || p.perf_grade ||
                ', score=' || TO_CHAR(p.perf_score) || ', years=' ||
                TO_CHAR(EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM e.hire_date))
            FROM departments d, emp_performance p
            WHERE d.dept_id = e.dept_id
              AND p.emp_id = e.emp_id
              AND p.eval_year = 2024
        )
        WHERE e.status = 'ACTIVE'
          AND EXISTS (SELECT 1 FROM emp_performance p2 WHERE p2.emp_id = e.emp_id AND p2.eval_year = 2024)
          AND EXISTS (SELECT 1 FROM departments d2 WHERE d2.dept_id = e.dept_id);

        v_affected := SQL%ROWCOUNT;

        -- 步骤2：用关联子查询更新 total_salary
        UPDATE employees
        SET total_salary = ROUND(
            base_salary * (1 + bonus_pct) + allowance +
            CASE WHEN manager_id IS NOT NULL THEN 2000 ELSE 0 END,
            2
        )
        WHERE status = 'ACTIVE';

        DBE_OUTPUT.PRINT_LINE('Complex update: ' || v_affected || ' rows (multi-step)');
    END demo_20_complex_combined;

END pkg_update_styles;
/

-- ============================================
-- 第三部分：批量调用所有演示
-- ============================================

BEGIN
    pkg_update_styles.proc_reset_data;
    pkg_update_styles.proc_show_data('Initial Data');
END;
/

BEGIN pkg_update_styles.demo_01_simple_set; END;
/
BEGIN pkg_update_styles.demo_02_multi_field; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 1-2'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_03_select_subquery; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 3'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_04_where_subquery; END;
/
BEGIN pkg_update_styles.demo_05_where_exists; END;
/
BEGIN pkg_update_styles.demo_06_where_in; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 4-6'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_07_correlated_subquery; END;
/
BEGIN pkg_update_styles.demo_08_case_expression; END;
/
BEGIN pkg_update_styles.demo_09_decode_nvl; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 7-9'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_10_from_clause; END;
/
BEGIN pkg_update_styles.demo_11_join_update; END;
/
BEGIN pkg_update_styles.demo_12_with_cte; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 10-12'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_13_returning_into; END;
/
BEGIN pkg_update_styles.demo_14_dynamic_sql; END;
/
BEGIN pkg_update_styles.demo_15_bulk_collect; END;
/
BEGIN pkg_update_styles.demo_16_rowtype_update; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 13-16'); END;
/

BEGIN pkg_update_styles.proc_reset_data; END;
/
BEGIN pkg_update_styles.demo_17_merger_style; END;
/
BEGIN pkg_update_styles.demo_18_partition_update; END;
/
BEGIN pkg_update_styles.demo_19_window_function; END;
/
BEGIN pkg_update_styles.demo_20_complex_combined; END;
/
BEGIN pkg_update_styles.proc_show_data('After Demo 17-20 (Final)'); END;
/

-- 查看历史记录表
SELECT * FROM salary_history ORDER BY history_id;
