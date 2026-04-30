-- ============================================================================
-- 员工管理模块 (pkg_employee_comments)
-- 业务领域: 人力资源
-- 创建日期: 2024-06-15
-- 作者: zhangsan
-- 最后修改: lisi, 2024-08-20 — 增加了批量导入功能
-- ============================================================================

-- 根据部门ID查询员工列表
-- 支持分页查询，返回 REFCURSOR
CREATE OR REPLACE PROCEDURE pkg_employee_comments.list_by_dept(
    p_dept_id BIGINT,
    p_page INT,
    p_page_size INT,
    out_code OUT VARCHAR,
    out_msg OUT VARCHAR,
    out_list OUT REFCURSOR
) AS $$
DECLARE
    v_offset INT;
    v_total INT;
BEGIN
    out_code := '0';
    out_msg := '查询成功';

    -- 先查总数
    SELECT count(1) INTO v_total
    FROM t_employees
    WHERE dept_id = p_dept_id AND status = 'ACTIVE';

    IF v_total <= 0 THEN
        OPEN out_list FOR SELECT NULL WHERE 1 = 0;
        RETURN;
    END IF;

    -- 计算分页偏移量
    v_offset := (p_page - 1) * p_page_size;

    /* 分页查询主数据
       使用 row_number() 实现服务端分页
       注意: PostgreSQL 中 OFFSET 从 0 开始 */
    OPEN out_list FOR
    SELECT id, name, email, hire_date, dept_id,
           row_number() OVER (ORDER BY hire_date DESC) AS rn
    FROM t_employees
    WHERE dept_id = p_dept_id AND status = 'ACTIVE'
    ORDER BY hire_date DESC
    LIMIT p_page_size OFFSET v_offset;

    out_code := '0';
    out_msg := '查询成功';
END;
$$ LANGUAGE plpgsql;


-- 新增员工
-- 自动发送入职通知邮件
CREATE OR REPLACE PROCEDURE pkg_employee_comments.add_employee(
    p_name VARCHAR,
    p_email VARCHAR,
    p_dept_id BIGINT,
    p_hire_date DATE,
    out_code OUT VARCHAR,
    out_msg OUT VARCHAR
) AS $$
DECLARE
    v_emp_id BIGINT;
    v_count INT;
BEGIN
    -- 校验邮箱唯一性
    SELECT count(1) INTO v_count
    FROM t_employees WHERE email = p_email;

    IF v_count > 0 THEN
        out_code := '1';
        out_msg := '邮箱已存在: ' || p_email;
        RETURN;
    END IF;

    /* 插入员工记录
       状态默认为 ACTIVE */
    INSERT INTO t_employees(name, email, dept_id, hire_date, status)
    VALUES (p_name, p_email, p_dept_id, p_hire_date, 'ACTIVE');

    -- 获取新插入的ID
    SELECT max(id) INTO v_emp_id FROM t_employees WHERE email = p_email;

    -- 记录操作日志
    PERFORM pkg_common.log_operation('EMPLOYEE', 'ADD', v_emp_id);

    -- 发送入职欢迎邮件
    PERFORM pkg_common.send_notification('EMAIL', '欢迎新员工: ' || p_name);

    out_code := '0';
    out_msg := '入职成功';
END;
$$ LANGUAGE plpgsql;


-- 变更员工所属部门（部门调动）
-- 涉及审批流程，此处仅处理数据层变更
CREATE OR REPLACE PROCEDURE pkg_employee_comments.transfer_dept(
    p_emp_id BIGINT,
    p_new_dept_id BIGINT,
    out_code OUT VARCHAR,
    out_msg OUT VARCHAR
) AS $$
DECLARE
    v_old_dept_id BIGINT;
BEGIN
    -- 查询当前部门
    SELECT dept_id INTO v_old_dept_id
    FROM t_employees WHERE id = p_emp_id;

    /* 安全校验:
       1. 目标部门不能与当前部门相同
       2. 后续版本会增加审批状态检查 */
    IF v_old_dept_id = p_new_dept_id THEN
        out_code := '1';
        out_msg := '目标部门与当前部门相同';
        RETURN;
    END IF;

    UPDATE t_employees SET dept_id = p_new_dept_id WHERE id = p_emp_id;

    -- 记录调动日志
    PERFORM pkg_common.log_operation('EMPLOYEE', 'TRANSFER', p_emp_id);
    PERFORM pkg_common.send_notification('SMS', '部门变更通知');

    out_code := '0';
    out_msg := '调动成功';
END;
$$ LANGUAGE plpgsql;


/* 离职处理
   - 将员工状态改为 INACTIVE
   - 保留历史记录，不物理删除
   - 触发资产归还流程的通知 */
CREATE OR REPLACE PROCEDURE pkg_employee_comments.resign(
    p_emp_id BIGINT,
    out_code OUT VARCHAR,
    out_msg OUT VARCHAR
) AS $$
BEGIN
    UPDATE t_employees SET status = 'INACTIVE' WHERE id = p_emp_id;

    PERFORM pkg_common.log_operation('EMPLOYEE', 'RESIGN', p_emp_id);

    -- 通知行政归还资产
    PERFORM pkg_common.send_notification('EMAIL', '请安排资产归还');
    -- 通知IT回收账号
    PERFORM pkg_common.send_notification('SMS', '请回收系统账号');

    out_code := '0';
    out_msg := '离职处理完成';
END;
$$ LANGUAGE plpgsql;


-- 批量导入员工（按部门批量入职）
-- 用于系统初始化或并购场景
CREATE OR REPLACE PROCEDURE pkg_employee_comments.batch_import(
    p_dept_id BIGINT,
    p_emp_list VARCHAR
) AS $$
BEGIN
    -- 此处简化实现，实际应对 p_emp_list 进行解析
    INSERT INTO t_employees(name, email, dept_id, hire_date, status)
    VALUES ('batch_user', 'batch@example.com', p_dept_id, CURRENT_DATE, 'ACTIVE');
    -- JUST FOR TEST COMMENTS

    PERFORM pkg_common.log_operation('EMPLOYEE', 'BATCH_IMPORT', p_dept_id);
END;
$$ LANGUAGE plpgsql;
