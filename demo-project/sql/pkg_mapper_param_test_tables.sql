-- ======================================================================
-- Mapper 参数方案验证测试表
-- 用于验证 #{param, jdbcType=X, javaType=Y} 增强和 DTO parameterType 方案
-- ======================================================================

-- 订单主表（多列，用于 INSERT 多参数场景）
CREATE TABLE IF NOT EXISTS t_mapper_order (
    order_id    BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    product_id  BIGINT NOT NULL,
    quantity     INT NOT NULL,
    unit_price   NUMERIC(18,4) NOT NULL,
    discount    NUMERIC(18,4) DEFAULT 0,
    total_amount NUMERIC(18,4),
    order_status VARCHAR(20) DEFAULT 'NEW',
    remark      VARCHAR(500),
    created_by  VARCHAR(50),
    updated_at  TIMESTAMP
);

-- 订单明细表（外键关联）
CREATE TABLE IF NOT EXISTS t_mapper_order_item (
    item_id     BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL,
    line_no     INT NOT NULL,
    product_name VARCHAR(200),
    qty         INT NOT NULL,
    unit_price   NUMERIC(18,4) NOT NULL,
    line_amount NUMERIC(18,4)
);

-- 审批记录表
CREATE TABLE IF NOT EXISTS t_mapper_approval (
    approval_id BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL,
    approver    VARCHAR(50) NOT NULL,
    action      VARCHAR(20) NOT NULL,
    reason      VARCHAR(500),
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
