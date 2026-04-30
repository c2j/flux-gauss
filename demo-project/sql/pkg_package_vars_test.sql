-- =====================================================================
-- 包级变量测试用例
-- 验证 GaussDB PACKAGE BODY 中的包级变量能被正确解析和转换
-- 包级变量应转译为 Java Service 类的 static 字段
-- =====================================================================

CREATE OR REPLACE PACKAGE BODY pkg_package_vars_test AS

-- 包级变量：各种类型 + 默认值
v_status       VARCHAR := 'ACTIVE';
v_counter      INTEGER := 0;
v_max_amount   NUMERIC := 99999.99;
v_threshold    INTEGER := 100;
v_app_name     VARCHAR := 'FluxGaussTest';

-- 存储过程1：读取并使用包级变量
PROCEDURE prc_check_status(p_order_id BIGINT) IS
    v_current VARCHAR;
BEGIN
    v_current := v_status;
    IF v_current = 'ACTIVE' THEN
        UPDATE t_orders SET status = 'PROCESSING' WHERE id = p_order_id;
    ELSE
        UPDATE t_orders SET status = 'CANCELLED' WHERE id = p_order_id;
    END IF;
    INSERT INTO t_log(id, msg) VALUES(1, 'status=' || v_current);
END;

-- 存储过程2：使用包级变量作为阈值
PROCEDURE prc_check_amount(p_amount NUMERIC) IS
BEGIN
    IF p_amount > v_max_amount THEN
        INSERT INTO t_alerts(order_id, alert_type, message)
        VALUES(0, 'AMOUNT_EXCEED', 'Amount exceeds max: ' || v_max_amount);
    ELSE
        INSERT INTO t_log(id, msg) VALUES(2, 'Amount OK: ' || p_amount);
    END IF;
END;

-- 存储过程3：使用多个包级变量
PROCEDURE prc_batch_process(p_batch_size INTEGER) IS
    v_count INTEGER := 0;
BEGIN
    v_count := v_counter + p_batch_size;
    IF v_count > v_threshold THEN
        INSERT INTO t_log(id, msg) VALUES(3, 'Batch exceeds threshold: ' || v_threshold);
    ELSE
        INSERT INTO t_log(id, msg) VALUES(3, 'App=' || v_app_name || ' processed ' || v_count);
    END IF;
END;

END pkg_package_vars_test;
