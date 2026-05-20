
-- ============================================================
-- 高斯/OpenGauss UPDATE SET (列列表) = (子查询) 完整示例
-- ============================================================

-- ============================================
-- 第一部分：建表 DDL
-- ============================================

-- 主表：员工薪资表
DROP TABLE IF EXISTS emp_salary CASCADE;
CREATE TABLE emp_salary (
    emp_id          INTEGER PRIMARY KEY,
    emp_name        VARCHAR2(100),
    dept_id         INTEGER,
    base_salary     NUMERIC(18,2),      -- 基本工资
    bonus_pct       NUMERIC(5,2),       -- 奖金比例
    allowance       NUMERIC(18,2),       -- 津贴
    total_salary    NUMERIC(18,2),       -- 总薪资（由子查询更新）
    last_update     TIMESTAMP,
    update_reason   VARCHAR2(200)
);

-- 参考表：部门调薪标准（本次调薪幅度）
DROP TABLE IF EXISTS dept_raise_standard CASCADE;
CREATE TABLE dept_raise_standard (
    dept_id         INTEGER PRIMARY KEY,
    dept_name       VARCHAR2(100),
    base_raise_pct  NUMERIC(5,2),       -- 基本工资上调比例
    bonus_raise_pct NUMERIC(5,2),       -- 奖金比例上调
    allowance_add   NUMERIC(18,2),       -- 固定津贴增加额
    effective_date  DATE,
    is_active       INTEGER DEFAULT 1
);

-- 参考表：员工绩效评级
DROP TABLE IF EXISTS emp_performance CASCADE;
CREATE TABLE emp_performance (
    emp_id          INTEGER PRIMARY KEY,
    perf_rating     VARCHAR2(10),         -- A/B/C/D
    perf_score      NUMERIC(5,2),         -- 绩效分数 0-100
    perf_year       INTEGER,
    perf_quarter    INTEGER
);

-- 参考表：绩效系数映射
DROP TABLE IF EXISTS perf_coefficient CASCADE;
CREATE TABLE perf_coefficient (
    perf_rating     VARCHAR2(10) PRIMARY KEY,
    salary_coeff    NUMERIC(5,2),         -- 薪资系数
    bonus_coeff     NUMERIC(5,2)          -- 奖金系数
);

-- 日志表：记录更新前后值
DROP TABLE IF EXISTS salary_update_log CASCADE;
CREATE TABLE salary_update_log (
    log_id          INTEGER PRIMARY KEY,
    emp_id          INTEGER,
    old_base        NUMERIC(18,2),
    new_base        NUMERIC(18,2),
    old_bonus_pct   NUMERIC(5,2),
    new_bonus_pct   NUMERIC(5,2),
    old_allowance   NUMERIC(18,2),
    new_allowance   NUMERIC(18,2),
    old_total       NUMERIC(18,2),
    new_total       NUMERIC(18,2),
    update_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_by       VARCHAR2(50) DEFAULT CURRENT_USER
);

DROP SEQUENCE IF EXISTS seq_sal_log;
CREATE SEQUENCE seq_sal_log START WITH 1 INCREMENT BY 1;

-- ============================================
-- 第二部分：测试数据
-- ============================================

INSERT INTO dept_raise_standard (dept_id, dept_name, base_raise_pct, bonus_raise_pct, allowance_add, effective_date, is_active) VALUES
(10, '销售部',  0.15, 0.05, 2000.00, '2024-06-01', 1),
(20, '技术部',  0.12, 0.03, 1500.00, '2024-06-01', 1),
(30, '财务部',  0.08, 0.02, 1000.00, '2024-06-01', 1),
(40, '人事部',  0.10, 0.02, 1200.00, '2024-06-01', 1);

INSERT INTO perf_coefficient (perf_rating, salary_coeff, bonus_coeff) VALUES
('A', 1.20, 1.30),
('B', 1.10, 1.15),
('C', 1.00, 1.00),
('D', 0.90, 0.80);

