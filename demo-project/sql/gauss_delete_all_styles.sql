
-- ============================================================
-- 高斯/OpenGauss DELETE 语句各种复杂写法存储过程
-- ============================================================

-- ============================================
-- 第一部分：DDL 建表与测试数据
-- ============================================

-- 1. 主表：员工表
DROP TABLE IF EXISTS employees CASCADE;
CREATE TABLE employees (
    emp_id          INTEGER PRIMARY KEY,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2),
    bonus_pct       NUMERIC(5,2),
    hire_date       DATE,
    status          VARCHAR2(20) DEFAULT 'ACTIVE',
    manager_id      INTEGER,
    is_deleted      INTEGER DEFAULT 0,
    delete_time     TIMESTAMP,
    delete_reason   VARCHAR2(200)
);

-- 2. 部门表
DROP TABLE IF EXISTS departments CASCADE;
CREATE TABLE departments (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    location        VARCHAR2(100),
    budget          NUMERIC(18,2),
    is_active       INTEGER DEFAULT 1
);

-- 3. 员工档案表（历史记录）
DROP TABLE IF EXISTS emp_archive CASCADE;
CREATE TABLE emp_archive (
    archive_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    final_salary    NUMERIC(18,2),
    archive_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archive_reason  VARCHAR2(200)
);

-- 4. 员工绩效表
DROP TABLE IF EXISTS emp_performance CASCADE;
CREATE TABLE emp_performance (
    perf_id         INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    perf_year       INTEGER,
    perf_score      NUMERIC(5,2),
    perf_grade      VARCHAR2(10),
    eval_date       DATE
);

-- 5. 员工项目关联表
DROP TABLE IF EXISTS emp_projects CASCADE;
CREATE TABLE emp_projects (
    project_id      INTEGER,
    emp_id          INTEGER,
    role            VARCHAR2(50),
    start_date      DATE,
    end_date        DATE,
    PRIMARY KEY (project_id, emp_id)
);

-- 6. 操作日志表
DROP TABLE IF EXISTS operation_log CASCADE;
CREATE TABLE operation_log (
    log_id          INTEGER PRIMARY KEY,
    operation       VARCHAR2(50),
    table_name      VARCHAR2(50),
    record_id       VARCHAR2(50),
    old_data        VARCHAR2(4000),
    new_data        VARCHAR2(4000),
    op_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    op_user         VARCHAR2(50) DEFAULT CURRENT_USER
);

-- 7. 删除审计表
DROP TABLE IF EXISTS delete_audit CASCADE;
CREATE TABLE delete_audit (
    audit_id        INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    delete_type     VARCHAR2(50),
    target_table    VARCHAR2(50),
    rows_deleted    INTEGER,
    rows_archived   INTEGER,
    criteria        VARCHAR2(500),
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    status          VARCHAR2(20)
);

-- 8. 级联关联表（测试 ON DELETE CASCADE）
DROP TABLE IF EXISTS emp_contacts CASCADE;
CREATE TABLE emp_contacts (
    contact_id      INTEGER PRIMARY KEY,
    emp_id          INTEGER REFERENCES employees(emp_id) ON DELETE CASCADE,
    contact_type    VARCHAR2(20),
    contact_value   VARCHAR2(100)
);

-- 9. 临时统计表
DROP TABLE IF EXISTS tmp_stats CASCADE;
CREATE TABLE tmp_stats (
    stat_id         INTEGER PRIMARY KEY,
    stat_name       VARCHAR2(100),
    stat_value      INTEGER,
    stat_time       TIMESTAMP
);

-- 序列
DROP SEQUENCE IF EXISTS seq_archive;
CREATE SEQUENCE seq_archive START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_log;
CREATE SEQUENCE seq_log START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_audit;
CREATE SEQUENCE seq_audit START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_perf;
CREATE SEQUENCE seq_perf START WITH 1 INCREMENT BY 1;

-- 插入部门数据
INSERT INTO departments (dept_id, dept_name, location, budget, is_active) VALUES
(10, '销售部', '上海', 5000000, 1),
(20, '技术部', '北京', 8000000, 1),
(30, '财务部', '深圳', 3000000, 1),
(40, '人事部', '广州', 2000000, 0);  -- 已停用部门

