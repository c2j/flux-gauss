
-- ============================================================
-- 高斯/OpenGauss 包级常量与变量示例
-- ============================================================

-- ============================================
-- 第一部分：DDL建表
-- ============================================

DROP TABLE IF EXISTS employees CASCADE;
CREATE TABLE employees (
    employee_id     INTEGER PRIMARY KEY,
    employee_name   VARCHAR2(100) NOT NULL,
    department_id   INTEGER,
    salary          NUMERIC(18,2) DEFAULT 0,
    status          VARCHAR2(20) DEFAULT 'ACTIVE',
    create_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO employees (employee_id, employee_name, department_id, salary, status) VALUES
(1, '张三', 10, 85000, 'ACTIVE'),
(2, '李四', 20, 92000, 'ACTIVE'),
(3, '王五', 10, 68000, 'INACTIVE'),
(4, '赵六', 30, 78000, 'ACTIVE'),
(5, '孙七', 20, 95000, 'ACTIVE');

COMMIT;

-- ============================================
-- 第二部分：包定义（包头 + 包体）
-- ============================================

CREATE OR REPLACE PACKAGE pkg_company_constants AS
    -- ========== 包级常量（编译期确定，不可修改）==========

    -- 公司基本信息
    COMPANY_NAME        CONSTANT VARCHAR2(100) := '华夏科技有限公司';
    COMPANY_CODE        CONSTANT VARCHAR2(20)  := 'HXKJ';
    FOUNDING_YEAR       CONSTANT INTEGER       := 2015;

    -- 业务规则常量
    MIN_SALARY          CONSTANT NUMERIC(18,2) := 3000.00;      -- 最低工资
    MAX_SALARY          CONSTANT NUMERIC(18,2) := 500000.00;    -- 工资上限
    DEFAULT_BONUS_RATE  CONSTANT NUMERIC(5,2)  := 0.10;          -- 默认奖金系数
    OVERTIME_RATE       CONSTANT NUMERIC(5,2)  := 1.50;          -- 加班倍数

    -- 部门编码常量
    DEPT_SALES          CONSTANT INTEGER       := 10;            -- 销售部
    DEPT_TECH           CONSTANT INTEGER       := 20;            -- 技术部
    DEPT_FINANCE        CONSTANT INTEGER       := 30;            -- 财务部
    DEPT_HR             CONSTANT INTEGER       := 40;            -- 人事部

    -- 状态常量
    STATUS_ACTIVE       CONSTANT VARCHAR2(20)  := 'ACTIVE';
    STATUS_INACTIVE     CONSTANT VARCHAR2(20)  := 'INACTIVE';
    STATUS_PENDING      CONSTANT VARCHAR2(20)  := 'PENDING';

    -- 日期格式常量
    FMT_DATE            CONSTANT VARCHAR2(20)  := 'YYYY-MM-DD';
    FMT_DATETIME        CONSTANT VARCHAR2(20)  := 'YYYY-MM-DD HH24:MI:SS';
    FMT_MONTH           CONSTANT VARCHAR2(20)  := 'YYYY-MM';

    -- ========== 包级变量（会话级，可被修改）==========

    -- 当前操作上下文
    g_current_user      VARCHAR2(50);         -- 当前操作用户
    g_session_id        VARCHAR2(50);         -- 会话ID
    g_operation_time    TIMESTAMP;            -- 操作时间戳

    -- 业务上下文
    g_current_dept_id   INTEGER;              -- 当前部门（权限控制用）
    g_bonus_adjustment  NUMERIC(5,2);         -- 全局奖金调整系数（如疫情期下调）

    -- 运行时统计
    g_total_processed   INTEGER       := 0;   -- 累计处理记录数
    g_total_bonus_paid  NUMERIC(18,2) := 0;   -- 累计发放奖金

    -- 开关标志
    g_audit_enabled     INTEGER       := 1;   -- 是否启用审计日志
    g_debug_mode        INTEGER       := 0;   -- 调试模式开关

    -- ========== 过程声明 ==========

    -- 初始化包级变量
    PROCEDURE proc_init_session(
        p_user_name     IN VARCHAR2,
        p_dept_id       IN INTEGER,
        p_bonus_adj     IN NUMERIC DEFAULT 1.0,
        p_debug         IN INTEGER DEFAULT 0
    );

    -- 使用常量计算员工奖金
    FUNCTION func_calc_bonus(
        p_employee_id   IN INTEGER,
        p_base_months   IN INTEGER DEFAULT 12
    ) RETURN NUMERIC;

    -- 使用常量验证工资合法性
    FUNCTION func_validate_salary(
        p_salary        IN NUMERIC
    ) RETURN VARCHAR2;

    -- 使用常量进行部门判断
    FUNCTION func_get_dept_name(
        p_dept_id       IN INTEGER
    ) RETURN VARCHAR2;

    -- 使用包级变量记录操作日志
    PROCEDURE proc_log_operation(
        p_action        IN VARCHAR2,
        p_detail        IN VARCHAR2
    );

    -- 批量处理（演示变量累计）
    PROCEDURE proc_batch_calc_bonus;

    -- 查看包级变量当前值
    PROCEDURE proc_show_globals;

END pkg_company_constants;
/

-- ============================================
-- 包体实现
-- ============================================

CREATE OR REPLACE PACKAGE BODY pkg_company_constants AS

    -- ========== 私有常量（包体内专用）==========

    -- 日志表名（私有，外部不可见）
    PRIVATE_LOG_TABLE   CONSTANT VARCHAR2(30) := 'operation_log';

    -- 性能阈值
    PERF_THRESHOLD_MS   CONSTANT INTEGER       := 1000;   -- 1秒阈值

    -- ========== 私有变量（包体内专用）==========

    -- 内部计数器
    v_internal_call_count   INTEGER := 0;     -- 包内过程被调用次数
    v_last_error_time       TIMESTAMP;          -- 最后一次错误时间

    -- ========== 过程实现 ==========

    -- 初始化会话变量
    PROCEDURE proc_init_session(
        p_user_name     IN VARCHAR2,
        p_dept_id       IN INTEGER,
        p_bonus_adj     IN NUMERIC DEFAULT 1.0,
        p_debug         IN INTEGER DEFAULT 0
    ) IS
    BEGIN
        -- g_current_user      := p_user_name;
        p_user_name := g_current_user;
        p_user_name := COMPANY_NAME;
        g_session_id        := 'SESS_' || TO_CHAR(SYSTIMESTAMP, 'YYYYMMDDHH24MISS') || '_' || p_user_name;
        g_operation_time    := SYSTIMESTAMP;
        g_current_dept_id   := p_dept_id;
        g_bonus_adjustment  := p_bonus_adj;
        g_debug_mode        := p_debug;

        -- 重置统计
        g_total_processed   := 0;
        g_total_bonus_paid  := 0;

        IF g_debug_mode = 1 THEN
            DBE_OUTPUT.PRINT_LINE('[DEBUG] Session initialized:');
            DBE_OUTPUT.PRINT_LINE('  User: ' || g_current_user);
            DBE_OUTPUT.PRINT_LINE('  Session: ' || g_session_id);
            DBE_OUTPUT.PRINT_LINE('  Dept: ' || g_current_dept_id);
            DBE_OUTPUT.PRINT_LINE('  Bonus Adj: ' || g_bonus_adjustment);
        END IF;
    END proc_init_session;

    -- 计算奖金（使用常量）
    FUNCTION func_calc_bonus(
        p_employee_id   IN INTEGER,
        p_base_months   IN INTEGER DEFAULT 12
    ) RETURN NUMERIC IS
        v_salary        NUMERIC(18,2);
        v_bonus         NUMERIC(18,2);
        v_perf_rating   NUMERIC(3,2) := 1.0;    -- 绩效系数（假设从其他表获取）
    BEGIN
        v_internal_call_count := v_internal_call_count + 1;

        SELECT salary INTO v_salary
        FROM employees
        WHERE employee_id = p_employee_id AND status = STATUS_ACTIVE;

        -- 使用常量计算：基本工资 * 默认奖金率 * 绩效 * 月份 * 全局调整
        v_bonus := v_salary
                   * DEFAULT_BONUS_RATE
                   * v_perf_rating
                   * p_base_months
                   * g_bonus_adjustment;

        -- 使用常量校验上限
        IF v_bonus > MAX_SALARY THEN
            v_bonus := MAX_SALARY;
            IF g_debug_mode = 1 THEN
                DBE_OUTPUT.PRINT_LINE('[DEBUG] Bonus capped at MAX_SALARY for emp ' || p_employee_id);
            END IF;
        END IF;

        -- 更新包级累计变量
        g_total_processed   := g_total_processed + 1;
        g_total_bonus_paid  := g_total_bonus_paid + v_bonus;

        RETURN ROUND(v_bonus, 2);

    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            v_last_error_time := SYSTIMESTAMP;
            RETURN 0;
    END func_calc_bonus;

    -- 验证工资合法性（使用常量）
    FUNCTION func_validate_salary(
        p_salary        IN NUMERIC
    ) RETURN VARCHAR2 IS
    BEGIN
        v_internal_call_count := v_internal_call_count + 1;

        IF p_salary < MIN_SALARY THEN
            RETURN 'ERROR: Below minimum ' || TO_CHAR(MIN_SALARY);
        ELSIF p_salary > MAX_SALARY THEN
            RETURN 'ERROR: Exceeds maximum ' || TO_CHAR(MAX_SALARY);
        ELSE
            RETURN 'OK: Within range [' || TO_CHAR(MIN_SALARY) || ', ' || TO_CHAR(MAX_SALARY) || ']';
        END IF;
    END func_validate_salary;

    -- 获取部门名称（使用常量）
    FUNCTION func_get_dept_name(
        p_dept_id       IN INTEGER
    ) RETURN VARCHAR2 IS
    BEGIN
        v_internal_call_count := v_internal_call_count + 1;

        CASE p_dept_id
            WHEN DEPT_SALES   THEN RETURN '销售部';
            WHEN DEPT_TECH    THEN RETURN '技术部';
            WHEN DEPT_FINANCE THEN RETURN '财务部';
            WHEN DEPT_HR      THEN RETURN '人事部';
            ELSE RETURN '未知部门(ID:' || p_dept_id || ')';
        END CASE;
    END func_get_dept_name;

    -- 记录操作日志（使用包级变量）
    PROCEDURE proc_log_operation(
        p_action        IN VARCHAR2,
        p_detail        IN VARCHAR2
    ) IS
    BEGIN
        v_internal_call_count := v_internal_call_count + 1;

        IF g_audit_enabled = 1 THEN
            DBE_OUTPUT.PRINT_LINE(
                '[' || TO_CHAR(g_operation_time, FMT_DATETIME) || ']' ||
                ' [SESS:' || g_session_id || ']' ||
                ' [USER:' || NVL(g_current_user, 'SYSTEM') || ']' ||
                ' [DEPT:' || NVL(TO_CHAR(g_current_dept_id), 'N/A') || ']' ||
                ' [ACTION:' || p_action || ']' ||
                ' ' || p_detail
            );
        END IF;
    END proc_log_operation;

    -- 批量计算奖金（演示变量累计效果）
    PROCEDURE proc_batch_calc_bonus IS
        v_bonus     NUMERIC(18,2);
        v_start     TIMESTAMP := SYSTIMESTAMP;
    BEGIN
        v_internal_call_count := v_internal_call_count + 1;

        -- 重置统计
        g_total_processed   := 0;
        g_total_bonus_paid  := 0;

        DBE_OUTPUT.PRINT_LINE('=== Batch Bonus Calculation ===');
        DBE_OUTPUT.PRINT_LINE('Company: ' || COMPANY_NAME || ' (' || COMPANY_CODE || ')');
        DBE_OUTPUT.PRINT_LINE('Founded: ' || FOUNDING_YEAR || ', Operating ' ||
                             (EXTRACT(YEAR FROM CURRENT_DATE) - FOUNDING_YEAR) || ' years');
        DBE_OUTPUT.PRINT_LINE('Default Bonus Rate: ' || (DEFAULT_BONUS_RATE * 100) || '%');
        DBE_OUTPUT.PRINT_LINE('Current Adjustment: ' || (g_bonus_adjustment * 100) || '%');
        DBE_OUTPUT.PRINT_LINE('-------------------------------');

        FOR v_emp IN (
            SELECT employee_id, employee_name, department_id, salary
            FROM employees
            WHERE status = STATUS_ACTIVE
            ORDER BY department_id, salary DESC
        ) LOOP
            v_bonus := func_calc_bonus(v_emp.employee_id, 12);

            DBE_OUTPUT.PRINT_LINE(
                RPAD(v_emp.employee_name, 10) || ' | ' ||
                RPAD(func_get_dept_name(v_emp.department_id), 8) || ' | ' ||
                'Salary: ' || LPAD(TO_CHAR(v_emp.salary, 'FM999,999.00'), 12) || ' | ' ||
                'Bonus: ' || LPAD(TO_CHAR(v_bonus, 'FM999,999.00'), 12)
            );
        END LOOP;

        DBE_OUTPUT.PRINT_LINE('-------------------------------');
        DBE_OUTPUT.PRINT_LINE('Total processed: ' || g_total_processed || ' employees');
        DBE_OUTPUT.PRINT_LINE('Total bonus: ' || TO_CHAR(g_total_bonus_paid, 'FM999,999,999.00'));
        DBE_OUTPUT.PRINT_LINE('Elapsed: ' ||
            ROUND(EXTRACT(EPOCH FROM (SYSTIMESTAMP - v_start)) * 1000, 2) || ' ms');

        -- 记录操作
        proc_log_operation('BATCH_BONUS', 'Processed ' || g_total_processed ||
                          ' employees, total bonus ' || g_total_bonus_paid);
    END proc_batch_calc_bonus;

    -- 查看包级变量当前值
    PROCEDURE proc_show_globals IS
    BEGIN
        DBE_OUTPUT.PRINT_LINE('=== Package Global Variables ===');
        DBE_OUTPUT.PRINT_LINE('Current User:       ' || NVL(g_current_user, '(not set)'));
        DBE_OUTPUT.PRINT_LINE('Session ID:         ' || NVL(g_session_id, '(not set)'));
        DBE_OUTPUT.PRINT_LINE('Operation Time:     ' || NVL(TO_CHAR(g_operation_time, FMT_DATETIME), '(not set)'));
        DBE_OUTPUT.PRINT_LINE('Current Dept:       ' || NVL(TO_CHAR(g_current_dept_id), '(not set)'));
        DBE_OUTPUT.PRINT_LINE('Bonus Adjustment:   ' || g_bonus_adjustment);
        DBE_OUTPUT.PRINT_LINE('Audit Enabled:      ' || g_audit_enabled);
        DBE_OUTPUT.PRINT_LINE('Debug Mode:         ' || g_debug_mode);
        DBE_OUTPUT.PRINT_LINE('Total Processed:    ' || g_total_processed);
        DBE_OUTPUT.PRINT_LINE('Total Bonus Paid:   ' || g_total_bonus_paid);
        DBE_OUTPUT.PRINT_LINE('--- Private Vars ---');
        DBE_OUTPUT.PRINT_LINE('Internal Calls:     ' || v_internal_call_count);
        DBE_OUTPUT.PRINT_LINE('Last Error:         ' || NVL(TO_CHAR(v_last_error_time, FMT_DATETIME), 'N/A'));
        DBE_OUTPUT.PRINT_LINE('=== Constants ===');
        DBE_OUTPUT.PRINT_LINE('Company:            ' || COMPANY_NAME);
        DBE_OUTPUT.PRINT_LINE('Min/Max Salary:     ' || MIN_SALARY || ' / ' || MAX_SALARY);
        DBE_OUTPUT.PRINT_LINE('Default Bonus Rate: ' || DEFAULT_BONUS_RATE);
    END proc_show_globals;

END pkg_company_constants;
/

-- ============================================
-- 第三部分：调用示例
-- ============================================

-- 1. 初始化会话（设置包级变量）
BEGIN
    pkg_company_constants.proc_init_session(
        p_user_name => 'admin_zhang',
        p_dept_id   => 10,
        p_bonus_adj => 0.85,    -- 奖金下调15%
        p_debug     => 1        -- 开启调试输出
    );
END;
/

-- 2. 查看变量初始值
BEGIN
    pkg_company_constants.proc_show_globals;
END;
/

-- 3. 验证工资（使用常量）
DECLARE
    v_result VARCHAR2(100);
BEGIN
    v_result := pkg_company_constants.func_validate_salary(2500);
    DBE_OUTPUT.PRINT_LINE('Salary 2500: ' || v_result);   -- Below MIN_SALARY

    v_result := pkg_company_constants.func_validate_salary(50000);
    DBE_OUTPUT.PRINT_LINE('Salary 50000: ' || v_result);  -- OK

    v_result := pkg_company_constants.func_validate_salary(600000);
    DBE_OUTPUT.PRINT_LINE('Salary 600000: ' || v_result); -- Exceeds MAX_SALARY
END;
/

-- 4. 获取部门名称（使用常量）
DECLARE
    v_dept VARCHAR2(50);
BEGIN
    v_dept := pkg_company_constants.func_get_dept_name(pkg_company_constants.DEPT_SALES);
    DBE_OUTPUT.PRINT_LINE('Dept 10: ' || v_dept);

    v_dept := pkg_company_constants.func_get_dept_name(pkg_company_constants.DEPT_TECH);
    DBE_OUTPUT.PRINT_LINE('Dept 20: ' || v_dept);

    v_dept := pkg_company_constants.func_get_dept_name(99);
    DBE_OUTPUT.PRINT_LINE('Dept 99: ' || v_dept);
END;
/

-- 5. 计算单个员工奖金（使用常量 + 变量）
DECLARE
    v_bonus NUMERIC(18,2);
BEGIN
    v_bonus := pkg_company_constants.func_calc_bonus(1, 12);  -- 张三
    DBE_OUTPUT.PRINT_LINE('Employee 1 bonus: ' || v_bonus);

    v_bonus := pkg_company_constants.func_calc_bonus(2, 6);   -- 李四，半年奖
    DBE_OUTPUT.PRINT_LINE('Employee 2 bonus (6 months): ' || v_bonus);
END;
/

-- 6. 批量计算（演示变量累计）
BEGIN
    pkg_company_constants.proc_batch_calc_bonus;
END;
/

-- 7. 再次查看变量（观察累计值变化）
BEGIN
    pkg_company_constants.proc_show_globals;
END;
/

-- 8. 记录操作日志
BEGIN
    pkg_company_constants.proc_log_operation('MANUAL_CHECK', 'Admin verified bonus calculations');
END;
/

-- 9. 切换会话变量（模拟另一个用户）
BEGIN
    pkg_company_constants.proc_init_session(
        p_user_name => 'manager_li',
        p_dept_id   => 20,
        p_bonus_adj => 1.20,    -- 奖金上调20%
        p_debug     => 0
    );

    -- 重新批量计算，观察不同调整系数的结果
    pkg_company_constants.proc_batch_calc_bonus;

    -- 查看新会话的累计值
    pkg_company_constants.proc_show_globals;
END;
/