INSERT INTO emp_salary (emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) VALUES
(1001, '张三', 10,  8000.00, 0.10,  500.00,  NULL, '2024-01-01', 'Initial'),
(1002, '李四', 20, 12000.00, 0.08, 1000.00,  NULL, '2024-01-01', 'Initial'),
(1003, '王五', 10,  9000.00, 0.12,  800.00,  NULL, '2024-01-01', 'Initial'),
(1004, '赵六', 30,  7000.00, 0.06,  600.00,  NULL, '2024-01-01', 'Initial'),
(1005, '孙七', 20, 15000.00, 0.15, 1200.00,  NULL, '2024-01-01', 'Initial'),
(1006, '周八', 40,  6500.00, 0.05,  400.00,  NULL, '2024-01-01', 'Initial'),
(1007, '吴九', 10, 11000.00, 0.11,  900.00,  NULL, '2024-01-01', 'Initial'),
(1008, '郑十', 20, 13500.00, 0.09, 1100.00,  NULL, '2024-01-01', 'Initial');

INSERT INTO emp_performance (emp_id, perf_rating, perf_score, perf_year, perf_quarter) VALUES
(1001, 'A', 92.5, 2024, 1),
(1002, 'B', 85.0, 2024, 1),
(1003, 'C', 78.0, 2024, 1),
(1004, 'D', 65.0, 2024, 1),
(1005, 'A', 95.0, 2024, 1),
(1006, 'C', 72.0, 2024, 1),
(1007, 'B', 88.0, 2024, 1),
(1008, 'A', 91.0, 2024, 1);

COMMIT;

-- ============================================
-- 第三部分：基础 UPDATE SET (a,b,c) = (SELECT ...) 示例
-- ============================================

-- 示例 1：最基本的用法 - 从单表子查询更新多列
-- 根据部门调薪标准，同时更新基本工资、奖金比例、津贴
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance) = (
    SELECT
        s.base_salary * (1 + r.base_raise_pct),
        s.bonus_pct + r.bonus_raise_pct,
        s.allowance + r.allowance_add
    FROM emp_salary s
    JOIN dept_raise_standard r ON s.dept_id = r.dept_id
    WHERE s.emp_id = emp_salary.emp_id
      AND r.is_active = 1
      AND r.effective_date <= CURRENT_DATE
)
WHERE EXISTS (
    SELECT 1 FROM dept_raise_standard r
    WHERE r.dept_id = emp_salary.dept_id AND r.is_active = 1
);

-- 查看结果
SELECT emp_id, emp_name, dept_id, base_salary, bonus_pct, allowance,
       ROUND(base_salary * (1 + bonus_pct) + allowance, 2) AS calc_total
FROM emp_salary ORDER BY emp_id;

-- ============================================
-- 示例 2：结合绩效系数 - 多表 JOIN 子查询
-- ============================================

-- 回滚到初始值（方便对比）
UPDATE emp_salary SET base_salary =
    CASE emp_id
        WHEN 1001 THEN 8000  WHEN 1002 THEN 12000 WHEN 1003 THEN 9000  WHEN 1004 THEN 7000
        WHEN 1005 THEN 15000 WHEN 1006 THEN 6500  WHEN 1007 THEN 11000 WHEN 1008 THEN 13500
    END,
    bonus_pct =
    CASE emp_id
        WHEN 1001 THEN 0.10 WHEN 1002 THEN 0.08 WHEN 1003 THEN 0.12 WHEN 1004 THEN 0.06
        WHEN 1005 THEN 0.15 WHEN 1006 THEN 0.05 WHEN 1007 THEN 0.11 WHEN 1008 THEN 0.09
    END,
    allowance =
    CASE emp_id
        WHEN 1001 THEN 500  WHEN 1002 THEN 1000 WHEN 1003 THEN 800  WHEN 1004 THEN 600
        WHEN 1005 THEN 1200 WHEN 1006 THEN 400  WHEN 1007 THEN 900  WHEN 1008 THEN 1100
    END,
    total_salary = NULL,
    last_update = '2024-01-01',
    update_reason = 'Initial';