-- 插入员工数据
INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id, is_deleted) VALUES
(1001, '张三', 10,  8000, 0.10, '2018-03-15', 'ACTIVE',   NULL, 0),
(1002, '李四', 20, 12000, 0.08, '2017-06-20', 'ACTIVE',   NULL, 0),
(1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001, 0),
(1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003, 0),
(1005, '孙七', 20, 15000, 0.15, '2016-11-01', 'ACTIVE', 1002, 0),
(1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001, 0),
(1007, '吴九', 20, 11000, 0.11, '2019-09-15', 'ACTIVE', 1002, 0),
(1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003, 0),
(1009, '钱十一', 10,  5500, 0.04, '2023-12-01', 'ACTIVE', 1001, 0),
(1010, '冯十二', 20,  9500, 0.07, '2020-02-15', 'INACTIVE', 1002, 0),
(1011, '陈十三', 40,  6000, 0.05, '2021-07-20', 'ACTIVE', NULL, 0),  -- 已停用部门
(1012, '褚十四', 40,  5800, 0.04, '2022-01-10', 'ACTIVE', NULL, 0);  -- 已停用部门

-- 插入绩效数据
INSERT INTO emp_performance (perf_id, emp_id, perf_year, perf_score, perf_grade, eval_date) VALUES
(1, 1001, 2024, 92.5, 'A', '2024-01-15'),
(2, 1002, 2024, 85.0, 'B', '2024-01-20'),
(3, 1003, 2024, 65.0, 'D', '2024-01-10'),
(4, 1004, 2024, 78.0, 'C', '2024-01-18'),
(5, 1005, 2024, 95.0, 'A', '2024-01-25'),
(6, 1006, 2024, 55.0, 'D', '2024-01-12'),
(7, 1007, 2024, 88.0, 'B', '2024-01-22'),
(8, 1008, 2024, 72.0, 'C', '2024-01-16'),
(9, 1009, 2024, 45.0, 'D', '2024-01-08'),
(10, 1010, 2024, 60.0, 'D', '2024-01-14'),
(11, 1011, 2024, 70.0, 'C', '2024-01-19'),
(12, 1012, 2024, 68.0, 'C', '2024-01-11');

-- 插入项目关联数据
INSERT INTO emp_projects (project_id, emp_id, role, start_date, end_date) VALUES
(1, 1001, 'MANAGER', '2024-01-01', NULL),
(1, 1003, 'MEMBER', '2024-01-01', NULL),
(1, 1006, 'MEMBER', '2024-01-01', NULL),
(2, 1002, 'MANAGER', '2024-02-01', NULL),
(2, 1005, 'LEAD', '2024-02-01', NULL),
(2, 1007, 'MEMBER', '2024-02-01', NULL),
(3, 1004, 'MANAGER', '2023-06-01', '2024-01-31'),
(3, 1008, 'MEMBER', '2023-06-01', '2024-01-31'),
(4, 1001, 'MANAGER', '2023-01-01', '2023-12-31'),
(4, 1009, 'MEMBER', '2023-01-01', '2023-12-31');

-- 插入联系人数据
INSERT INTO emp_contacts (contact_id, emp_id, contact_type, contact_value) VALUES
(1, 1001, 'PHONE', '13800138001'),
(2, 1001, 'EMAIL', 'zhangsan@company.com'),
(3, 1002, 'PHONE', '13800138002'),
(4, 1003, 'EMAIL', 'wangwu@company.com'),
(5, 1004, 'PHONE', '13800138004'),
(6, 1005, 'EMAIL', 'sunqi@company.com'),
(7, 1006, 'PHONE', '13800138006'),
(8, 1007, 'EMAIL', 'wujiu@company.com');

COMMIT;

-- ============================================
-- 第二部分：DELETE 各种写法存储过程包
-- ============================================

CREATE OR REPLACE PACKAGE pkg_delete_styles AS
    PROCEDURE demo_01_simple_delete;                    -- 1. 简单 DELETE
    PROCEDURE demo_02_delete_where;                     -- 2. WHERE 条件删除
    PROCEDURE demo_03_delete_subquery;                  -- 3. WHERE 子查询删除
    PROCEDURE demo_04_delete_exists;                    -- 4. WHERE EXISTS 删除
    PROCEDURE demo_05_delete_in;                        -- 5. WHERE IN 子查询删除
    PROCEDURE demo_06_delete_correlated;                -- 6. 关联子查询删除
    PROCEDURE demo_07_delete_join;                      -- 7. JOIN 删除（高斯扩展）
    PROCEDURE demo_08_delete_using;                     -- 8. USING 删除（高斯扩展）
    PROCEDURE demo_09_delete_cte;                       -- 9. CTE (WITH) 删除
    PROCEDURE demo_10_delete_returning;                 -- 10. RETURNING INTO 删除
    PROCEDURE demo_11_delete_limit;                     -- 11. LIMIT 删除（高斯扩展）
    PROCEDURE demo_12_delete_order_by_limit;            -- 12. ORDER BY + LIMIT 删除
    PROCEDURE demo_13_delete_partition;                 -- 13. 分区删除（模拟）
    PROCEDURE demo_14_delete_cascade;                   -- 14. 级联删除（外键 ON DELETE CASCADE）
    PROCEDURE demo_15_delete_soft;                      -- 15. 软删除（标记删除）
    PROCEDURE demo_16_delete_archive;                   -- 16. 删除前归档
    PROCEDURE demo_17_delete_log;                       -- 17. 删除并记录日志
    PROCEDURE demo_18_delete_merge;                     -- 18. MERGE INTO 模拟删除
    PROCEDURE demo_19_delete_dynamic;                   -- 19. 动态 SQL 删除
    PROCEDURE demo_20_delete_complex;                   -- 20. 综合复杂删除

    PROCEDURE proc_reset_data;
    PROCEDURE proc_show_counts(p_title IN VARCHAR2);
