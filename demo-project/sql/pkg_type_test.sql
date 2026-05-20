-- =====================================================================
-- 自定义 TYPE 类型测试用例
-- 验证以下场景在 PL/pgSQL → Java 转换中的处理:
--   1. CREATE TYPE (composite) 作为变量类型
--   2. %TYPE 锚定到自定义 TYPE 的字段
--   3. %ROWTYPE 锚定到实际表
--   4. RECORD 类型变量
--   5. 自定义函数返回简单类型（VARCHAR, NUMERIC 等）
--   6. 自定义函数返回复合 TYPE（composite type）
--   7. 嵌套：变量类型为函数返回值类型
--   8. 表类型变量（TABLE OF / 数组语义）
-- =====================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 0. 依赖的表结构（DDL，用于 %TYPE 和 %ROWTYPE 推断）
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_employees (
    id          BIGINT,
    name        VARCHAR(100),
    email       VARCHAR(200),
    dept_id     BIGINT,
    salary      NUMERIC(18, 2),
    status      VARCHAR(20),
    hire_date   DATE
);

CREATE TABLE IF NOT EXISTS t_departments (
    id          BIGINT,
    dept_name   VARCHAR(100),
    budget      NUMERIC(18, 2),
    manager_id  BIGINT
);

-- ─────────────────────────────────────────────────────────────────────
-- 1. 自定义复合类型 (composite type)
--    GaussDB/OpenGauss 语法: CREATE TYPE name AS (field type, ...)
--    期望转换: 变量声明为 Map<String, Object> 或自定义 Java 类
-- ─────────────────────────────────────────────────────────────────────
CREATE TYPE emp_info AS (
    emp_id      BIGINT,
    emp_name    VARCHAR(100),
    emp_salary  NUMERIC(18, 2)
);

CREATE TYPE dept_summary AS (
    dept_id     BIGINT,
    dept_name   VARCHAR(100),
    head_count  INTEGER,
    total_salary NUMERIC(18, 2)
);

-- ─────────────────────────────────────────────────────────────────────
-- 2. 表类型 (TABLE OF) — GaussDB 兼容
--    用于模拟集合/数组语义
-- ─────────────────────────────────────────────────────────────────────
-- GaussDB 支持: CREATE TYPE int_list AS TABLE OF INTEGER
-- PostgreSQL 等价: 使用 INTEGER[] 或自定义数组类型
-- 注: ogsql-parser 对 TABLE OF 的支持情况取决于版本

-- ─────────────────────────────────────────────────────────────────────
-- 3. 辅助函数：返回简单类型
--    期望转换: 正常 Java 方法返回类型
-- ─────────────────────────────────────────────────────────────────────

-- 返回 VARCHAR: 根据员工ID查姓名
CREATE OR REPLACE FUNCTION pkg_type_test.get_emp_name(p_emp_id BIGINT)
RETURNS VARCHAR
AS $$
DECLARE
    v_name VARCHAR;
BEGIN
    SELECT name INTO v_name FROM t_employees WHERE id = p_emp_id;
    RETURN COALESCE(v_name, 'UNKNOWN');
END;
$$ LANGUAGE plpgsql;

-- 返回 NUMERIC: 计算部门总薪资
CREATE OR REPLACE FUNCTION pkg_type_test.calc_dept_total_salary(p_dept_id BIGINT)
RETURNS NUMERIC
AS $$
DECLARE
    v_total NUMERIC;
BEGIN
    SELECT SUM(salary) INTO v_total FROM t_employees WHERE dept_id = p_dept_id;
    RETURN COALESCE(v_total, 0);
END;
$$ LANGUAGE plpgsql;

-- 返回 INTEGER: 统计部门人数
CREATE OR REPLACE FUNCTION pkg_type_test.count_dept_employees(p_dept_id BIGINT)
RETURNS INTEGER
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM t_employees WHERE dept_id = p_dept_id;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────────────
-- 4. 辅助函数：返回复合类型
--    期望转换: 返回 Map<String, Object> 或对应 Java 类型
-- ─────────────────────────────────────────────────────────────────────

-- 返回自定义复合类型 emp_info
CREATE OR REPLACE FUNCTION pkg_type_test.get_emp_info(p_emp_id BIGINT)
RETURNS emp_info
AS $$
DECLARE
    v_result emp_info;
