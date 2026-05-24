-- NOTE: DDL moved to ddl/*.sql



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
                ROUND(emp_salary.base_salary * (1 + p_raise_pct), 2),
                GREATEST(MIN_BONUS_PCT, LEAST(MAX_BONUS_PCT, emp_salary.bonus_pct + p_bonus_add)),
                emp_salary.allowance + p_allowance_add,
                ROUND(emp_salary.base_salary * (1 + p_raise_pct) *
                      (1 + GREATEST(MIN_BONUS_PCT, LEAST(MAX_BONUS_PCT, emp_salary.bonus_pct + p_bonus_add))), 2) +
                emp_salary.allowance + p_allowance_add,
                CURRENT_TIMESTAMP,
                'Batch adjust: raise=' || TO_CHAR(p_raise_pct * 100) || '%, bonus_add=' ||
                TO_CHAR(p_bonus_add) || ', perf>=' || p_min_perf
            FROM emp_performance p
            WHERE emp_salary.emp_id = p.emp_id
              AND p.perf_rating <= p_min_perf
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
                ROUND(emp_salary.base_salary * (1 + p_raise_pct), 2),
                LEAST(MAX_BONUS_PCT, emp_salary.bonus_pct + 0.05),
                ROUND(emp_salary.base_salary * (1 + p_raise_pct) * (1 + LEAST(MAX_BONUS_PCT, emp_salary.bonus_pct + 0.05)), 2) + emp_salary.allowance,
                CURRENT_TIMESTAMP,
                'Top ' || p_top_n || ' performer raise: ' || TO_CHAR(p_raise_pct * 100) || '%'
            FROM (
                SELECT emp_id,
                       ROW_NUMBER() OVER (ORDER BY perf_score DESC) AS rn
                FROM emp_performance
                WHERE perf_year = 2024
            ) r
            WHERE emp_salary.emp_id = r.emp_id AND r.rn <= p_top_n
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
    SELECT emp_salary.base_salary * 1.05, emp_salary.base_salary * 1.05 * (1 + emp_salary.bonus_pct) + emp_salary.allowance
)
WHERE emp_id = 1001;

-- 变体2：子查询使用聚合 + HAVING
UPDATE emp_salary
SET (base_salary, allowance, update_reason) = (
    SELECT
        AVG(emp_salary.base_salary) OVER (PARTITION BY emp_salary.dept_id),
        MAX(emp_salary.allowance) OVER (PARTITION BY emp_salary.dept_id),
        'Normalized to dept avg'
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
        SELECT emp_salary.base_salary * 1.10,
               emp_salary.base_salary * 1.10 * (1 + emp_salary.bonus_pct) + emp_salary.allowance,
               CURRENT_TIMESTAMP
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
