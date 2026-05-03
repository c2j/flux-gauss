-- ======================================================================
-- pkg_mapper_param_test — Mapper 参数方案验证
-- 覆盖: 简单参数、多参数 INSERT、%ROWTYPE 字段、自定义 TYPE 字段、
--        RECORD 字段、局部变量字段、EXECUTE IMMEDIATE USING、表达式值
-- ======================================================================
-- 验证点标记:
--   [P0] 扁平参数方案必须通过
--   [DTO] DTO 方案必须通过
--   [TYPE] 类型推断验证点
-- ======================================================================


-- ──────────────────────────────────────────────────────────────────────
-- 0. 自定义 TYPE（用于复合类型字段推断测试）
-- ──────────────────────────────────────────────────────────────────────
CREATE OR REPLACE TYPE order_detail AS (
    customer_id  BIGINT,
    product_id   BIGINT,
    item_count   NUMERIC,
    unit_price   NUMERIC
);


-- ======================================================================
-- 场景 1: 简单参数 SELECT（基线 — 两种方案应产生相同结果）
-- 预期: #{pOrderId, jdbcType=BIGINT, javaType=Long}
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.simple_select(
    p_order_id IN BIGINT
) AS $$
DECLARE
    v_status VARCHAR(20);
    v_amount NUMERIC(18,4);
BEGIN
    -- [P0] 单参数 SELECT INTO
    SELECT order_status, total_amount
    INTO v_status, v_amount
    FROM t_mapper_order
    WHERE order_id = p_order_id;
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 2: 少参数 INSERT（2 个参数 + 局部变量 — 两种方案均可）
-- 预期: #{pCustomerId, jdbcType=BIGINT}, #{pProductId, jdbcType=BIGINT}
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.simple_insert(
    p_customer_id IN BIGINT,
    p_product_id  IN BIGINT
) AS $$
DECLARE
    v_qty INT := 1;
BEGIN
    -- [P0][DTO] INSERT 用局部变量 + 参数
    INSERT INTO t_mapper_order_item (order_id, line_no, product_name, qty, price)
    VALUES (p_customer_id, 1, 'test', v_qty, p_product_id);
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 3: 多参数 INSERT（>5 参数 — DTO 方案的优势场景）
-- 模拟一个完整的订单创建: 8 个入参全用在 INSERT 中
-- 预期扁平: 8 个 @Param
-- 预期 DTO: parameterType="...SimpleOrderParams"
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.create_order(
    p_customer_id IN BIGINT,
    p_product_id  IN BIGINT,
    p_quantity    IN INT,
    p_unit_price  IN NUMERIC,
    p_discount    IN NUMERIC,
    p_remark      IN VARCHAR,
    p_created_by  IN VARCHAR,
    p_status      IN VARCHAR
) AS $$
DECLARE
    v_total NUMERIC(18,4);
BEGIN
    v_total := p_quantity * p_unit_price;

    -- [P0][DTO] 8 个参数全部出现在 INSERT 中
    INSERT INTO t_mapper_order (
        customer_id, product_id, quantity, unit_price,
        discount, total_amount, order_status, remark, created_by
    ) VALUES (
        p_customer_id, p_product_id, p_quantity, p_unit_price,
        COALESCE(p_discount, 0), v_total, p_status, p_remark, p_created_by
    );
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 4: %ROWTYPE 参数 + 字段访问（触发器/回调典型模式）
-- 预期: #{pNewOrder_customerId, jdbcType=BIGINT, javaType=Long} 等
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.process_order_change(
    p_old_rec IN t_mapper_order%ROWTYPE,
    p_new_rec IN t_mapper_order%ROWTYPE,
    p_operator IN VARCHAR
) AS $$
DECLARE
    v_diff NUMERIC(18,4);
BEGIN
    -- [P0][DTO][TYPE] 用 p_new_rec 的多个字段做 WHERE 条件
    UPDATE t_mapper_order
    SET order_status = 'PROCESSING',
        updated_at = CURRENT_TIMESTAMP
    WHERE customer_id = p_new_rec.customer_id
      AND product_id = p_new_rec.product_id;

    -- [P0][DTO][TYPE] 用新旧记录字段做金额校验
    SELECT COALESCE(SUM(total_amount), 0)
    INTO v_diff
    FROM t_mapper_order
    WHERE customer_id = p_new_rec.customer_id
      AND order_status IN ('NEW', 'PROCESSING');

    -- [P0][DTO][TYPE] INSERT 中混合使用 p_new_rec 字段和 p_operator
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        p_new_rec.order_id,
        p_operator,
        'AUTO_REVIEW',
        'Amount changed from ' || p_old_rec.total_amount || ' to ' || p_new_rec.total_amount
    );
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 5: 自定义 TYPE 变量 + 字段访问
-- v_detail 是 order_detail 类型，字段类型可从 TYPE 定义推断
-- 预期: #{vDetail_customerId, jdbcType=BIGINT, javaType=Long} 等
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.create_from_detail(
    p_order_id IN BIGINT,
    p_line_no  IN INT
) AS $$
DECLARE
    v_detail order_detail;