BEGIN
    SELECT id, name, salary INTO v_result.emp_id, v_result.emp_name, v_result.emp_salary
    FROM t_employees WHERE id = p_emp_id;
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

-- 返回 dept_summary 复合类型
CREATE OR REPLACE FUNCTION pkg_type_test.get_dept_summary(p_dept_id BIGINT)
RETURNS dept_summary
AS $$
DECLARE
    v_result dept_summary;
BEGIN
    v_result.dept_id := p_dept_id;
    SELECT dept_name INTO v_result.dept_name FROM t_departments WHERE id = p_dept_id;
    v_result.head_count := pkg_type_test.count_dept_employees(p_dept_id);
    v_result.total_salary := pkg_type_test.calc_dept_total_salary(p_dept_id);
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────────────
-- 5. 主测试存储过程：使用自定义 TYPE 作为变量
-- ─────────────────────────────────────────────────────────────────────

-- 测试1: 变量使用自定义复合类型 emp_info
CREATE OR REPLACE PROCEDURE pkg_type_test.test_composite_var(p_emp_id BIGINT)
AS $$
DECLARE
    -- 自定义复合类型变量 — 期望转译为 Map<String, Object> 或专用 DTO
    v_emp        emp_info;
    v_dept_sum   dept_summary;
    -- 普通类型用于对比
    v_name       VARCHAR;
    v_salary     NUMERIC;
BEGIN
    -- 调用返回复合类型的函数
    v_emp := pkg_type_test.get_emp_info(p_emp_id);

    -- 访问复合类型字段（赋值到简单类型变量）
    v_name := v_emp.emp_name;
    v_salary := v_emp.emp_salary;

    -- 用复合类型字段做条件判断
    IF v_emp.emp_salary > 10000 THEN
        INSERT INTO t_log(id, msg) VALUES(1, 'High salary: ' || v_emp.emp_name);
    ELSIF v_emp.emp_salary > 5000 THEN
        INSERT INTO t_log(id, msg) VALUES(2, 'Medium salary: ' || v_emp.emp_name);
    ELSE
        INSERT INTO t_log(id, msg) VALUES(3, 'Low salary: ' || v_name);
    END IF;

    -- 调用另一个返回复合类型的函数
    v_dept_sum := pkg_type_test.get_dept_summary(1);

    -- 使用第二个复合类型的字段
    IF v_dept_sum.head_count > 10 THEN
        UPDATE t_departments SET budget = v_dept_sum.total_salary * 1.2
        WHERE id = v_dept_sum.dept_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 测试2: %TYPE 锚定到表列（已有功能，此文件用于回归）
CREATE OR REPLACE PROCEDURE pkg_type_test.test_percent_type(p_emp_id BIGINT)
AS $$
DECLARE
    -- %TYPE 锚定 — 期望从 DDL 或 TYPE_OVERRIDES 推断 Java 类型
    v_emp_name  t_employees.name%TYPE;
    v_emp_salary t_employees.salary%TYPE;
    v_dept_id   t_employees.dept_id%TYPE;
BEGIN
    SELECT name, salary, dept_id INTO v_emp_name, v_emp_salary, v_dept_id
    FROM t_employees WHERE id = p_emp_id;

    IF v_emp_salary > 10000 THEN
        UPDATE t_employees SET salary = v_emp_salary * 0.9 WHERE id = p_emp_id;
        INSERT INTO t_log(id, msg) VALUES(10, 'Adjusted: ' || v_emp_name);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 测试3: %ROWTYPE 锚定到表行
CREATE OR REPLACE PROCEDURE pkg_type_test.test_rowtype(p_emp_id BIGINT)
IS
-- AS $$
-- DECLARE
    -- %ROWTYPE — 期望转译为 Map<String, Object>
    v_emp  t_employees%ROWTYPE;
BEGIN
    SELECT * INTO v_emp FROM t_employees WHERE id = p_emp_id;
    insert into t_employees
       (select v_emp.id,
           v_emp.status
       from sys_dummpy);

    -- 访问 %ROWTYPE 字段
    IF v_emp.status = 'ACTIVE' THEN
        UPDATE t_departments SET manager_id = v_emp.id WHERE id = v_emp.dept_id;
        INSERT INTO t_log(id, msg) VALUES(20, 'Manager set: ' || v_emp.name);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 测试4: RECORD 类型变量