-- 同时更新：基本工资（部门标准 * 绩效系数）、奖金比例（部门标准 + 绩效系数）、津贴、总薪资、更新时间
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) = (
    SELECT
        -- 新基本工资 = 原工资 * 部门涨幅 * 绩效系数
        ROUND(s.base_salary * (1 + r.base_raise_pct) * c.salary_coeff, 2),
        -- 新奖金比例 = 原比例 + 部门涨幅 * 绩效系数
        ROUND(s.bonus_pct + r.bonus_raise_pct * c.bonus_coeff, 4),
        -- 新津贴 = 原津贴 + 部门固定增加
        s.allowance + r.allowance_add,
        -- 新总薪资 = 新基本工资 * (1 + 新奖金比例) + 新津贴
        ROUND(s.base_salary * (1 + r.base_raise_pct) * c.salary_coeff *
              (1 + s.bonus_pct + r.bonus_raise_pct * c.bonus_coeff), 2) +
        s.allowance + r.allowance_add,
        -- 更新时间
        CURRENT_TIMESTAMP,
        -- 更新原因
        'Annual raise: dept=' || r.dept_name || ', perf=' || p.perf_rating
    FROM emp_salary s
    JOIN dept_raise_standard r ON s.dept_id = r.dept_id
    JOIN emp_performance p ON s.emp_id = p.emp_id
    JOIN perf_coefficient c ON p.perf_rating = c.perf_rating
    WHERE s.emp_id = emp_salary.emp_id
      AND r.is_active = 1
      AND p.perf_year = 2024
      AND p.perf_quarter = 1
)
WHERE EXISTS (
    SELECT 1 FROM emp_performance p
    WHERE p.emp_id = emp_salary.emp_id AND p.perf_year = 2024 AND p.perf_quarter = 1
);

-- 查看结果
SELECT
    e.emp_id, e.emp_name, d.dept_name, p.perf_rating,
    e.base_salary AS new_base, e.bonus_pct AS new_bonus, e.allowance AS new_allow,
    e.total_salary, e.last_update, e.update_reason
FROM emp_salary e
JOIN dept_raise_standard d ON e.dept_id = d.dept_id
JOIN emp_performance p ON e.emp_id = p.emp_id
ORDER BY e.emp_id;

-- ============================================
-- 示例 3：带 WHERE 条件的精细更新 + 记录日志
-- ============================================

-- 先记录更新前的值到日志表
INSERT INTO salary_update_log (
    log_id, emp_id, old_base, new_base, old_bonus_pct, new_bonus_pct,
    old_allowance, new_allowance, old_total, new_total
)
SELECT
    seq_sal_log.NEXTVAL, e.emp_id, e.base_salary, NULL, e.bonus_pct, NULL,
    e.allowance, NULL, e.total_salary, NULL
FROM emp_salary e
WHERE e.dept_id = 20 AND e.base_salary < 15000;

-- 仅对技术部(dept=20)且基本工资<15000的员工进行专项调整
-- 使用 (SELECT ...) 同时更新 4 列
UPDATE emp_salary
SET (base_salary, bonus_pct, allowance, total_salary) = (
    SELECT
        s.base_salary * 1.25,           -- 技术部专项上调25%
        LEAST(s.bonus_pct + 0.05, 0.30), -- 奖金上限30%
        s.allowance + 3000,              -- 技术津贴
        ROUND(s.base_salary * 1.25 * (1 + LEAST(s.bonus_pct + 0.05, 0.30)) + s.allowance + 3000, 2)
    FROM emp_salary s
    WHERE s.emp_id = emp_salary.emp_id
)
WHERE dept_id = 20
  AND base_salary < 15000
  AND EXISTS (SELECT 1 FROM emp_performance p WHERE p.emp_id = emp_salary.emp_id AND p.perf_rating IN ('A', 'B'));

-- 更新日志表的新值
UPDATE salary_update_log l
SET (new_base, new_bonus_pct, new_allowance, new_total) = (
    SELECT e.base_salary, e.bonus_pct, e.allowance, e.total_salary
    FROM emp_salary e WHERE e.emp_id = l.emp_id
)
WHERE l.new_base IS NULL;

-- 查看日志
SELECT * FROM salary_update_log ORDER BY log_id;

-- ============================================
-- 示例 4：复杂子查询 - 聚合函数 + 窗口函数
-- ============================================

-- 创建部门平均工资参考表
DROP TABLE IF EXISTS dept_avg_salary CASCADE;
CREATE TABLE dept_avg_salary AS
SELECT
    dept_id,
    AVG(base_salary) AS avg_salary,
    MAX(base_salary) AS max_salary,
    MIN(base_salary) AS min_salary,
    COUNT(*) AS emp_count
FROM emp_salary
GROUP BY dept_id;

