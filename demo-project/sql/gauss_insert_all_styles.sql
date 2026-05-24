-- NOTE: DDL moved to ddl/*.sql



CREATE OR REPLACE PACKAGE pkg_insert_styles AS
    PROCEDURE demo_01_simple_values;                        -- 1. 简单 VALUES
    PROCEDURE demo_02_multi_rows;                           -- 2. 多行 VALUES
    PROCEDURE demo_03_insert_select;                        -- 3. INSERT ... SELECT
    PROCEDURE demo_04_insert_select_join;                   -- 4. INSERT ... SELECT JOIN
    PROCEDURE demo_05_insert_with;                          -- 5. INSERT ... WITH (CTE)
    PROCEDURE demo_06_insert_all;                           -- 6. INSERT ALL（多表）
    PROCEDURE demo_07_insert_returning;                     -- 7. INSERT RETURNING INTO
    PROCEDURE demo_08_insert_dynamic;                       -- 8. EXECUTE IMMEDIATE 动态INSERT
    PROCEDURE demo_09_insert_rowtype;                     -- 9. INSERT %ROWTYPE 变量
    PROCEDURE demo_10_insert_record_type;                   -- 10. INSERT 自定义 RECORD 变量
    PROCEDURE demo_11_insert_bulk_collect;                  -- 11. FORALL BULK INSERT
    PROCEDURE demo_12_insert_merge;                        -- 12. MERGE INTO INSERT
    PROCEDURE demo_13_insert_upsert;                       -- 13. INSERT ON CONFLICT (UPSERT)
    PROCEDURE demo_14_insert_subquery_values;              -- 14. VALUES 用子查询
    PROCEDURE demo_15_insert_default_cols;                 -- 15. 指定列 + DEFAULT
    PROCEDURE demo_16_insert_select_union;                  -- 16. INSERT ... SELECT UNION
    PROCEDURE demo_17_insert_overwrite;                    -- 17. INSERT OVERWRITE（高斯扩展）
    PROCEDURE demo_18_insert_partition;                   -- 18. 分区表 INSERT（模拟）
    PROCEDURE demo_19_insert_cross_join;                   -- 19. CROSS JOIN 生成数据
    PROCEDURE demo_20_insert_complex_combined;             -- 20. 综合复杂 INSERT

    PROCEDURE proc_reset_data;
    PROCEDURE proc_show_employees(p_title IN VARCHAR2);
    PROCEDURE proc_show_archive;
    PROCEDURE proc_show_log;
END pkg_insert_styles;
/

CREATE OR REPLACE PACKAGE BODY pkg_insert_styles AS

    -- ========== 辅助过程 ==========
    PROCEDURE proc_reset_data IS
    BEGIN
        DELETE FROM employees;
        DELETE FROM emp_archive;
        DELETE FROM emp_log;
        DELETE FROM salary_history;
        DELETE FROM dept_summary;
        COMMIT;
        DBE_OUTPUT.PRINT_LINE('All tables cleared.');
    END proc_reset_data;

    PROCEDURE proc_show_employees(p_title IN VARCHAR2) IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== ' || p_title || ' ===');
        DBE_OUTPUT.PRINT_LINE(RPAD('ID', 6) || RPAD('Name', 12) || RPAD('Dept', 6) ||
                             RPAD('Base', 12) || RPAD('Bonus%', 8) || RPAD('Allow', 10) ||
                             RPAD('Status', 10) || RPAD('HireDate', 12) || 'Reason');
        DBE_OUTPUT.PRINT_LINE('--------------------------------------------------------------------------------');
        FOR r IN (SELECT * FROM employees ORDER BY emp_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                RPAD(r.emp_id, 6) || RPAD(r.emp_name, 12) || RPAD(NVL(TO_CHAR(r.dept_id), 'NULL'), 6) ||
                RPAD(TO_CHAR(r.base_salary, 'FM999,999.00'), 12) ||
                RPAD(TO_CHAR(r.bonus_pct, 'FM0.00'), 8) ||
                RPAD(TO_CHAR(r.allowance, 'FM999,999.00'), 10) ||
                RPAD(r.status, 10) || RPAD(TO_CHAR(r.hire_date, 'YYYY-MM-DD'), 12) ||
                SUBSTR(r.update_reason, 1, 30)
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('');
    END proc_show_employees;

    PROCEDURE proc_show_archive IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== Archive Table ===');
        FOR r IN (SELECT * FROM emp_archive ORDER BY archive_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.archive_id || ' | Emp:' || r.emp_id || ' | ' || r.emp_name ||
                ' | Salary:' || r.final_salary || ' | ' || r.archive_reason
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('');
    END proc_show_archive;

    PROCEDURE proc_show_log IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== Log Table ===');
        FOR r IN (SELECT * FROM emp_log ORDER BY log_id) LOOP
            DBE_OUTPUT.PRINT_LINE(
                r.log_id || ' | ' || r.operation || ' | Emp:' || r.emp_id ||
                ' | ' || TO_CHAR(r.op_time, 'HH24:MI:SS')
            );
        END LOOP;
        DBE_OUTPUT.PRINT_LINE('');
    END proc_show_log;

    -- ========== 1. 简单 VALUES ==========
    PROCEDURE demo_01_simple_values IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 1: Simple VALUES INSERT ---');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, hire_date)
        VALUES (1001, '张三', 10, 8000.00, '2024-01-15');

        DBE_OUTPUT.PRINT_LINE('Inserted 1 row: simple VALUES');
    END demo_01_simple_values;

    -- ========== 2. 多行 VALUES ==========
    PROCEDURE demo_02_multi_rows IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 2: Multi-row VALUES INSERT ---');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, status, hire_date)
        VALUES
            (1002, '李四', 20, 12000.00, 0.08, 1000.00, 'ACTIVE', '2023-06-20'),
            (1003, '王五', 10,  9000.00, 0.12,  800.00, 'ACTIVE', '2022-03-10'),
            (1004, '赵六', 30,  7000.00, 0.06,  600.00, 'ACTIVE', '2023-11-01'),
            (1005, '孙七', 20, 15000.00, 0.15, 1200.00, 'ACTIVE', '2021-08-15');

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: multi-row VALUES');
    END demo_02_multi_rows;

    -- ========== 3. INSERT ... SELECT ==========
    PROCEDURE demo_03_insert_select IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 3: INSERT ... SELECT ---');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, status, hire_date, update_reason)
        SELECT
            1000 + ROW_NUMBER() OVER (ORDER BY dept_id),
            'Clone_' || dept_name,
            dept_id,
            budget / 100,
            'TEMP',
            CURRENT_DATE,
            'INSERT SELECT from departments'
        FROM departments
        WHERE budget > 3000000;

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: INSERT ... SELECT');
    END demo_03_insert_select;

    -- ========== 4. INSERT ... SELECT JOIN ==========
    PROCEDURE demo_04_insert_select_join IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 4: INSERT ... SELECT with JOIN ---');

        -- 先插入基础员工
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, hire_date)
        VALUES (1010, '周八', 10, 6500, '2024-02-01');

        -- 基于 JOIN 插入汇总数据到日志表
        INSERT INTO emp_log (log_id, operation, emp_id, old_data, new_data, op_time)
        SELECT
            seq_log.NEXTVAL,
            'AUTO_LOG',
            e.emp_id,
            'Name:' || e.emp_name,
            'Dept:' || d.dept_name || '|Loc:' || d.location || '|Budget:' || d.budget,
            CURRENT_TIMESTAMP
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        WHERE e.emp_id >= 1001;

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: INSERT ... SELECT JOIN');
    END demo_04_insert_select_join;

    -- ========== 5. INSERT ... WITH (CTE) ==========
    PROCEDURE demo_05_insert_with IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 5: INSERT ... WITH (CTE) ---');

        INSERT INTO dept_summary (summary_id, dept_id, dept_name, emp_count, total_payroll, avg_salary, max_salary, min_salary)
        WITH dept_stats AS (
            SELECT
                e.dept_id,
                d.dept_name,
                COUNT(*) AS emp_count,
                SUM(e.base_salary) AS total_payroll,
                AVG(e.base_salary) AS avg_salary,
                MAX(e.base_salary) AS max_salary,
                MIN(e.base_salary) AS min_salary
            FROM employees e
            JOIN departments d ON e.dept_id = d.dept_id
            GROUP BY e.dept_id, d.dept_name
        )
        SELECT
            seq_summary.NEXTVAL,
            dept_id, dept_name, emp_count, total_payroll,
            ROUND(avg_salary, 2), max_salary, min_salary
        FROM dept_stats;

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: INSERT ... WITH (CTE)');
    END demo_05_insert_with;

    -- ========== 6. INSERT ALL（多表插入） ==========
    PROCEDURE demo_06_insert_all IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 6: INSERT ALL (multi-table) ---');

        -- 高斯不支持 Oracle 的 INSERT ALL，用循环模拟
        FOR r IN (
            SELECT emp_id, emp_name, dept_id, base_salary * (1 + bonus_pct) + allowance AS total_pay
            FROM employees
            WHERE status = 'ACTIVE'
        ) LOOP
            -- 插入归档表
            INSERT INTO emp_archive (archive_id, emp_id, emp_name, dept_id, final_salary, archive_reason)
            VALUES (seq_archive.NEXTVAL, r.emp_id, r.emp_name, r.dept_id, r.total_pay, 'Monthly snapshot');

            -- 插入日志表
            INSERT INTO emp_log (log_id, operation, emp_id, new_data)
            VALUES (seq_log.NEXTVAL, 'ARCHIVE', r.emp_id, 'TotalPay=' || r.total_pay);
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('Inserted into archive and log: multi-table via loop');
    END demo_06_insert_all;

    -- ========== 7. INSERT RETURNING INTO ==========
    PROCEDURE demo_07_insert_returning IS
        v_emp_id    INTEGER;
        v_name      VARCHAR2(100);
        v_create    TIMESTAMP;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 7: INSERT RETURNING INTO ---');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
        VALUES (1020, 'RETURNING测试', 20, 20000, 'Demo RETURNING')
        RETURNING emp_id, emp_name, create_time
        INTO v_emp_id, v_name, v_create;

        DBE_OUTPUT.PRINT_LINE('Inserted emp ' || v_emp_id || ' (' || v_name || ') at ' || TO_CHAR(v_create, 'HH24:MI:SS'));
    END demo_07_insert_returning;

    -- ========== 8. EXECUTE IMMEDIATE 动态 INSERT ==========
    PROCEDURE demo_08_insert_dynamic IS
        v_sql       VARCHAR2(500);
        v_table     VARCHAR2(30) := 'employees';
        v_emp_id    INTEGER := 1030;
        v_name      VARCHAR2(100) := 'DynamicUser';
        v_dept      INTEGER := 30;
        v_salary    NUMERIC := 18000;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 8: EXECUTE IMMEDIATE dynamic INSERT ---');

        v_sql := 'INSERT INTO ' || v_table ||
                 ' (emp_id, emp_name, dept_id, base_salary, update_reason) ' ||
                 'VALUES (:1, :2, :3, :4, :5)';

        EXECUTE IMMEDIATE v_sql
            USING v_emp_id, v_name, v_dept, v_salary, 'Dynamic SQL insert';

        DBE_OUTPUT.PRINT_LINE('Inserted 1 row: EXECUTE IMMEDIATE with USING');
    END demo_08_insert_dynamic;

    -- ========== 9. INSERT %ROWTYPE 变量 ==========
    PROCEDURE demo_09_insert_rowtype IS
        v_emp       employees%ROWTYPE;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 9: INSERT %ROWTYPE variable ---');

        -- 从现有记录获取结构
        SELECT * INTO v_emp FROM employees WHERE emp_id = 1001;

        -- 修改为新记录
        v_emp.emp_id := 1040;
        v_emp.emp_name := 'ROWTYPE_Clone';
        v_emp.base_salary := v_emp.base_salary * 1.20;
        v_emp.bonus_pct := 0.20;
        v_emp.hire_date := CURRENT_DATE;
        v_emp.create_time := CURRENT_TIMESTAMP;
        v_emp.update_reason := 'INSERT from %ROWTYPE';

        -- 插入整行
        INSERT INTO employees VALUES v_emp;

        DBE_OUTPUT.PRINT_LINE('Inserted emp ' || v_emp.emp_id || ' from %ROWTYPE');
    END demo_09_insert_rowtype;

    -- ========== 10. INSERT 自定义 RECORD 变量 ==========
    PROCEDURE demo_10_insert_record_type IS
        TYPE rec_new_emp IS RECORD (
            id          INTEGER,
            name        VARCHAR2(100),
            dept        INTEGER,
            salary      NUMERIC(18,2),
            bonus       NUMERIC(5,2),
            allow       NUMERIC(18,2),
            stat        VARCHAR2(20),
            mgr         INTEGER,
            hdate       DATE,
            reason      VARCHAR2(200)
        );
        v_rec       rec_new_emp;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 10: INSERT custom RECORD variable ---');

        v_rec.id := 1050;
        v_rec.name := 'CustomRecord';
        v_rec.dept := 20;
        v_rec.salary := 25000;
        v_rec.bonus := 0.15;
        v_rec.allow := 2000;
        v_rec.stat := 'ACTIVE';
        v_rec.mgr := 1002;
        v_rec.hdate := DATE '2024-06-01';
        v_rec.reason := 'INSERT from custom RECORD';

        -- 自定义 RECORD 不能直接 INSERT，需要展开字段
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance,
                               status, manager_id, hire_date, update_reason)
        VALUES (v_rec.id, v_rec.name, v_rec.dept, v_rec.salary, v_rec.bonus, v_rec.allow,
                v_rec.stat, v_rec.mgr, v_rec.hdate, v_rec.reason);

        DBE_OUTPUT.PRINT_LINE('Inserted emp ' || v_rec.id || ' from custom RECORD');
    END demo_10_insert_record_type;

    -- ========== 11. FORALL BULK INSERT ==========
    PROCEDURE demo_11_insert_bulk_collect IS
        TYPE t_emp_ids IS TABLE OF employees.emp_id%TYPE;
        TYPE t_names IS TABLE OF employees.emp_name%TYPE;
        TYPE t_depts IS TABLE OF employees.dept_id%TYPE;
        TYPE t_sals IS TABLE OF employees.base_salary%TYPE;
        v_ids       t_emp_ids := t_emp_ids(1060, 1061, 1062, 1063, 1064);
        v_names     t_names := t_names('BulkA', 'BulkB', 'BulkC', 'BulkD', 'BulkE');
        v_depts     t_depts := t_depts(10, 20, 10, 30, 20);
        v_sals      t_sals := t_sals(8000, 9000, 8500, 7500, 11000);
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 11: FORALL BULK INSERT ---');

        FORALL i IN 1..v_ids.COUNT
            INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
            VALUES (v_ids(i), v_names(i), v_depts(i), v_sals(i), 'FORALL bulk insert #' || i);

        DBE_OUTPUT.PRINT_LINE('Inserted ' || v_ids.COUNT || ' rows: FORALL BULK');
    END demo_11_insert_bulk_collect;

    -- ========== 12. MERGE INTO INSERT ==========
    PROCEDURE demo_12_insert_merge IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 12: MERGE INTO (INSERT when not matched) ---');

        MERGE INTO employees tgt
        -- 高斯不支持dual表
        USING (SELECT 1070 AS emp_id, 'MergeNew' AS name, 20 AS dept, 22000 AS sal FROM sys_dummy) src
        ON (tgt.emp_id = src.emp_id)
        WHEN NOT MATCHED THEN
            INSERT (emp_id, emp_name, dept_id, base_salary, update_reason)
            VALUES (src.emp_id, src.name, src.dept, src.sal, 'MERGE INSERT');

        DBE_OUTPUT.PRINT_LINE('MERGE inserted ' || SQL%ROWCOUNT || ' rows');
    END demo_12_insert_merge;

    -- ========== 13. INSERT ON CONFLICT (UPSERT) ==========
    PROCEDURE demo_13_insert_upsert IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 13: INSERT ON CONFLICT (Gauss UPSERT) ---');

        -- 先插入一条
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
        VALUES (1080, 'UpsertFirst', 10, 10000, 'First insert');

        -- 再插入相同主键，触发 ON CONFLICT
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
        VALUES (1080, 'UpsertSecond', 20, 12000, 'Upsert update')
        ON duplicate key UPDATE
            emp_name = EXCLUDED.emp_name,
            dept_id = EXCLUDED.dept_id,
            base_salary = EXCLUDED.base_salary,
            update_reason = EXCLUDED.update_reason || ' (was ' || employees.update_reason || ')';

        DBE_OUTPUT.PRINT_LINE('Upsert completed for emp 1080');
    END demo_13_insert_upsert;

    -- ========== 14. VALUES 用子查询 ==========
    PROCEDURE demo_14_insert_subquery_values IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 14: VALUES with subquery expressions ---');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, total_salary, update_reason)
        VALUES (
            1090,
            'SubqueryVal',
            (SELECT dept_id FROM departments WHERE dept_name = '技术部'),
            (SELECT AVG(budget) / 100 FROM departments),
            (SELECT MAX(bonus_pct) FROM employees),
            (SELECT COUNT(*) * 1000 FROM employees),
            'VALUES with subqueries'
        );

        DBE_OUTPUT.PRINT_LINE('Inserted 1 row: VALUES with subquery expressions');
    END demo_14_insert_subquery_values;

    -- ========== 15. 指定列 + DEFAULT ==========
    PROCEDURE demo_15_insert_default_cols IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 15: Partial columns with DEFAULT ---');

        INSERT INTO employees (emp_id, emp_name)
        VALUES (1100, 'DefaultsOnly');

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary)
        VALUES (1101, 'PartialData', 10, DEFAULT);

        INSERT INTO employees (emp_id, emp_name, base_salary, hire_date)
        VALUES (1102, 'ExplicitNull', 5000, NULL);

        DBE_OUTPUT.PRINT_LINE('Inserted 3 rows: DEFAULT and NULL handling');
    END demo_15_insert_default_cols;

    -- ========== 16. INSERT ... SELECT UNION ==========
    PROCEDURE demo_16_insert_select_union IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 16: INSERT ... SELECT UNION ALL ---');

        INSERT INTO emp_log (log_id, operation, emp_id, new_data)
        SELECT seq_log.NEXTVAL, 'UNION_SRC1', emp_id, 'Salary:' || base_salary
        FROM employees WHERE dept_id = 10
        UNION ALL
        SELECT seq_log.NEXTVAL, 'UNION_SRC2', emp_id, 'Salary:' || base_salary
        FROM employees WHERE dept_id = 20;

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: UNION ALL');
    END demo_16_insert_select_union;

    -- ========== 17. INSERT OVERWRITE（高斯扩展） ==========
    PROCEDURE demo_17_insert_overwrite IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 17: INSERT OVERWRITE (truncate + insert) ---');

        -- 高斯不支持标准 INSERT OVERWRITE，用 TRUNCATE + INSERT 模拟
        TRUNCATE TABLE dept_summary;

        INSERT INTO dept_summary (summary_id, dept_id, dept_name, emp_count, total_payroll, avg_salary, max_salary, min_salary)
        SELECT
            seq_summary.NEXTVAL,
            e.dept_id,
            d.dept_name,
            COUNT(*),
            SUM(e.base_salary),
            ROUND(AVG(e.base_salary), 2),
            MAX(e.base_salary),
            MIN(e.base_salary)
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        GROUP BY e.dept_id, d.dept_name;

        DBE_OUTPUT.PRINT_LINE('Overwrote dept_summary with ' || SQL%ROWCOUNT || ' rows');
    END demo_17_insert_overwrite;

    -- ========== 18. 分区表 INSERT（模拟） ==========
    PROCEDURE demo_18_insert_partition IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 18: Partition-wise INSERT (simulated) ---');

        -- 模拟按部门分区插入
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
        SELECT 1110 + ROW_NUMBER() OVER (), 'PartSales_' || dept_name, dept_id, 8000, 'Partition: sales'
        FROM departments WHERE dept_id = 10;

        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, update_reason)
        SELECT 1120 + ROW_NUMBER() OVER (), 'PartTech_' || dept_name, dept_id, 12000, 'Partition: tech'
        FROM departments WHERE dept_id = 20;

        DBE_OUTPUT.PRINT_LINE('Partition inserts completed');
    END demo_18_insert_partition;

    -- ========== 19. CROSS JOIN 生成数据 ==========
    PROCEDURE demo_19_insert_cross_join IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 19: CROSS JOIN generate data ---');

        INSERT INTO emp_temp_staging (seq_no, raw_name, raw_dept, raw_salary)
        SELECT
            ROW_NUMBER() OVER (),
            'Gen_' || d.dept_name || '_' || TO_CHAR(n.n),
            d.dept_name,
            TO_CHAR(5000 + n.n * 1000)
        FROM departments d
        CROSS JOIN (SELECT generate_series(1, 3) AS n) n
        WHERE d.budget > 3000000;

        DBE_OUTPUT.PRINT_LINE('Inserted ' || SQL%ROWCOUNT || ' rows: CROSS JOIN generated');
    END demo_19_insert_cross_join;

    -- ========== 20. 综合复杂 INSERT ==========
    PROCEDURE demo_20_insert_complex_combined IS
        v_emp_rec   employees%ROWTYPE;
        v_new_id    INTEGER := 1150;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 20: Complex combined INSERT ---');

        -- 步骤1：从现有记录复制结构（%ROWTYPE）
        SELECT * INTO v_emp_rec FROM employees WHERE emp_id = 1002;

        -- 步骤2：修改为新数据
        v_emp_rec.emp_id := v_new_id;
        v_emp_rec.emp_name := 'ComplexCombined';
        v_emp_rec.base_salary := v_emp_rec.base_salary * 1.50;
        v_emp_rec.bonus_pct := 0.25;
        v_emp_rec.allowance := 3000;
        v_emp_rec.hire_date := CURRENT_DATE;
        v_emp_rec.create_time := CURRENT_TIMESTAMP;
        v_emp_rec.update_reason := 'Complex: %ROWTYPE + calc + RETURNING';

        -- 步骤3：INSERT %ROWTYPE + RETURNING
        INSERT INTO employees VALUES v_emp_rec
        RETURNING emp_id, emp_name, base_salary
        INTO v_emp_rec.emp_id, v_emp_rec.emp_name, v_emp_rec.base_salary;

        -- 步骤4：立即用 RETURNING 的值插入关联表
        INSERT INTO salary_history (history_id, emp_id, old_salary, new_salary, change_reason)
        VALUES (seq_history.NEXTVAL, v_emp_rec.emp_id, 0, v_emp_rec.base_salary, 'Initial complex insert');

        -- 步骤5：用动态 SQL 插入日志
        EXECUTE IMMEDIATE
            'INSERT INTO emp_log (log_id, operation, emp_id, new_data) VALUES (:1, :2, :3, :4)'
            USING seq_log.NEXTVAL, 'COMPLEX', v_emp_rec.emp_id, 'Salary=' || v_emp_rec.base_salary;

        DBE_OUTPUT.PRINT_LINE('Complex insert chain completed for emp ' || v_emp_rec.emp_id);
    END demo_20_insert_complex_combined;

END pkg_insert_styles;
/

-- ============================================
-- 第三部分：批量调用所有演示
-- ============================================

BEGIN pkg_insert_styles.proc_reset_data; END;
/

BEGIN pkg_insert_styles.demo_01_simple_values; END;
/
BEGIN pkg_insert_styles.demo_02_multi_rows; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 1-2'); END;
/

BEGIN pkg_insert_styles.demo_03_insert_select; END;
/
BEGIN pkg_insert_styles.demo_04_insert_select_join; END;
/
BEGIN pkg_insert_styles.demo_05_insert_with; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 3-5'); END;
/
BEGIN pkg_insert_styles.proc_show_log; END;
/

BEGIN pkg_insert_styles.demo_06_insert_all; END;
/
BEGIN pkg_insert_styles.demo_07_insert_returning; END;
/
BEGIN pkg_insert_styles.demo_08_insert_dynamic; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 6-8'); END;
/
BEGIN pkg_insert_styles.proc_show_archive; END;
/

BEGIN pkg_insert_styles.demo_09_insert_rowtype; END;
/
BEGIN pkg_insert_styles.demo_10_insert_record_type; END;
/
BEGIN pkg_insert_styles.demo_11_insert_bulk_collect; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 9-11'); END;
/

BEGIN pkg_insert_styles.demo_12_insert_merge; END;
/
BEGIN pkg_insert_styles.demo_13_insert_upsert; END;
/
BEGIN pkg_insert_styles.demo_14_insert_subquery_values; END;
/
BEGIN pkg_insert_styles.demo_15_insert_default_cols; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 12-15'); END;
/

BEGIN pkg_insert_styles.demo_16_insert_select_union; END;
/
BEGIN pkg_insert_styles.demo_17_insert_overwrite; END;
/
BEGIN pkg_insert_styles.demo_18_insert_partition; END;
/
BEGIN pkg_insert_styles.demo_19_insert_cross_join; END;
/
BEGIN pkg_insert_styles.demo_20_insert_complex_combined; END;
/
BEGIN pkg_insert_styles.proc_show_employees('After Demo 16-20 (Final)'); END;
/
BEGIN pkg_insert_styles.proc_show_archive; END;
/
BEGIN pkg_insert_styles.proc_show_log; END;
/

-- 查看 staging 表
SELECT * FROM emp_temp_staging ORDER BY seq_no;

-- 查看 summary 表
SELECT * FROM dept_summary ORDER BY summary_id;

-- 查看 salary_history
SELECT * FROM salary_history ORDER BY history_id;