CREATE OR REPLACE PROCEDURE pkg_type_test.test_record_type(p_dept_id BIGINT)
AS $$
DECLARE
    -- RECORD 类型 — 期望转译为 Map<String, Object>
    v_rec        RECORD;
    v_count      INTEGER;
BEGIN
    -- RECORD 在 FOR IN SELECT 中使用
    FOR v_rec IN SELECT id, name, salary FROM t_employees WHERE dept_id = p_dept_id ORDER BY id LOOP
        v_count := v_count + 1;
        IF v_rec.salary > 8000 THEN
            UPDATE t_employees SET status = 'SENIOR' WHERE id = v_rec.id;
        END IF;
        INSERT INTO t_audit(user_id, action) VALUES(v_rec.id, 'reviewed');
    END LOOP;

    INSERT INTO t_log(id, msg) VALUES(30, 'Reviewed ' || v_count || ' employees');
END;
$$ LANGUAGE plpgsql;

-- 测试5: 混合使用自定义函数返回值作为变量类型推断依据
CREATE OR REPLACE PROCEDURE pkg_type_test.test_func_return_types(p_emp_id BIGINT)
AS $$
DECLARE
    -- 简单类型：直接由函数返回类型决定
    v_name       VARCHAR;
    v_total_sal  NUMERIC;
    v_head_cnt   INTEGER;
    -- 复合类型：函数返回自定义 TYPE
    v_info       emp_info;
    v_summary    dept_summary;
BEGIN
    -- 调用返回简单类型的函数
    v_name := pkg_type_test.get_emp_name(p_emp_id);
    v_total_sal := pkg_type_test.calc_dept_total_salary(1);
    v_head_cnt := pkg_type_test.count_dept_employees(1);

    -- 调用返回复合类型的函数，取字段
    v_info := pkg_type_test.get_emp_info(p_emp_id);
    v_summary := pkg_type_test.get_dept_summary(1);

    -- 简单类型参与运算
    IF v_total_sal > 100000 THEN
        INSERT INTO t_log(id, msg) VALUES(40, 'Budget alert: ' || v_total_sal);
    END IF;

    -- 复合类型字段参与运算
    IF v_summary.total_salary > v_total_sal THEN
        INSERT INTO t_log(id, msg) VALUES(41, 'Cross-dept comparison: ' || v_summary.dept_name);
    END IF;

    -- 混合使用
    INSERT INTO t_log(id, msg)
    VALUES(42, 'Emp=' || v_info.emp_name || ' DeptTotal=' || v_summary.total_salary);
END;
$$ LANGUAGE plpgsql;

-- 测试6: 变量类型为另一个自定义 TYPE 的字段 %TYPE
--    这是一种深层锚定: custom_type.field%TYPE
--    GaussDB 不原生支持此语法，但某些迁移场景会模拟
CREATE OR REPLACE PROCEDURE pkg_type_test.test_nested_type_usage(p_emp_id BIGINT)
AS $$
DECLARE
    -- 直接使用复合类型变量并在 DECLARE 中初始化
    v_info       emp_info;
    v_summary    dept_summary;
    -- 包级变量风格: 用简单类型接收复合类型字段值
    v_salary     NUMERIC(18, 2);
    v_dept_name  VARCHAR(100);
BEGIN
    -- 获取复合类型并拆解字段
    v_info := pkg_type_test.get_emp_info(p_emp_id);
    v_salary := v_info.emp_salary;

    v_summary := pkg_type_test.get_dept_summary(1);
    v_dept_name := v_summary.dept_name;

    -- 用拆解后的简单类型做业务逻辑
    IF v_salary IS NOT NULL AND v_salary > 0 THEN
        UPDATE t_employees SET salary = v_salary * 1.1 WHERE id = p_emp_id;
        INSERT INTO t_log(id, msg)
        VALUES(50, 'Raised ' || v_info.emp_name || ' in dept ' || v_dept_name);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────────────