-- 将低于部门平均工资的员工，一次性更新：基本工资、津贴、总薪资、更新原因
UPDATE emp_salary
SET (base_salary, allowance, total_salary, update_reason) = (
    SELECT
        -- 新工资 = 部门平均工资 * 0.95（保底95%均值）
        GREATEST(s.base_salary * 1.10, a.avg_salary * 0.95),
        -- 津贴 = 原津贴 + (均值 - 当前工资) * 0.5（差额补贴）
        s.allowance + GREATEST(0, (a.avg_salary - s.base_salary) * 0.5),
        -- 总薪资重新计算
        GREATEST(s.base_salary * 1.10, a.avg_salary * 0.95) * (1 + s.bonus_pct) +
        s.allowance + GREATEST(0, (a.avg_salary - s.base_salary) * 0.5),
        -- 更新原因
        'Low salary adjustment: dept_avg=' || TO_CHAR(ROUND(a.avg_salary, 2)) ||
        ', rank=' || TO_CHAR(r.sal_rank) || '/' || TO_CHAR(a.emp_count)
    FROM emp_salary s
    JOIN dept_avg_salary a ON s.dept_id = a.dept_id
    JOIN (
        SELECT emp_id, dept_id,
               RANK() OVER (PARTITION BY dept_id ORDER BY base_salary) AS sal_rank
        FROM emp_salary
    ) r ON s.emp_id = r.emp_id AND s.dept_id = r.dept_id
    WHERE s.emp_id = emp_salary.emp_id
)
WHERE base_salary < (
    SELECT avg_salary FROM dept_avg_salary a WHERE a.dept_id = emp_salary.dept_id
);

-- 查看结果
SELECT e.*, a.avg_salary AS dept_avg
FROM emp_salary e
JOIN dept_avg_salary a ON e.dept_id = a.dept_id
ORDER BY e.dept_id, e.base_salary;

-- ============================================
-- 示例 5：存储过程中使用 UPDATE SET (..) = (SELECT ..)
-- ============================================

CREATE OR REPLACE PACKAGE pkg_salary_update AS
    -- 常量
    MAX_BONUS_PCT   CONSTANT NUMERIC(5,2) := 0.50;
    MIN_BONUS_PCT   CONSTANT NUMERIC(5,2) := 0.02;

    -- 批量调薪过程
    PROCEDURE proc_batch_adjust_salary(
        p_dept_id       IN INTEGER,         -- 目标部门
        p_raise_pct     IN NUMERIC,         -- 涨幅比例
        p_bonus_add     IN NUMERIC,         -- 奖金增加
        p_allowance_add IN NUMERIC,         -- 津贴增加
        p_min_perf      IN VARCHAR2         -- 最低绩效要求
    );

    -- 根据绩效排名调薪
    PROCEDURE proc_adjust_by_rank(
        p_top_n         IN INTEGER,         -- 前N名
        p_raise_pct     IN NUMERIC          -- 额外涨幅
    );

    -- 回滚到指定日期
    PROCEDURE proc_rollback_to_date(
        p_rollback_date IN DATE
    );
END pkg_salary_update;
/