END pkg_delete_styles;
/

CREATE OR REPLACE PACKAGE BODY pkg_delete_styles AS

    PROCEDURE proc_reset_data IS
    BEGIN
        -- 重置所有表
        DELETE FROM emp_archive;
        DELETE FROM operation_log;
        DELETE FROM delete_audit;
        DELETE FROM tmp_stats;

        -- 重置员工表（先删子表避免约束冲突）
        DELETE FROM emp_contacts;
        DELETE FROM emp_projects;
        DELETE FROM emp_performance;
        DELETE FROM employees;

        -- 重新插入员工
        INSERT INTO employees (emp_id, emp_name, dept_id, base_salary, bonus_pct, hire_date, status, manager_id, is_deleted) VALUES
        (1001, '张三', 10,  8000, 0.10, '2018-03-15', 'ACTIVE',   NULL, 0),
        (1002, '李四', 20, 12000, 0.08, '2017-06-20', 'ACTIVE',   NULL, 0),
        (1003, '王五', 10,  9000, 0.12, '2021-01-10', 'ACTIVE', 1001, 0),
        (1004, '赵六', 30,  7000, 0.06, '2022-05-08', 'ACTIVE', 1003, 0),
        (1005, '孙七', 20, 15000, 0.15, '2016-11-01', 'ACTIVE', 1002, 0),
        (1006, '周八', 10,  6500, 0.05, '2023-03-20', 'INACTIVE', 1001, 0),
        (1007, '吴九', 20, 11000, 0.11, '2019-09-15', 'ACTIVE', 1002, 0),
        (1008, '郑十', 30,  8500, 0.09, '2022-08-01', 'ACTIVE', 1003, 0),
        (1009, '钱十一', 10,  5500, 0.04, '2023-12-01', 'ACTIVE', 1001, 0),
        (1010, '冯十二', 20,  9500, 0.07, '2020-02-15', 'INACTIVE', 1002, 0),
        (1011, '陈十三', 40,  6000, 0.05, '2021-07-20', 'ACTIVE', NULL, 0),
        (1012, '褚十四', 40,  5800, 0.04, '2022-01-10', 'ACTIVE', NULL, 0);

        -- 重新插入绩效
        INSERT INTO emp_performance (perf_id, emp_id, perf_year, perf_score, perf_grade, eval_date) VALUES
        (1, 1001, 2024, 92.5, 'A', '2024-01-15'),
        (2, 1002, 2024, 85.0, 'B', '2024-01-20'),
        (3, 1003, 2024, 65.0, 'D', '2024-01-10'),
        (4, 1004, 2024, 78.0, 'C', '2024-01-18'),
        (5, 1005, 2024, 95.0, 'A', '2024-01-25'),
        (6, 1006, 2024, 55.0, 'D', '2024-01-12'),
        (7, 1007, 2024, 88.0, 'B', '2024-01-22'),
        (8, 1008, 2024, 72.0, 'C', '2024-01-16'),
        (9, 1009, 2024, 45.0, 'D', '2024-01-08'),
        (10, 1010, 2024, 60.0, 'D', '2024-01-14'),
        (11, 1011, 2024, 70.0, 'C', '2024-01-19'),
        (12, 1012, 2024, 68.0, 'C', '2024-01-11');

        -- 重新插入项目
        INSERT INTO emp_projects (project_id, emp_id, role, start_date, end_date) VALUES
        (1, 1001, 'MANAGER', '2024-01-01', NULL),
        (1, 1003, 'MEMBER', '2024-01-01', NULL),
        (1, 1006, 'MEMBER', '2024-01-01', NULL),
        (2, 1002, 'MANAGER', '2024-02-01', NULL),
        (2, 1005, 'LEAD', '2024-02-01', NULL),
        (2, 1007, 'MEMBER', '2024-02-01', NULL),
        (3, 1004, 'MANAGER', '2023-06-01', '2024-01-31'),
        (3, 1008, 'MEMBER', '2023-06-01', '2024-01-31'),
        (4, 1001, 'MANAGER', '2023-01-01', '2023-12-31'),
        (4, 1009, 'MEMBER', '2023-01-01', '2023-12-31');

        -- 重新插入联系人
        INSERT INTO emp_contacts (contact_id, emp_id, contact_type, contact_value) VALUES
        (1, 1001, 'PHONE', '13800138001'),
        (2, 1001, 'EMAIL', 'zhangsan@company.com'),
        (3, 1002, 'PHONE', '13800138002'),
        (4, 1003, 'EMAIL', 'wangwu@company.com'),
        (5, 1004, 'PHONE', '13800138004'),
        (6, 1005, 'EMAIL', 'sunqi@company.com'),
        (7, 1006, 'PHONE', '13800138006'),
        (8, 1007, 'EMAIL', 'wujiu@company.com');

        COMMIT;
        DBE_OUTPUT.PRINT_LINE('All data reset.');
    END proc_reset_data;

    PROCEDURE proc_show_counts(p_title IN VARCHAR2) IS
        v_emp       INTEGER;
        v_perf      INTEGER;
        v_proj      INTEGER;
        v_contact   INTEGER;
        v_archive   INTEGER;
        v_log       INTEGER;
    BEGIN
        SELECT COUNT(*) INTO v_emp FROM employees;
        SELECT COUNT(*) INTO v_perf FROM emp_performance;
        SELECT COUNT(*) INTO v_proj FROM emp_projects;
        SELECT COUNT(*) INTO v_contact FROM emp_contacts;
        SELECT COUNT(*) INTO v_archive FROM emp_archive;
        SELECT COUNT(*) INTO v_log FROM operation_log;

        DBE_OUTPUT.PRINT_LINE('=== ' || p_title || ' ===');
        DBE_OUTPUT.PRINT_LINE('employees: ' || v_emp || ' | performance: ' || v_perf ||
                             ' | projects: ' || v_proj || ' | contacts: ' || v_contact ||
                             ' | archive: ' || v_archive || ' | log: ' || v_log);
        DBE_OUTPUT.PRINT_LINE('');
    END proc_show_counts;

    -- ========== 1. 简单 DELETE ==========
    PROCEDURE demo_01_simple_delete IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 1: Simple DELETE ---');

        -- 删除全表（危险操作）
        -- DELETE FROM employees; -- 注释掉，避免误删

        -- 实际演示：删除特定条件
        DELETE FROM tmp_stats;  -- 清空临时表

        DBE_OUTPUT.PRINT_LINE('Deleted all from tmp_stats (empty table)');
    END demo_01_simple_delete;

    -- ========== 2. WHERE 条件删除 ==========
    PROCEDURE demo_02_delete_where IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 2: DELETE with WHERE ---');
        proc_show_counts('Before');

        -- 单条件删除
        DELETE FROM emp_performance
        WHERE perf_grade = 'D';

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' rows: perf_grade = D');

        -- 多条件 AND/OR
        DELETE FROM emp_performance
        WHERE perf_score < 70
           OR (perf_grade = 'C' AND perf_score < 75);

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' rows: score<70 OR (grade=C AND score<75)');
        proc_show_counts('After');
    END demo_02_delete_where;

    -- ========== 3. WHERE 子查询删除 ==========
    PROCEDURE demo_03_delete_subquery IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 3: DELETE with subquery in WHERE ---');
        proc_show_counts('Before');

        -- 标量子查询作为条件
        DELETE FROM emp_projects
        WHERE emp_id IN (
            SELECT emp_id FROM employees
            WHERE status = 'INACTIVE'
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' projects of INACTIVE employees');

        -- 相关子查询：删除工资低于部门平均的员工绩效记录
        DELETE FROM emp_performance p
        WHERE p.emp_id IN (
            SELECT e.emp_id FROM employees e
            WHERE e.base_salary < (
                SELECT AVG(e2.base_salary) FROM employees e2 WHERE e2.dept_id = e.dept_id
            )
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' performance records of below-avg-salary employees');
        proc_show_counts('After');
    END demo_03_delete_subquery;

    -- ========== 4. WHERE EXISTS 删除 ==========
    PROCEDURE demo_04_delete_exists IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 4: DELETE with WHERE EXISTS ---');
        proc_show_counts('Before');

        -- 删除没有绩效记录的员工项目关联
        DELETE FROM emp_projects ep
        WHERE NOT EXISTS (
            SELECT 1 FROM emp_performance p
            WHERE p.emp_id = ep.emp_id
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' project records with no performance data');

        -- 删除已停用部门的员工
        DELETE FROM employees e
        WHERE EXISTS (
            SELECT 1 FROM departments d
            WHERE d.dept_id = e.dept_id AND d.is_active = 0
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' employees from inactive departments');
        proc_show_counts('After');
    END demo_04_delete_exists;

    -- ========== 5. WHERE IN 子查询删除 ==========
    PROCEDURE demo_05_delete_in IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 5: DELETE with WHERE IN (subquery) ---');
        proc_show_counts('Before');

        -- 删除指定部门的员工绩效
        DELETE FROM emp_performance
        WHERE emp_id IN (
            SELECT emp_id FROM employees WHERE dept_id = 10
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' performance records for dept 10');

        -- 多列 IN
        DELETE FROM emp_projects
        WHERE (project_id, emp_id) IN (
            SELECT project_id, emp_id FROM emp_projects
            WHERE end_date IS NOT NULL AND end_date < '2024-01-01'
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' completed old project records');
        proc_show_counts('After');
    END demo_05_delete_in;

    -- ========== 6. 关联子查询删除 ==========
    PROCEDURE demo_06_delete_correlated IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 6: DELETE with correlated subquery ---');
        proc_show_counts('Before');

        -- 删除工龄超过 6 年且绩效为 C 或 D 的员工
        DELETE FROM employees e
        WHERE EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM e.hire_date) > 6
          AND EXISTS (
              SELECT 1 FROM emp_performance p
              WHERE p.emp_id = e.emp_id
                AND p.perf_grade IN ('C', 'D')
                AND p.perf_year = 2024
          );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' senior employees with low performance');
        proc_show_counts('After');
    END demo_06_delete_correlated;

    -- ========== 7. JOIN 删除（高斯扩展） ==========
    PROCEDURE demo_07_delete_join IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 7: DELETE with JOIN (Gauss extension) ---');
        proc_show_counts('Before');

        -- 高斯支持 DELETE ... FROM ... JOIN 语法
        DELETE FROM emp_performance p
        USING employees e, departments d
        WHERE p.emp_id = e.emp_id
          AND e.dept_id = d.dept_id
          AND d.is_active = 0;

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' performance records from inactive dept (JOIN)');

        -- 另一种 JOIN 写法
        DELETE FROM emp_projects ep
        FROM employees e
        WHERE ep.emp_id = e.emp_id
          AND e.status = 'INACTIVE';

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' project records of inactive employees (JOIN)');
        proc_show_counts('After');
    END demo_07_delete_join;

    -- ========== 8. USING 删除（高斯扩展） ==========
    PROCEDURE demo_08_delete_using IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 8: DELETE ... USING (Gauss extension) ---');
        proc_show_counts('Before');

        -- USING 子句指定额外的表用于条件判断
        DELETE FROM emp_contacts c
        USING employees e
        WHERE c.emp_id = e.emp_id
          AND e.is_deleted = 1;

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' contacts USING employees');
        proc_show_counts('After');
    END demo_08_delete_using;

    -- ========== 9. CTE (WITH) 删除 ==========
    PROCEDURE demo_09_delete_cte IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 9: DELETE with CTE (WITH) ---');
        proc_show_counts('Before');

        -- 先用 CTE 计算需要删除的目标，再执行删除
        WITH low_performers AS (
            SELECT emp_id, perf_score, perf_grade
            FROM emp_performance
            WHERE perf_score < 70
        ),
        to_delete AS (
            SELECT e.emp_id
            FROM employees e
            JOIN low_performers lp ON e.emp_id = lp.emp_id
            WHERE e.base_salary > 8000  -- 高工资但低绩效
        )
        DELETE FROM emp_performance
        WHERE emp_id IN (SELECT emp_id FROM to_delete);

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' performance records via CTE (high salary, low perf)');
        proc_show_counts('After');
    END demo_09_delete_cte;

    -- ========== 10. RETURNING INTO 删除 ==========
    PROCEDURE demo_10_delete_returning IS
        v_emp_id    INTEGER;
        v_name      VARCHAR2(100);
        v_salary    NUMERIC(18,2);
        v_count     INTEGER := 0;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 10: DELETE with RETURNING INTO ---');
        proc_show_counts('Before');

        -- 单条 RETURNING
        DELETE FROM employees
        WHERE emp_id = 1012
        RETURNING emp_id, emp_name, base_salary
        INTO v_emp_id, v_name, v_salary;

        DBE_OUTPUT.PRINT_LINE('Deleted emp ' || v_emp_id || ' (' || v_name || ') salary=' || v_salary);

        -- 多条 RETURNING（需 BULK COLLECT）
        DECLARE
            TYPE t_ids IS TABLE OF employees.emp_id%TYPE;
            TYPE t_names IS TABLE OF employees.emp_name%TYPE;
            v_ids   t_ids;
            v_names t_names;
        BEGIN
            DELETE FROM emp_performance
            WHERE perf_grade = 'C'
            RETURNING emp_id, emp_id
            BULK COLLECT INTO v_ids, v_names;

            DBE_OUTPUT.PRINT_LINE('Deleted ' || v_ids.COUNT || ' performance records (BULK RETURNING)');
        END;

        proc_show_counts('After');
    END demo_10_delete_returning;

    -- ========== 11. LIMIT 删除（高斯扩展） ==========
    PROCEDURE demo_11_delete_limit IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 11: DELETE with LIMIT ---');
        proc_show_counts('Before');

        -- 高斯支持 DELETE ... LIMIT
        DELETE FROM emp_performance
        WHERE perf_grade = 'B'
        LIMIT 2;

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' rows with LIMIT 2');
        proc_show_counts('After');
    END demo_11_delete_limit;

    -- ========== 12. ORDER BY + LIMIT 删除 ==========
    PROCEDURE demo_12_delete_order_by_limit IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 12: DELETE with ORDER BY + LIMIT ---');
        proc_show_counts('Before');

        -- 按入职日期排序，删除最早入职的 N 条绩效记录
        DELETE FROM emp_performance
        WHERE emp_id IN (
            SELECT emp_id FROM employees ORDER BY hire_date LIMIT 3
        );

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' performance records of earliest hires');
        proc_show_counts('After');
    END demo_12_delete_order_by_limit;

    -- ========== 13. 分区删除（模拟） ==========
    PROCEDURE demo_13_delete_partition IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 13: Partition-wise DELETE (simulated) ---');
        proc_show_counts('Before');

        -- 按部门分批删除（模拟分区删除）
        DELETE FROM emp_projects WHERE emp_id IN (SELECT emp_id FROM employees WHERE dept_id = 10);
        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' project records: dept 10 partition');

        DELETE FROM emp_projects WHERE emp_id IN (SELECT emp_id FROM employees WHERE dept_id = 20);
        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' project records: dept 20 partition');

        DELETE FROM emp_projects WHERE emp_id IN (SELECT emp_id FROM employees WHERE dept_id = 30);
        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' project records: dept 30 partition');

        proc_show_counts('After');
    END demo_13_delete_partition;

    -- ========== 14. 级联删除（外键 ON DELETE CASCADE） ==========
    PROCEDURE demo_14_delete_cascade IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 14: CASCADE DELETE (FK ON DELETE CASCADE) ---');
        proc_show_counts('Before');

        -- emp_contacts 有外键 REFERENCES employees(emp_id) ON DELETE CASCADE
        -- 删除员工会自动级联删除其联系人
        DELETE FROM employees WHERE emp_id = 1001;

        DBE_OUTPUT.PRINT_LINE('Deleted emp 1001, contacts cascaded automatically');
        proc_show_counts('After (note contacts decreased)');
    END demo_14_delete_cascade;

    -- ========== 15. 软删除（标记删除） ==========
    PROCEDURE demo_15_delete_soft IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 15: Soft DELETE (mark as deleted) ---');

        -- 不真正删除，而是标记 is_deleted=1
        UPDATE employees
        SET is_deleted = 1,
            delete_time = CURRENT_TIMESTAMP,
            delete_reason = 'Soft delete: performance below threshold',
            status = 'INACTIVE'
        WHERE emp_id IN (
            SELECT e.emp_id FROM employees e
            JOIN emp_performance p ON e.emp_id = p.emp_id
            WHERE p.perf_score < 60 AND p.perf_year = 2024
        )
        AND is_deleted = 0;

        DBE_OUTPUT.PRINT_LINE('Soft deleted ' || SQL%ROWCOUNT || ' employees (marked is_deleted=1)');

        -- 查询时排除软删除记录
        DBE_OUTPUT.PRINT_LINE('Active employees: ' || (SELECT COUNT(*) FROM employees WHERE is_deleted = 0));
        DBE_OUTPUT.PRINT_LINE('Soft-deleted employees: ' || (SELECT COUNT(*) FROM employees WHERE is_deleted = 1));
    END demo_15_delete_soft;

    -- ========== 16. 删除前归档 ==========
    PROCEDURE demo_16_delete_archive IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 16: DELETE with Archive ---');
        proc_show_counts('Before');

        -- 步骤1：先归档到历史表
        INSERT INTO emp_archive (archive_id, emp_id, emp_name, dept_id, final_salary, archive_reason)
        SELECT seq_archive.NEXTVAL, emp_id, emp_name, dept_id, base_salary, 'Pre-delete archive'
        FROM employees
        WHERE status = 'INACTIVE' AND is_deleted = 1;

        DBE_OUTPUT.PRINT_LINE('Archived ' || SQL%ROWCOUNT || ' employees before delete');

        -- 步骤2：删除已归档的记录
        DELETE FROM employees
        WHERE status = 'INACTIVE' AND is_deleted = 1;

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' archived employees');
        proc_show_counts('After');
    END demo_16_delete_archive;

    -- ========== 17. 删除并记录日志 ==========
    PROCEDURE demo_17_delete_log IS
        TYPE t_ids IS TABLE OF employees.emp_id%TYPE;
        TYPE t_names IS TABLE OF employees.emp_name%TYPE;
        TYPE t_sals IS TABLE OF employees.base_salary%TYPE;
        v_ids       t_ids;
        v_names     t_names;
        v_sals      t_sals;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 17: DELETE with Logging ---');
        proc_show_counts('Before');

        -- 步骤1：将要删除的数据收集到数组
        SELECT emp_id, emp_name, base_salary
        BULK COLLECT INTO v_ids, v_names, v_sals
        FROM employees
        WHERE dept_id = 40;  -- 已停用部门

        -- 步骤2：记录日志
        FOR i IN 1..v_ids.COUNT LOOP
            INSERT INTO operation_log (log_id, operation, table_name, record_id, old_data, new_data)
            VALUES (seq_log.NEXTVAL, 'DELETE', 'employees', v_ids(i),
                    'Name:' || v_names(i) || '|Salary:' || v_sals(i), 'DELETED');
        END LOOP;

        -- 步骤3：执行删除
        DELETE FROM employees WHERE dept_id = 40;

        DBE_OUTPUT.PRINT_LINE('Deleted ' || SQL%ROWCOUNT || ' employees, logged ' || v_ids.COUNT || ' records');
        proc_show_counts('After');
    END demo_17_delete_log;

    -- ========== 18. MERGE INTO 模拟删除 ==========
    PROCEDURE demo_18_delete_merge IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 18: MERGE INTO simulating DELETE ---');
        proc_show_counts('Before');

        -- 使用 MERGE 的 DELETE 子句（高斯支持）-- 经验证，高斯不支持此写法
        -- MERGE INTO emp_performance tgt
        -- USING (
        --     SELECT emp_id FROM employees
        --     WHERE base_salary < 7000 OR status = 'INACTIVE'
        -- ) src
        -- ON (tgt.emp_id = src.emp_id)
        -- WHEN MATCHED THEN
        --     DELETE;

        DBE_OUTPUT.PRINT_LINE('MERGE deleted ' || SQL%ROWCOUNT || ' performance records');
        proc_show_counts('After');
    END demo_18_delete_merge;

    -- ========== 19. 动态 SQL 删除 ==========
    PROCEDURE demo_19_delete_dynamic IS
        v_sql       VARCHAR2(500);
        v_table     VARCHAR2(30) := 'emp_performance';
        v_threshold NUMERIC := 70;
        v_count     INTEGER;
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 19: Dynamic SQL DELETE ---');
        proc_show_counts('Before');

        -- 动态构建 DELETE 语句
        v_sql := 'DELETE FROM ' || v_table || ' WHERE perf_score < :1';
        EXECUTE IMMEDIATE v_sql USING v_threshold;
        v_count := SQL%ROWCOUNT;

        DBE_OUTPUT.PRINT_LINE('Dynamic deleted ' || v_count || ' rows from ' || v_table || ' where score<' || v_threshold);

        -- 动态表名和条件
        v_sql := 'DELETE FROM ' || v_table || ' WHERE emp_id IN (SELECT emp_id FROM employees WHERE dept_id = :1)';
        EXECUTE IMMEDIATE v_sql USING 30;

        DBE_OUTPUT.PRINT_LINE('Dynamic deleted ' || SQL%ROWCOUNT || ' rows for dept 30');
        proc_show_counts('After');
    END demo_19_delete_dynamic;

    -- ========== 20. 综合复杂删除 ==========
    PROCEDURE demo_20_delete_complex IS
        v_audit_id  INTEGER;
        v_deleted   INTEGER := 0;
        v_archived  INTEGER := 0;
        v_batch_id  VARCHAR2(50) := 'DEL_BATCH_' || TO_CHAR(SYSTIMESTAMP, 'YYYYMMDDHH24MISS');
    BEGIN
        DBE_OUTPUT.PRINT_LINE('--- Demo 20: Complex Combined DELETE ---');
        proc_show_counts('Before');

        -- 步骤1：创建审计记录
        INSERT INTO delete_audit (audit_id, batch_id, delete_type, target_table, criteria, start_time, status)
        VALUES (seq_audit.NEXTVAL, v_batch_id, 'COMPLEX', 'employees',
                'Low perf + old hire + inactive dept', CURRENT_TIMESTAMP, 'RUNNING')
        RETURNING audit_id INTO v_audit_id;

        delete from delete_audit
        where audit_id=1
        and batch_id in (
              select emp_id
              from employees
              where emp_name = v_batch_id
        )
        and exists (
              select 1
              from employees
              where emp_name = v_batch_id
              and hire_date <= to_char(now(), 'yyyymmdd')
              and hire_date > to_char(now(), 'yyyymmdd')
        );
        -- 步骤2：用 CTE 确定删除目标
        WITH delete_candidates AS (
            SELECT e.emp_id, e.emp_name, e.base_salary, e.dept_id, e.hire_date,
                   p.perf_score, p.perf_grade,
                   d.is_active AS dept_active,
                   EXTRACT(YEAR FROM CURRENT_DATE) - EXTRACT(YEAR FROM e.hire_date) AS service_years
            FROM employees e
            LEFT JOIN emp_performance p ON e.emp_id = p.emp_id AND p.perf_year = 2024
            LEFT JOIN departments d ON e.dept_id = d.dept_id
            WHERE e.is_deleted = 0
        ),
        final_targets AS (
            SELECT emp_id, emp_name, base_salary
            FROM delete_candidates
            WHERE (perf_grade = 'D' AND service_years >= 3)
               OR (dept_active = 0)
               OR (perf_score < 50)
        )
        -- 步骤3：先归档
        INSERT INTO emp_archive (archive_id, emp_id, emp_name, dept_id, final_salary, archive_reason)
        SELECT seq_archive.NEXTVAL, t.emp_id, t.emp_name, e.dept_id, t.base_salary,
               'Complex delete: batch=' || v_batch_id
        FROM final_targets t
        JOIN employees e ON t.emp_id = e.emp_id;

        v_archived := SQL%ROWCOUNT;

        -- 步骤4：删除主表
        DELETE FROM employees
        WHERE emp_id IN (
            SELECT emp_id FROM final_targets
        );

        v_deleted := SQL%ROWCOUNT;

        -- 步骤5：清理关联表（级联已处理 contacts，手动清理 projects 和 performance）
        DELETE FROM emp_projects
        WHERE emp_id NOT IN (SELECT emp_id FROM employees);

        DELETE FROM emp_performance
        WHERE emp_id NOT IN (SELECT emp_id FROM employees);

        -- 步骤6：记录日志
        INSERT INTO operation_log (log_id, operation, table_name, record_id, old_data, new_data)
        VALUES (seq_log.NEXTVAL, 'COMPLEX_DELETE', 'employees', v_batch_id,
                'Archived:' || v_archived, 'Deleted:' || v_deleted);

        -- 步骤7：更新审计记录
        UPDATE delete_audit
        SET end_time = CURRENT_TIMESTAMP,
            rows_deleted = v_deleted,
            rows_archived = v_archived,
            status = 'SUCCESS'
        WHERE audit_id = v_audit_id;

        COMMIT;

        DBE_OUTPUT.PRINT_LINE('Complex delete completed:');
        DBE_OUTPUT.PRINT_LINE('  Archived: ' || v_archived);
        DBE_OUTPUT.PRINT_LINE('  Deleted: ' || v_deleted);
        DBE_OUTPUT.PRINT_LINE('  Audit ID: ' || v_audit_id);
        proc_show_counts('After');
    END demo_20_delete_complex;

END pkg_delete_styles;
/

-- ============================================
-- 第三部分：批量调用所有演示
-- ============================================

BEGIN pkg_delete_styles.proc_reset_data; END;
/

-- Demo 1-2: 基础删除
BEGIN pkg_delete_styles.demo_01_simple_delete; END;
/
BEGIN pkg_delete_styles.demo_02_delete_where; END;
/

-- 重置后继续
BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_03_delete_subquery; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_04_delete_exists; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_05_delete_in; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_06_delete_correlated; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_07_delete_join; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_08_delete_using; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_09_delete_cte; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_10_delete_returning; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_11_delete_limit; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_12_delete_order_by_limit; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_13_delete_partition; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_14_delete_cascade; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_15_delete_soft; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_16_delete_archive; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_17_delete_log; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_18_delete_merge; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_19_delete_dynamic; END;
/

BEGIN pkg_delete_styles.proc_reset_data; END;
/
BEGIN pkg_delete_styles.demo_20_delete_complex; END;
/

-- 查看审计表
SELECT * FROM delete_audit ORDER BY start_time DESC;

-- 查看归档表
SELECT * FROM emp_archive ORDER BY archive_id;

-- 查看日志表
SELECT * FROM operation_log ORDER BY log_id;