-- 7. 函数返回自定义 TYPE → 在另一个存过中赋值给变量
--    重点验证: 函数返回 composite type 后直接赋值给同类型变量
-- ─────────────────────────────────────────────────────────────────────

CREATE TYPE salary_report AS (
    emp_id      BIGINT,
    old_salary  NUMERIC(18, 2),
    new_salary  NUMERIC(18, 2),
    raise_pct   NUMERIC(5, 2)
);

-- 函数: 根据员工ID和涨幅比例，生成调薪报告
CREATE OR REPLACE FUNCTION pkg_type_test.calc_salary_raise(
    p_emp_id   BIGINT,
    p_pct      NUMERIC
)
RETURNS salary_report
AS $$
DECLARE
    v_report salary_report;
BEGIN
    v_report.emp_id := p_emp_id;
    SELECT salary INTO v_report.old_salary FROM t_employees WHERE id = p_emp_id;
    v_report.raise_pct := p_pct;
    v_report.new_salary := v_report.old_salary + v_report.old_salary * p_pct / 100;
    RETURN v_report;
END;
$$ LANGUAGE plpgsql;

-- 存过: 调用上面的函数，将返回值赋给变量，然后使用该变量的字段
CREATE OR REPLACE PROCEDURE pkg_type_test.test_func_assign_to_var(
    p_emp_id BIGINT
)
AS $$
DECLARE
    -- 用自定义 TYPE 声明变量，接收函数返回值
    v_raise_report  salary_report;
    v_emp_name      VARCHAR;
BEGIN
    -- 核心场景: 函数返回 TYPE，赋值给同类型变量
    v_raise_report := pkg_type_test.calc_salary_raise(p_emp_id, 15);

    -- 从赋值后的变量中取字段
    v_emp_name := pkg_type_test.get_emp_name(p_emp_id);

    -- 用变量字段做业务判断
    IF v_raise_report.new_salary > 20000 THEN
        INSERT INTO t_log(id, msg)
        VALUES(60, v_emp_name || ': raise too high, capped at 20000');
        UPDATE t_employees SET salary = 20000 WHERE id = p_emp_id;
    ELSE
        INSERT INTO t_log(id, msg)
        VALUES(61, v_emp_name || ': ' || v_raise_report.old_salary || ' -> ' || v_raise_report.new_salary);
        UPDATE t_employees SET salary = v_raise_report.new_salary WHERE id = p_emp_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────────────
-- 8. 综合测试: 员工年度绩效评审流程
--    覆盖: 多TYPE变量交互、函数嵌套调用、
--    多表DML(INSERT+UPDATE+DELETE)、复合字段运算、IS NOT NULL、
--    字符串拼接、嵌套IF/ELSIF、聚合函数、FOR-IN-SELECT循环
-- ─────────────────────────────────────────────────────────────────────

CREATE TYPE perf_review AS (
    emp_id          BIGINT,
    emp_name        VARCHAR(100),
    dept_id         BIGINT,
    old_salary      NUMERIC(18, 2),
    bonus_amount    NUMERIC(18, 2),
    new_salary      NUMERIC(18, 2),
    performance     VARCHAR(20)
);

CREATE TYPE dept_review_summary AS (
    dept_id         BIGINT,
    dept_name       VARCHAR(100),
    total_reviews   INTEGER,
    total_bonus     NUMERIC(18, 2),
    avg_salary      NUMERIC(18, 2)
);

CREATE TABLE IF NOT EXISTS t_performance_reviews (
    id              BIGINT,
    emp_id          BIGINT,
    review_year     INTEGER,
    performance     VARCHAR(20),
    bonus           NUMERIC(18, 2),
    new_salary      NUMERIC(18, 2)
);

CREATE OR REPLACE FUNCTION pkg_type_test.build_perf_review(
    p_emp_id    BIGINT,
    p_score     NUMERIC
)
RETURNS perf_review
AS $$
DECLARE
    v_review    perf_review;
    v_emp_info  emp_info;