CREATE OR REPLACE PACKAGE BODY pkg_salary_update AS

    PROCEDURE proc_batch_adjust_salary(
        p_dept_id       IN INTEGER,
        p_raise_pct     IN NUMERIC,
        p_bonus_add     IN NUMERIC,
        p_allowance_add IN NUMERIC,
        p_min_perf      IN VARCHAR2
    ) IS
        v_updated_count INTEGER := 0;
        v_old_total     NUMERIC(18,2) := 0;
        v_new_total     NUMERIC(18,2) := 0;
    BEGIN
        -- 记录更新前总薪资
        SELECT SUM(total_salary) INTO v_old_total
        FROM emp_salary WHERE dept_id = p_dept_id;

        -- 核心：UPDATE SET (多列) = (SELECT ...) 在存储过程中使用
        UPDATE emp_salary
        SET (base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) = (
            SELECT
                ROUND(s.base_salary * (1 + p_raise_pct), 2),
                GREATEST(MIN_BONUS_PCT, LEAST(MAX_BONUS_PCT, s.bonus_pct + p_bonus_add)),
                s.allowance + p_allowance_add,
                ROUND(s.base_salary * (1 + p_raise_pct) *
                      (1 + GREATEST(MIN_BONUS_PCT, LEAST(MAX_BONUS_PCT, s.bonus_pct + p_bonus_add))), 2) +
                s.allowance + p_allowance_add,
                CURRENT_TIMESTAMP,
                'Batch adjust: raise=' || TO_CHAR(p_raise_pct * 100) || '%, bonus_add=' ||
                TO_CHAR(p_bonus_add) || ', perf>=' || p_min_perf
            FROM emp_salary s
            JOIN emp_performance p ON s.emp_id = p.emp_id
            WHERE s.emp_id = emp_salary.emp_id
              AND p.perf_rating <= p_min_perf  -- A<B<C<D
        )
        WHERE dept_id = p_dept_id
          AND EXISTS (
              SELECT 1 FROM emp_performance p
              WHERE p.emp_id = emp_salary.emp_id
                AND p.perf_rating <= p_min_perf
          );

        v_updated_count := SQL%ROWCOUNT;

        -- 记录更新后总薪资
        SELECT SUM(total_salary) INTO v_new_total
        FROM emp_salary WHERE dept_id = p_dept_id;

        DBE_OUTPUT.PRINT_LINE('Updated ' || v_updated_count || ' employees in dept ' || p_dept_id);
        DBE_OUTPUT.PRINT_LINE('Total salary change: ' || TO_CHAR(v_old_total) || ' -> ' || TO_CHAR(v_new_total));
        DBE_OUTPUT.PRINT_LINE('Difference: ' || TO_CHAR(v_new_total - v_old_total));
    END proc_batch_adjust_salary;

    PROCEDURE proc_adjust_by_rank(
        p_top_n         IN INTEGER,
        p_raise_pct     IN NUMERIC
    ) IS
    BEGIN
        -- 仅对绩效排名前N的员工调薪
        UPDATE emp_salary
        SET (base_salary, bonus_pct, total_salary, last_update, update_reason) = (
            SELECT
                ROUND(s.base_salary * (1 + p_raise_pct), 2),
                LEAST(MAX_BONUS_PCT, s.bonus_pct + 0.05),
                ROUND(s.base_salary * (1 + p_raise_pct) * (1 + LEAST(MAX_BONUS_PCT, s.bonus_pct + 0.05)), 2) + s.allowance,
                CURRENT_TIMESTAMP,
                'Top ' || p_top_n || ' performer raise: ' || TO_CHAR(p_raise_pct * 100) || '%'
            FROM emp_salary s
            JOIN (
                SELECT emp_id,
                       ROW_NUMBER() OVER (ORDER BY perf_score DESC) AS rn
                FROM emp_performance
                WHERE perf_year = 2024
            ) r ON s.emp_id = r.emp_id
            WHERE s.emp_id = emp_salary.emp_id AND r.rn <= p_top_n
        )
        WHERE emp_id IN (
            SELECT emp_id FROM (
                SELECT emp_id, ROW_NUMBER() OVER (ORDER BY perf_score DESC) AS rn
                FROM emp_performance WHERE perf_year = 2024
            ) WHERE rn <= p_top_n
        );

        DBE_OUTPUT.PRINT_LINE('Adjusted top ' || p_top_n || ' performers');
    END proc_adjust_by_rank;

    PROCEDURE proc_rollback_to_date(
        p_rollback_date IN DATE
    ) IS
    BEGIN
        v_cnt number := 0;
        -- 从日志表回滚（简化示例）
        -- 4. 查看更新日志
        select count(1) into v_cnt from (SELECT temp.* FROM salary_update_log temp ) a;
        UPDATE emp_salary e
        SET (base_salary, bonus_pct, allowance, total_salary, last_update, update_reason) = (
            SELECT l.old_base, l.old_bonus_pct, l.old_allowance, l.old_total,
                   p_rollback_date, 'Rollback to ' || TO_CHAR(p_rollback_date)
            FROM salary_update_log l
            WHERE l.emp_id = e.emp_id
              AND l.log_id = (
                  SELECT MAX(log_id) FROM salary_update_log
                  WHERE emp_id = e.emp_id AND update_time >= p_rollback_date
              )
        )
        WHERE EXISTS (
            SELECT 1 FROM salary_update_log l
            WHERE l.emp_id = e.emp_id
              AND l.update_time >= p_rollback_date
        );

        DBE_OUTPUT.PRINT_LINE('Rolled back ' || SQL%ROWCOUNT || ' records to ' || p_rollback_date);
    END proc_rollback_to_date;

