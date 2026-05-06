-- ============================================================
-- 扩展包规范：增加触发器联动接口
-- ============================================================
CREATE OR REPLACE PACKAGE complex_clearing_pkg AS

    -- 原有声明保留...
    c_max_retry CONSTANT INT := 3;
    c_epsilon CONSTANT NUMERIC := 1e-9;
    "GOTO_LABEL_真的能用中文吗" CONSTANT TEXT := '测试Unicode标识符';

    TYPE t_trade_cursor IS REF CURSOR RETURN trade_record%ROWTYPE;
    TYPE t_generic_cursor IS REF CURSOR;
    TYPE t_amount_array IS VARRAY(100) OF NUMERIC(18,4);
    TYPE t_nested_table IS TABLE OF t_amount_array;

    TYPE t_base_rec IS RECORD (id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    TYPE t_trade_rec IS RECORD (base t_base_rec, amount NUMERIC, status VARCHAR(20));

    e_insufficient_funds EXCEPTION; PRAGMA EXCEPTION_INIT(e_insufficient_funds, -20001);
    e_stale_data EXCEPTION; PRAGMA EXCEPTION_INIT(e_stale_data, -20002);
    e_infinite_loop_guard EXCEPTION; PRAGMA EXCEPTION_INIT(e_infinite_loop_guard, -20003);
    e_just_because EXCEPTION;
    e_trigger_recursion EXCEPTION; PRAGMA EXCEPTION_INIT(e_trigger_recursion, -20005);
    e_ddl_intercept EXCEPTION; PRAGMA EXCEPTION_INIT(e_ddl_intercept, -20006);

    g_session_counter INT := 0;
    g_last_trade_id BIGINT;
    g_audit_trail CLOB := '';

    -- 新增：触发器共享状态（跨调用持久化，模拟"脏"全局状态）
    g_trigger_depth INT := 0;
    g_mutating_table_guard BOOLEAN := FALSE;
    g_ddl_stack TEXT := '';

    FUNCTION calc_fee(p_amount IN NUMERIC) RETURN NUMERIC;
    FUNCTION calc_fee(p_amount IN NUMERIC, p_vip_level IN INT DEFAULT 1) RETURN NUMERIC;
    FUNCTION calc_fee(p_amount IN NUMERIC, p_discount_rate IN NUMERIC, p_apply_ceil IN BOOLEAN) RETURN NUMERIC;

    PROCEDURE run_clearing(p_batch_date IN DATE, p_parallel_degree IN INT DEFAULT 4, p_dry_run IN BOOLEAN DEFAULT FALSE, o_summary OUT CLOB, o_status OUT VARCHAR(50));
    FUNCTION get_suspicious_trades(p_threshold NUMERIC) RETURN t_generic_cursor;
    PROCEDURE log_audit_autonomous(p_msg IN TEXT, p_severity IN VARCHAR(10) DEFAULT 'INFO');

    -- 新增：触发器专用接口
    PROCEDURE handle_trade_change(
        p_op IN VARCHAR(10),           -- INSERT/UPDATE/DELETE
        p_old_rec IN trade_record%ROWTYPE,
        p_new_rec IN trade_record%ROWTYPE,
        p_trigger_name IN VARCHAR(100),
        o_skip_default IN OUT BOOLEAN   -- 是否跳过默认处理
    );

    PROCEDURE handle_account_change(
        p_op IN VARCHAR(10),
        p_old_rec IN account%ROWTYPE,
        p_new_rec IN account%ROWTYPE,
        p_cascade IN BOOLEAN DEFAULT TRUE
    );

    -- DDL 事件处理（事件触发器回调）
    PROCEDURE handle_ddl_event(
        p_tag IN VARCHAR(50),
        p_object_name IN VARCHAR(200),
        p_object_type IN VARCHAR(50),
        p_command IN TEXT
    );

    -- 递归/嵌套调用控制
    FUNCTION recursive_validate(
        p_trade_id IN BIGINT,
        p_depth IN INT DEFAULT 0
    ) RETURN VARCHAR(200);

    -- 跨表一致性检查（触发器内调用）
    FUNCTION check_cross_table_consistency(
        p_account_id IN BIGINT,
        p_check_mode IN INT DEFAULT 1   -- 1=严格, 2=宽松, 3=仅审计
    ) RETURN BOOLEAN;

END complex_clearing_pkg;
/

-- ============================================================
-- 扩展包体：触发器联动实现
-- ============================================================
CREATE OR REPLACE PACKAGE BODY complex_clearing_pkg AS

    g_private_cache t_nested_table := t_nested_table();
    g_loop_counter INT := 0;

    FUNCTION "私有函数_居然支持$特殊字符#"(p_input IN NUMERIC) RETURN NUMERIC IS
    BEGIN RETURN p_input * (1 + c_epsilon); END;

    FUNCTION calc_fee(p_amount IN NUMERIC) RETURN NUMERIC IS BEGIN RETURN calc_fee(p_amount, 1); END;

    FUNCTION calc_fee(p_amount IN NUMERIC, p_vip_level IN INT DEFAULT 1) RETURN NUMERIC IS
        v_rate NUMERIC;
    BEGIN
        v_rate := CASE p_vip_level WHEN 1 THEN 0.0015 WHEN 2 THEN 0.0010 WHEN 3 THEN 0.0005 ELSE 0.0020 END;
        RETURN ROUND(p_amount * v_rate, 2);
    END;

    FUNCTION calc_fee(p_amount IN NUMERIC, p_discount_rate IN NUMERIC, p_apply_ceil IN BOOLEAN) RETURN NUMERIC IS
        v_raw NUMERIC;
    BEGIN
        v_raw := p_amount * p_discount_rate;
        IF p_apply_ceil IS TRUE THEN RETURN CEIL(v_raw); ELSE RETURN v_raw; END IF;
    END;

    PROCEDURE log_audit_autonomous(p_msg IN TEXT, p_severity IN VARCHAR(10) DEFAULT 'INFO') IS
        PRAGMA AUTONOMOUS_TRANSACTION;
    BEGIN
        INSERT INTO audit_log (log_time, severity, message, session_id)
        VALUES (CURRENT_TIMESTAMP, p_severity, p_msg, pg_backend_pid());
        COMMIT;
    EXCEPTION WHEN OTHERS THEN ROLLBACK;
    END;

    -- ============================================================
    -- 核心新增：递归验证（触发器内可能调用，形成调用链）
    -- ============================================================
    FUNCTION recursive_validate(p_trade_id IN BIGINT, p_depth IN INT DEFAULT 0) RETURN VARCHAR IS
        v_parent_id BIGINT;
        v_chain TEXT := '';
        v_result VARCHAR(200);
    BEGIN
        -- 递归深度守卫
        IF p_depth > 10 THEN
            RAISE e_trigger_recursion USING MESSAGE = 'Validation depth exceeded for trade ' || p_trade_id;
        END IF;

        -- 查找关联父交易（自引用表模拟层级）
        SELECT parent_trade_id INTO v_parent_id
        FROM trade_record WHERE trade_id = p_trade_id;

        IF v_parent_id IS NOT NULL THEN
            -- 递归调用自身！触发器内调用此函数可能形成递归爆炸
            v_result := recursive_validate(v_parent_id, p_depth + 1);
            v_chain := p_trade_id || '->' || v_result;
        ELSE
            v_chain := p_trade_id || '(root)';
        END IF;

        -- 模拟复杂校验逻辑
        IF p_depth = 0 AND LENGTH(v_chain) > 50 THEN
            -- 顶层返回时做汇总判断
            RETURN 'DEEP_CHAIN:' || v_chain;
        END IF;

        RETURN v_chain;
    END;

    -- ============================================================
    -- 核心新增：跨表一致性检查（触发器内调用，可能触发变异表错误）
    -- ============================================================
    FUNCTION check_cross_table_consistency(p_account_id IN BIGINT, p_check_mode IN INT DEFAULT 1) RETURN BOOLEAN IS
        v_balance NUMERIC;
        v_trade_sum NUMERIC;
        v_diff NUMERIC;
        v_threshold NUMERIC;
    BEGIN
        -- 变异表守卫：防止触发器内查询本表导致 ORA-04091 类错误
        IF g_mutating_table_guard THEN
            log_audit_autonomous('Mutating table guard hit for account ' || p_account_id, 'WARN');
            RETURN TRUE;  -- 保守通过
        END IF;

        -- 设置守卫（非线程安全，故意演示问题）
        g_mutating_table_guard := TRUE;

        BEGIN
            -- 读取账户余额
            SELECT balance INTO v_balance FROM account WHERE account_id = p_account_id;

            -- 读取交易汇总（这里可能触发变异表错误！）
            SELECT COALESCE(SUM(amount - fee), 0) INTO v_trade_sum
            FROM trade_record
            WHERE account_id = p_account_id AND status = 'SETTLED';

            v_diff := ABS(v_balance - v_trade_sum);
            v_threshold := CASE p_check_mode
                WHEN 1 THEN c_epsilon
                WHEN 2 THEN 1.0
                ELSE 999999999
            END;

            IF v_diff > v_threshold THEN
                IF p_check_mode = 3 THEN
                    -- 仅审计模式，不抛异常
                    log_audit_autonomous('Consistency mismatch: account=' || p_account_id || ' diff=' || v_diff, 'WARN');
                    RETURN TRUE;
                ELSE
                    RETURN FALSE;
                END IF;
            END IF;

            RETURN TRUE;
        EXCEPTION WHEN OTHERS THEN
            g_mutating_table_guard := FALSE;
            RAISE;
        END;

        g_mutating_table_guard := FALSE;
    END;

    -- ============================================================
    -- 核心新增：触发器变化处理主入口（最复杂的控制流）
    -- ============================================================
    PROCEDURE handle_trade_change(
        p_op IN VARCHAR(10),
        p_old_rec IN trade_record%ROWTYPE,
        p_new_rec IN trade_record%ROWTYPE,
        p_trigger_name IN VARCHAR(100),
        o_skip_default IN OUT BOOLEAN
    ) IS
        v_cascade_account BOOLEAN := FALSE;
        v_validation_result VARCHAR(200);
        v_temp_trade_id BIGINT;
        v_audit_msg TEXT;
        v_need_rollback BOOLEAN := FALSE;

        -- 局部游标（触发器内动态声明）
        CURSOR c_related_trades(p_acct BIGINT, p_except_id BIGINT) IS
            SELECT trade_id, amount, status
            FROM trade_record
            WHERE account_id = p_acct AND trade_id != p_except_id
            FOR UPDATE OF trade_record;  -- 锁定相关行
    BEGIN
        -- 递归深度检查（触发器级联调用自我防护）
        g_trigger_depth := g_trigger_depth + 1;
        IF g_trigger_depth > 5 THEN
            RAISE e_trigger_recursion USING MESSAGE = 'Trigger cascade too deep: ' || p_trigger_name;
        END IF;

        -- 操作分发（模拟 switch，但用 GOTO 跳转表风格）
        IF p_op = 'INSERT' THEN
            GOTO handle_insert;
        ELSIF p_op = 'UPDATE' THEN
            GOTO handle_update;
        ELSIF p_op = 'DELETE' THEN
            GOTO handle_delete;
        ELSE
            GOTO unknown_op;
        END IF;

        <<handle_insert>>
        -- 新插入交易：验证账户存在、余额充足、递归检查链
        BEGIN
            -- 检查账户存在（可能触发 account 表触发器！）
            PERFORM 1 FROM account WHERE account_id = p_new_rec.account_id;
            IF NOT FOUND THEN
                RAISE e_insufficient_funds USING MESSAGE = 'Account not found: ' || p_new_rec.account_id;
            END IF;

            -- 递归验证交易链（可能深度递归）
            IF p_new_rec.parent_trade_id IS NOT NULL THEN
                v_validation_result := recursive_validate(p_new_rec.trade_id, 0);
                IF v_validation_result LIKE 'DEEP_CHAIN:%' THEN
                    log_audit_autonomous('Deep chain detected: ' || v_validation_result, 'WARN');
                END IF;
            END IF;

            -- 变异表敏感操作：一致性检查
            IF NOT check_cross_table_consistency(p_new_rec.account_id, 2) THEN
                -- 宽松模式失败，尝试严格模式
                IF NOT check_cross_table_consistency(p_new_rec.account_id, 3) THEN
                    RAISE e_stale_data USING MESSAGE = 'Consistency check failed for account ' || p_new_rec.account_id;
                END IF;
            END IF;

            -- 级联更新账户余额（可能再次触发触发器！）
            v_cascade_account := TRUE;
            GOTO cascade_account;

        EXCEPTION WHEN e_insufficient_funds THEN
            v_need_rollback := TRUE;
            GOTO error_handler;
        END;

        <<handle_update>>
        -- 更新交易：状态流转校验、金额变更审计
        BEGIN
            -- 状态机校验（硬编码状态流转矩阵）
            IF p_old_rec.status = 'PENDING' AND p_new_rec.status NOT IN ('SETTLED', 'CANCELLED', 'DISPUTED') THEN
                RAISE e_stale_data USING MESSAGE = 'Invalid status transition: ' || p_old_rec.status || '->' || p_new_rec.status;
            END IF;

            IF p_old_rec.status = 'SETTLED' AND p_new_rec.status != 'DISPUTED' THEN
                -- 已结算只能争议化
                RAISE e_stale_data USING MESSAGE = 'Settled trade can only be disputed';
            END IF;

            -- 金额变更检测
            IF ABS(p_old_rec.amount - p_new_rec.amount) > c_epsilon THEN
                -- 金额变了！需要重新计算费用并审计
                v_temp_trade_id := p_new_rec.trade_id;

                -- 动态重新计算费用（调用重载）
                p_new_rec.fee := calc_fee(p_new_rec.amount, p_new_rec.amount / NULLIF(p_old_rec.amount, 0), FALSE);
                -- 上面这行故意怪异：用金额比作为折扣率，可能除零

                -- 记录金额变更审计
                v_audit_msg := '{"trade_id":' || v_temp_trade_id ||
                               ',"old_amount":' || p_old_rec.amount ||
                               ',"new_amount":' || p_new_rec.amount || '}';
                log_audit_autonomous('Amount changed: ' || v_audit_msg, 'WARN');

                -- 标记需要级联
                v_cascade_account := TRUE;
            END IF;

            -- 如果状态变 SETTLED，做最终一致性检查
            IF p_new_rec.status = 'SETTLED' AND p_old_rec.status != 'SETTLED' THEN
                IF NOT check_cross_table_consistency(p_new_rec.account_id, 1) THEN
                    RAISE e_stale_data USING MESSAGE = 'Final consistency check failed';
                END IF;
            END IF;

            GOTO cascade_account;

        EXCEPTION WHEN e_stale_data THEN
            v_need_rollback := TRUE;
            GOTO error_handler;
        END;

        <<handle_delete>>
        -- 删除交易：级联清理、历史归档
        BEGIN
            -- 已结算交易不能直接删
            IF p_old_rec.status = 'SETTLED' THEN
                -- 改为逻辑删除（更新状态而非物理删除）
                o_skip_default := TRUE;  -- 告诉触发器跳过默认 DELETE，改为 UPDATE

                -- 这里我们手动做"软删除"（又可能触发 UPDATE 触发器！）
                EXECUTE IMMEDIATE 'UPDATE trade_record SET status = ''ARCHIVED'', deleted_at = CURRENT_TIMESTAMP WHERE trade_id = $1'
                USING p_old_rec.trade_id;

                log_audit_autonomous('Soft deleted settled trade: ' || p_old_rec.trade_id, 'INFO');

                -- 跳过级联，因为软删除不直接影响余额
                v_cascade_account := FALSE;
                GOTO cleanup;
            END IF;

            -- 未结算交易：允许物理删除，但需调整账户余额
            v_cascade_account := TRUE;
            GOTO cascade_account;
        END;

        <<cascade_account>>
        -- 级联更新账户（可能触发 account 表触发器，形成触发器链）
        IF v_cascade_account THEN
            handle_account_change(
                CASE WHEN p_op = 'DELETE' THEN 'UPDATE' ELSE p_op END,
                NULL,  -- old_rec 未知
                (SELECT ROW(account.*)::account FROM account WHERE account_id = COALESCE(p_new_rec.account_id, p_old_rec.account_id)),
                TRUE   -- 允许进一步级联
            );
        END IF;
        GOTO cleanup;

        <<unknown_op>>
        RAISE e_just_because USING MESSAGE = 'Unknown operation: ' || p_op;

        <<error_handler>>
        -- 统一错误处理（多个 GOTO 汇聚）
        log_audit_autonomous('Trade change error [' || p_trigger_name || ']: ' || SQLERRM, 'ERROR');
        g_trigger_depth := g_trigger_depth - 1;  -- 必须手动递减！
        RAISE;  -- 重抛

        <<cleanup>>
        -- 清理和收尾
        g_trigger_depth := g_trigger_depth - 1;
        g_audit_trail := g_audit_trail || CHR(10) || p_op || ':' || COALESCE(p_new_rec.trade_id::TEXT, p_old_rec.trade_id::TEXT);

    END;

    -- ============================================================
    -- 账户变化处理（被交易触发器级联调用，也可能被自身触发器调用）
    -- ============================================================
    PROCEDURE handle_account_change(
        p_op IN VARCHAR(10),
        p_old_rec IN account%ROWTYPE,
        p_new_rec IN account%ROWTYPE,
        p_cascade IN BOOLEAN DEFAULT TRUE
    ) IS
        v_delta NUMERIC;
        v_related_count INT;
    BEGIN
        -- 防止自我级联死循环
        IF g_trigger_depth > 3 AND p_cascade THEN
            log_audit_autonomous('Account cascade suppressed at depth ' || g_trigger_depth, 'WARN');
            RETURN;
        END IF;

        IF p_op = 'UPDATE' AND p_new_rec.account_id IS NOT NULL THEN
            -- 重新计算账户余额（聚合查询，可能很慢）
            SELECT COALESCE(SUM(amount - fee), 0) INTO v_delta
            FROM trade_record
            WHERE account_id = p_new_rec.account_id
              AND status IN ('SETTLED', 'PENDING');

            -- 如果余额变化显著，触发进一步处理
            IF ABS(v_delta - p_new_rec.balance) > 1.0 THEN
                -- 动态更新（又可能触发触发器！）
                EXECUTE IMMEDIATE 'UPDATE account SET balance = $1, last_recalc = CURRENT_TIMESTAMP WHERE account_id = $2'
                USING v_delta, p_new_rec.account_id;

                -- 检查是否需要通知（模拟）
                SELECT COUNT(*) INTO v_related_count
                FROM trade_record
                WHERE account_id = p_new_rec.account_id AND status = 'DISPUTED';

                IF v_related_count > 0 THEN
                    log_audit_autonomous('Account ' || p_new_rec.account_id || ' has ' || v_related_count || ' disputed trades', 'WARN');
                END IF;
            END IF;
        END IF;
    END;

    -- ============================================================
    -- DDL 事件处理（事件触发器回调）
    -- ============================================================
    PROCEDURE handle_ddl_event(
        p_tag IN VARCHAR(50),
        p_object_name IN VARCHAR(200),
        p_object_type IN VARCHAR(50),
        p_command IN TEXT
    ) IS
    BEGIN
        -- 记录 DDL 操作栈
        g_ddl_stack := g_ddl_stack || CHR(10) || p_tag || ':' || p_object_name;

        -- 拦截危险操作
        IF p_tag LIKE '%DROP%' AND p_object_name LIKE '%trade%' THEN
            RAISE e_ddl_intercept USING MESSAGE = 'DDL intercept: Cannot drop trade-related objects. Command: ' || p_command;
        END IF;

        -- 自动审计
        log_audit_autonomous('DDL executed: ' || p_tag || ' on ' || p_object_type || ' ' || p_object_name, 'INFO');

        -- 如果是创建表，自动添加审计列（模拟 schema 管理）
        IF p_tag = 'CREATE TABLE' AND p_object_name NOT LIKE 'pg_%' THEN
            BEGIN
                EXECUTE IMMEDIATE 'ALTER TABLE ' || p_object_name || ' ADD COLUMN IF NOT EXISTS created_by VARCHAR(50) DEFAULT CURRENT_USER';
                EXECUTE IMMEDIATE 'ALTER TABLE ' || p_object_name || ' ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP';
            EXCEPTION WHEN OTHERS THEN
                log_audit_autonomous('Auto-alter failed for ' || p_object_name || ': ' || SQLERRM, 'WARN');
            END;
        END IF;
    END;

    -- 原有 run_clearing 保留（略，与之前相同，但加入触发器深度检查）
    PROCEDURE run_clearing(p_batch_date IN DATE, p_parallel_degree IN INT DEFAULT 4, p_dry_run IN BOOLEAN DEFAULT FALSE, o_summary OUT CLOB, o_status OUT VARCHAR(50)) IS
    BEGIN
        -- 检查是否在触发器上下文中被调用
        IF g_trigger_depth > 0 THEN
            log_audit_autonomous('run_clearing called from trigger context at depth ' || g_trigger_depth, 'WARN');
        END IF;

        -- 原有实现...
        o_status := 'SUCCESS';
        o_summary := '{}';
    END;

    FUNCTION get_suspicious_trades(p_threshold NUMERIC) RETURN t_generic_cursor IS
        v_cur t_generic_cursor;
    BEGIN
        OPEN v_cur FOR
            SELECT t.*, a.account_name,
                   (SELECT COUNT(*) FROM trade_record t2 WHERE t2.account_id = t.account_id AND t2.trade_date > t.trade_date - INTERVAL '30 days') as recent_count
            FROM trade_record t
            JOIN account a ON t.account_id = a.account_id
            WHERE t.amount > p_threshold AND t.status IN ('PENDING', 'DISPUTED')
              AND EXISTS (SELECT 1 FROM audit_log al WHERE al.message LIKE '%' || t.trade_id || '%' AND al.severity = 'WARN' AND al.log_time > CURRENT_TIMESTAMP - INTERVAL '7 days')
            ORDER BY t.amount DESC;
        RETURN v_cur;
    END;

END complex_clearing_pkg;
/

-- ============================================================
-- 触发器定义：行级 + 语句级 + DDL 事件触发器
-- ============================================================

-- 1. 交易表行级触发器（最复杂：BEFORE + AFTER + 多事件）
-- CREATE OR REPLACE FUNCTION trg_trade_row_level() RETURNS TRIGGER AS $$
-- DECLARE
--     v_skip_default BOOLEAN := FALSE;
--     v_old_rec trade_record%ROWTYPE;
--     v_new_rec trade_record%ROWTYPE;
-- BEGIN
--     IF TG_OP = 'DELETE' THEN
--         v_old_rec := OLD;
--         v_new_rec := NULL;
--     ELSIF TG_OP = 'INSERT' THEN
--         v_old_rec := NULL;
--         v_new_rec := NEW;
--     ELSE
--         v_old_rec := OLD;
--         v_new_rec := NEW;
--     END IF;

--     -- BEFORE 事件：做校验和修改
--     IF TG_WHEN = 'BEFORE' THEN
--         complex_clearing_pkg.handle_trade_change(TG_OP, v_old_rec, v_new_rec, TG_NAME, v_skip_default);

--         IF v_skip_default AND TG_OP = 'DELETE' THEN
--             -- 跳过默认 DELETE，改为逻辑删除已在包内完成
--             RETURN NULL;  -- 阻止原始 DELETE
--         END IF;

--         -- 允许修改 NEW（BEFORE 触发器特权）
--         IF TG_OP IN ('INSERT', 'UPDATE') THEN
--             NEW.processed_at := CURRENT_TIMESTAMP;
--             NEW.fee := COALESCE(NEW.fee, complex_clearing_pkg.calc_fee(NEW.amount));
--             RETURN NEW;
--         END IF;

--         RETURN OLD;
--     END IF;

--     -- AFTER 事件：级联和审计
--     IF TG_WHEN = 'AFTER' THEN
--         complex_clearing_pkg.handle_trade_change(TG_OP, v_old_rec, v_new_rec, TG_NAME, v_skip_default);

--         -- AFTER 中再做一次一致性检查（双重检查）
--         IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.account_id IS NOT NULL THEN
--             IF NOT complex_clearing_pkg.check_cross_table_consistency(NEW.account_id, 3) THEN
--                 RAISE WARNING 'Post-operation consistency check failed for account %', NEW.account_id;
--             END IF;
--         END IF;

--         RETURN NULL;  -- AFTER 触发器忽略返回值
--     END IF;

--     RETURN NULL;
-- END;
-- $$ LANGUAGE plpgsql;

-- 绑定多个触发器实例（故意拆分，增加复杂度）
-- CREATE TRIGGER trg_trade_before BEFORE INSERT OR UPDATE OR DELETE ON trade_record
--     FOR EACH ROW EXECUTE FUNCTION trg_trade_row_level();

-- CREATE TRIGGER trg_trade_after AFTER INSERT OR UPDATE OR DELETE ON trade_record
--     FOR EACH ROW EXECUTE FUNCTION trg_trade_row_level();

-- 2. 交易表语句级触发器（批量操作审计）
-- CREATE OR REPLACE FUNCTION trg_trade_statement() RETURNS TRIGGER AS $$
-- DECLARE
--     v_count INT;
--     v_total NUMERIC;
-- BEGIN
--     IF TG_OP = 'INSERT' THEN
--         SELECT COUNT(*), COALESCE(SUM(amount), 0) INTO v_count, v_total FROM trade_record WHERE trade_date = CURRENT_DATE;
--         complex_clearing_pkg.log_audit_autonomous('Daily insert summary: count=' || v_count || ' total=' || v_total, 'INFO');
--     END IF;
--     RETURN NULL;
-- END;
-- $$ LANGUAGE plpgsql;

-- CREATE TRIGGER trg_trade_stmt_after AFTER INSERT ON trade_record
--     FOR EACH STATEMENT EXECUTE FUNCTION trg_trade_statement();

-- 3. 账户表触发器（被交易触发器级联调用）
-- CREATE OR REPLACE FUNCTION trg_account_change() RETURNS TRIGGER AS $$
-- BEGIN
--     IF TG_OP = 'UPDATE' THEN
--         -- 余额变化时，触发交易重算（可能又触发交易触发器！）
--         IF ABS(COALESCE(OLD.balance, 0) - COALESCE(NEW.balance, 0)) > 1.0 THEN
--             UPDATE trade_record
--             SET fee = complex_clearing_pkg.calc_fee(amount)
--             WHERE account_id = NEW.account_id
--               AND status = 'PENDING'
--               AND created_at > CURRENT_TIMESTAMP - INTERVAL '1 day';
--             -- 这行 UPDATE 可能触发 trg_trade_before/after！
--         END IF;
--     END IF;
--     RETURN NULL;
-- END;
-- $$ LANGUAGE plpgsql;

-- CREATE TRIGGER trg_account_after AFTER UPDATE OF balance ON account
--     FOR EACH ROW EXECUTE FUNCTION trg_account_change();

-- 4. DDL 事件触发器（数据库级）
-- CREATE OR REPLACE FUNCTION trg_ddl_intercept() RETURNS EVENT_TRIGGER AS $$
-- DECLARE
--     v_obj RECORD;
-- BEGIN
--     FOR v_obj IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
--         complex_clearing_pkg.handle_ddl_event(
--             TG_TAG,
--             v_obj.object_name,
--             v_obj.object_type,
--             v_obj.command_tag
--         );
--     END LOOP;
-- EXCEPTION WHEN OTHERS THEN
--     -- DDL 触发器错误不能阻止操作，只能记录
--     INSERT INTO audit_log (severity, message) VALUES ('ERROR', 'DDL trigger failed: ' || SQLERRM);
-- END;
-- $$ LANGUAGE plpgsql;

-- CREATE EVENT TRIGGER trg_ddl_audit ON ddl_command_end
--     EXECUTE FUNCTION trg_ddl_intercept();

-- 5. 登录/注销触发器（会话级状态初始化）
-- CREATE OR REPLACE FUNCTION trg_session_init() RETURNS EVENT_TRIGGER AS $$
-- BEGIN
--     -- 重置包级状态（每个会话）
--     complex_clearing_pkg.g_trigger_depth := 0;
--     complex_clearing_pkg.g_mutating_table_guard := FALSE;
--     complex_clearing_pkg.g_audit_trail := '';
--     complex_clearing_pkg.g_session_counter := 0;
-- END;
-- $$ LANGUAGE plpgsql;

-- 注意：GaussDB 可能不支持标准登录触发器，用连接池初始化模拟
-- 实际项目中可能在应用层调用初始化过程

-- ============================================================
-- 测试用例：触发器联动效果
-- ============================================================

-- 测试 1：插入交易（触发链：trade_before -> trade_after -> account_after -> trade_before...）
INSERT INTO trade_record (account_id, amount, status, trade_date, parent_trade_id)
VALUES (1, 50000, 'PENDING', CURRENT_DATE, NULL);

-- 测试 2：更新交易状态（状态机校验 + 级联）
UPDATE trade_record SET status = 'SETTLED' WHERE trade_id = 1;

-- 测试 3：尝试删除已结算交易（应被转为软删除）
DELETE FROM trade_record WHERE trade_id = 1 AND status = 'SETTLED';

-- 测试 4：批量插入（语句级触发器）
INSERT INTO trade_record (account_id, amount, status, trade_date)
SELECT (random()*99+1)::BIGINT, (random()*100000)::NUMERIC, 'PENDING', CURRENT_DATE
FROM generate_series(1, 100);

-- 测试 5：DDL 拦截测试（应被审计或阻止）
-- CREATE TABLE trade_backup (LIKE trade_record);  -- 会被自动加审计列
-- DROP TABLE trade_record;  -- 会被拦截（如果事件触发器生效）

-- 查询审计日志观察触发器调用链
SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 20;