BEGIN
    SELECT customer_id, product_id, quantity, unit_price
    INTO v_detail.customer_id, v_detail.product_id,
         v_detail.item_count, v_detail.unit_price
    FROM t_mapper_order
    WHERE order_id = p_order_id;

    -- [P0][DTO][TYPE] INSERT 用 TYPE 变量的所有字段
    INSERT INTO t_mapper_order_item (
        order_id, line_no, product_name, qty, price, line_amount
    ) VALUES (
        p_order_id,
        p_line_no,
        'detail_item',
        v_detail.item_count,
        v_detail.unit_price,
        v_detail.item_count * v_detail.unit_price
    );

    -- [P0][DTO][TYPE] UPDATE 用 TYPE 字段做 WHERE
    UPDATE t_mapper_order
    SET total_amount = v_detail.item_count * v_detail.unit_price
    WHERE customer_id = v_detail.customer_id
      AND product_id = v_detail.product_id;
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 6: RECORD 类型 FOR 循环 + 字段访问（RECORD 字段类型推断）
-- v_rec 来自 SELECT，字段类型需要从 SELECT 列推断
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.batch_approve(
    p_approver IN VARCHAR,
    p_min_amount IN NUMERIC
) AS $$
DECLARE
    v_count INT := 0;
BEGIN
    -- [P0][DTO] FOR 循环中的 v_rec 是 RECORD，字段来自 SELECT
    FOR v_rec IN
        SELECT order_id, customer_id, total_amount, order_status
        FROM t_mapper_order
        WHERE total_amount > p_min_amount
          AND order_status = 'NEW'
        ORDER BY order_id
    LOOP
        -- INSERT 用 v_rec 字段 + p_approver
        INSERT INTO t_mapper_approval (order_id, approver, action, reason)
        VALUES (v_rec.order_id, p_approver, 'BATCH_APPROVE',
                'Auto approved, amount=' || v_rec.total_amount);

        -- UPDATE 用 v_rec 字段做 WHERE
        UPDATE t_mapper_order
        SET order_status = 'APPROVED', updated_at = CURRENT_TIMESTAMP
        WHERE order_id = v_rec.order_id;

        v_count := v_count + 1;
    END LOOP;

    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (0, p_approver, 'BATCH_SUMMARY', 'Approved ' || v_count || ' orders');
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 7: EXECUTE IMMEDIATE USING（$1/$2 位置参数）
-- 验证: $1 → #{param1, jdbcType=X, javaType=Y}
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.dynamic_update(
    p_order_id    IN BIGINT,
    p_new_status  IN VARCHAR,
    p_operator    IN VARCHAR
) AS $$
BEGIN
    -- [P0] EXECUTE IMMEDIATE + USING: $1/$2 需转为 #{param1}/#{param2}
    EXECUTE IMMEDIATE
        'UPDATE t_mapper_order SET order_status = $1, updated_at = CURRENT_TIMESTAMP WHERE order_id = $2'
    USING p_new_status, p_order_id;

    -- [P0] USING 中使用参数（非位置参数场景已在其他 case 覆盖）
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (p_order_id, p_operator, 'STATUS_CHANGE', 'Changed to ' || p_new_status);
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 8: OUT 参数 + INOUT 参数
-- 验证: OUT 参数不出现在 mapper 方法中，INOUT 的 IN 部分出现
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.calc_order_summary(
    p_customer_id IN BIGINT,
    p_order_count INOUT INT,
    p_total_amount OUT NUMERIC
) AS $$
BEGIN
    -- [P0] 只有 p_customer_id 是纯 IN 参数出现在 DML 中
    SELECT COUNT(*), COALESCE(SUM(total_amount), 0)
    INTO p_order_count, p_total_amount
    FROM t_mapper_order
    WHERE customer_id = p_customer_id;

    -- p_order_count 是 INOUT，在后续逻辑中可读可写
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (0, 'SYSTEM', 'SUMMARY', 'Customer ' || p_customer_id || ' has ' || p_order_count || ' orders');
END;
$$ LANGUAGE plpgsql;


-- ======================================================================
-- 场景 9: 同一 procedure 内多 DML 使用不同的局部变量字段组合
-- 这是 DTO 方案的核心优势场景: 一个 DTO 覆盖所有 DML
-- ======================================================================
CREATE OR REPLACE PROCEDURE pkg_mapper_param_test.comprehensive_workflow(
    p_order_id   IN BIGINT,
    p_approver   IN VARCHAR,
    p_action     IN VARCHAR
) AS $$
DECLARE
    v_order     t_mapper_order%ROWTYPE;
    v_item      t_mapper_order_item%ROWTYPE;
    v_approved  BOOLEAN := FALSE;
BEGIN
    -- 获取订单信息 → 填充 v_order
    SELECT *
    INTO v_order
    FROM t_mapper_order
    WHERE order_id = p_order_id;

    -- [DTO] DML-A: 只用 v_order 的 3 个字段
    UPDATE t_mapper_order
    SET order_status = 'REVIEWING'
    WHERE customer_id = v_order.customer_id
      AND product_id = v_order.product_id
      AND order_status = v_order.order_status;

    -- [DTO] DML-B: 用 v_order 的 5 个字段 + p_approver + p_action
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        v_order.order_id,
        p_approver,
        p_action,
        'Order amount=' || v_order.total_amount || ' status=' || v_order.order_status
    );

    -- 获取明细 → 填充 v_item
    SELECT *
    INTO v_item
    FROM t_mapper_order_item
    WHERE order_id = p_order_id
    LIMIT 1;

    -- [DTO] DML-C: 用 v_item 的 3 个字段
    UPDATE t_mapper_order_item
    SET line_amount = v_item.qty * v_item.price
    WHERE order_id = v_item.order_id AND line_no = v_item.line_no;

    -- [DTO] DML-D: 混合使用 v_order + v_item + 参数
    INSERT INTO t_mapper_approval (order_id, approver, action, reason)
    VALUES (
        v_order.order_id,
        p_approver,
        'ITEM_REVIEW',
        'Item total=' || v_item.line_amount || ' for product=' || v_item.product_name
    );

    -- [DTO] DML-E: DELETE 用 v_order 字段
    DELETE FROM t_mapper_order_item
    WHERE order_id = v_order.order_id
      AND line_amount < 0;
END;
$$ LANGUAGE plpgsql;