BEGIN
    v_emp_info := pkg_type_test.get_emp_info(p_emp_id);

    v_review.emp_id := p_emp_id;
    v_review.emp_name := v_emp_info.emp_name;

    SELECT dept_id INTO v_review.dept_id FROM t_employees WHERE id = p_emp_id;

    v_review.old_salary := v_emp_info.emp_salary;

    IF p_score >= 90 THEN
        v_review.performance := 'EXCELLENT';
        v_review.bonus_amount := v_review.old_salary * 0.2;
    ELSIF p_score >= 70 THEN
        v_review.performance := 'GOOD';
        v_review.bonus_amount := v_review.old_salary * 0.1;
    ELSIF p_score >= 50 THEN
        v_review.performance := 'AVERAGE';
        v_review.bonus_amount := v_review.old_salary * 0.05;
    ELSE
        v_review.performance := 'BELOW';
        v_review.bonus_amount := 0;
    END IF;

    v_review.new_salary := v_review.old_salary + v_review.bonus_amount;

    RETURN v_review;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE pkg_type_test.test_annual_review(
    p_dept_id   BIGINT,
    p_year      INTEGER
)
AS $$
DECLARE
    v_review        perf_review;
    v_dept_summary  dept_review_summary;
    v_total_bonus   NUMERIC(18, 2);
    v_review_count  INTEGER;
    v_avg_salary    NUMERIC(18, 2);
    v_dept_name     VARCHAR(100);
    v_emp_rec       RECORD;
BEGIN
    v_total_bonus := 0;
    v_review_count := 0;

    v_dept_summary.dept_id := p_dept_id;
    v_dept_summary.total_reviews := 0;
    v_dept_summary.total_bonus := 0;

    SELECT dept_name INTO v_dept_name FROM t_departments WHERE id = p_dept_id;

    FOR v_emp_rec IN SELECT id, salary FROM t_employees
                     WHERE dept_id = p_dept_id AND status IS NOT NULL
                     ORDER BY id LOOP

        v_review := pkg_type_test.build_perf_review(v_emp_rec.id, 75);

        v_review_count := v_review_count + 1;
        v_total_bonus := v_total_bonus + v_review.bonus_amount;

        IF v_review.new_salary > 50000 THEN
            INSERT INTO t_performance_reviews(id, emp_id, review_year, performance, bonus, new_salary)
            VALUES(v_review.emp_id, v_review.emp_id, p_year, v_review.performance, v_review.new_salary * 0.01, v_review.new_salary);

            UPDATE t_employees SET salary = v_review.new_salary WHERE id = v_review.emp_id;
        ELSIF v_review.new_salary > 20000 THEN
            INSERT INTO t_performance_reviews(id, emp_id, review_year, performance, bonus, new_salary)
            VALUES(v_review.emp_id, v_review.emp_id, p_year, v_review.performance, v_review.bonus_amount, v_review.new_salary);

            UPDATE t_employees SET salary = v_review.new_salary WHERE id = v_review.emp_id;
        ELSE
            INSERT INTO t_performance_reviews(id, emp_id, review_year, performance, bonus, new_salary)
            VALUES(v_review.emp_id, v_review.emp_id, p_year, v_review.performance, 0, v_review.old_salary);

            DELETE FROM t_performance_reviews WHERE emp_id = v_review.emp_id AND review_year < p_year;
        END IF;

    END LOOP;

    v_avg_salary := pkg_type_test.calc_dept_total_salary(p_dept_id);
    IF v_review_count > 0 THEN
        v_avg_salary := v_avg_salary / v_review_count;
    END IF;

    v_dept_summary.dept_name := v_dept_name;
    v_dept_summary.total_reviews := v_review_count;
    v_dept_summary.total_bonus := v_total_bonus;
    v_dept_summary.avg_salary := v_avg_salary;

    INSERT INTO t_log(id, msg)
    VALUES(100, 'Dept ' || v_dept_summary.dept_name || ': ' || v_dept_summary.total_reviews || ' reviews, total bonus=' || v_dept_summary.total_bonus);

    IF v_dept_summary.total_bonus > 100000 THEN
        INSERT INTO t_log(id, msg) VALUES(101, 'Budget warning for dept: ' || v_dept_name);
    END IF;

    IF v_dept_summary.avg_salary > 30000 AND v_dept_summary.total_reviews > 5 THEN
        UPDATE t_departments SET budget = v_dept_summary.avg_salary * v_dept_summary.total_reviews * 1.5
        WHERE id = p_dept_id;
    END IF;
END;
$$ LANGUAGE plpgsql;