END pkg_salary_update;
/

-- ============================================
-- 第四部分：调用测试
-- ============================================

-- 1. 批量调薪
BEGIN
    pkg_salary_update.proc_batch_adjust_salary(
        p_dept_id       => 10,          -- 销售部
        p_raise_pct     => 0.20,       -- 涨20%
        p_bonus_add     => 0.03,       -- 奖金+3%
        p_allowance_add => 2000,       -- 津贴+2000
        p_min_perf      => 'C'         -- 绩效C及以上
    );
END;
/

-- 2. 前3名额外奖励
BEGIN
    pkg_salary_update.proc_adjust_by_rank(
        p_top_n     => 3,
        p_raise_pct => 0.10
    );
END;
/

-- 3. 查看最终薪资表
SELECT
    e.emp_id,
    e.emp_name,
    d.dept_name,
    p.perf_rating,
    e.base_salary,
    e.bonus_pct,
    e.allowance,
    e.total_salary,
    e.last_update,
    e.update_reason
FROM emp_salary e
LEFT JOIN dept_raise_standard d ON e.dept_id = d.dept_id
LEFT JOIN emp_performance p ON e.emp_id = p.emp_id
ORDER BY e.emp_id;

-- 4. 查看更新日志
SELECT * FROM salary_update_log ORDER BY log_id;

-- ============================================
-- 第五部分：更多语法变体
-- ============================================

-- 变体1：子查询返回单行多列（必须严格匹配列数）
UPDATE emp_salary
SET (base_salary, total_salary) = (
    SELECT base_salary * 1.05, base_salary * 1.05 * (1 + bonus_pct) + allowance
    FROM emp_salary s WHERE s.emp_id = emp_salary.emp_id
)
WHERE emp_id = 1001;

-- 变体2：子查询使用聚合 + HAVING
UPDATE emp_salary
SET (base_salary, allowance, update_reason) = (
    SELECT
        AVG(s.base_salary) OVER (PARTITION BY s.dept_id),
        MAX(s.allowance) OVER (PARTITION BY s.dept_id),
        'Normalized to dept avg'
    FROM emp_salary s
    WHERE s.emp_id = emp_salary.emp_id
)
WHERE emp_id IN (
    SELECT emp_id FROM emp_salary s2
    GROUP BY dept_id
    HAVING COUNT(*) > 2
);

-- 变体3：结合 RETURNING 获取更新后的值（高斯支持）
DECLARE
    v_emp_id    INTEGER;
    v_new_base  NUMERIC(18,2);
    v_new_total NUMERIC(18,2);
BEGIN
    UPDATE emp_salary
    SET (base_salary, total_salary, last_update) = (
        SELECT base_salary * 1.10,
               base_salary * 1.10 * (1 + bonus_pct) + allowance,
               CURRENT_TIMESTAMP
        FROM emp_salary s WHERE s.emp_id = emp_salary.emp_id
    )
    WHERE emp_id = 1005
    RETURNING emp_id, base_salary, total_salary INTO v_emp_id, v_new_base, v_new_total;

    DBE_OUTPUT.PRINT_LINE('Updated emp ' || v_emp_id || ': base=' || v_new_base || ', total=' || v_new_total);
END;
/

-- 变体4：使用 WITH 子查询（CTE）
WITH ranked_salary AS (
    SELECT emp_id,
           base_salary,
           ROW_NUMBER() OVER (PARTITION BY dept_id ORDER BY base_salary DESC) AS rn,
           AVG(base_salary) OVER (PARTITION BY dept_id) AS dept_avg
    FROM emp_salary
)
UPDATE emp_salary
SET (base_salary, total_salary, update_reason) = (
    SELECT
        r.base_salary * 0.95,  -- 高工资者微调
        r.base_salary * 0.95 * (1 + s.bonus_pct) + s.allowance,
        'High earner adjustment: was ' || TO_CHAR(r.base_salary) ||
        ', dept_avg=' || TO_CHAR(r.dept_avg)
    FROM ranked_salary r
    JOIN emp_salary s ON r.emp_id = s.emp_id
    WHERE r.emp_id = emp_salary.emp_id AND r.rn = 1
)
WHERE emp_id IN (SELECT emp_id FROM ranked_salary WHERE rn = 1);
