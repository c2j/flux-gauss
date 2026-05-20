
-- ============================================================
-- 高斯/OpenGauss 复杂 MERGE INTO 存储过程示例
--
-- 场景：企业级数据仓库 ETL 流程
-- 涉及：多源合并、增量更新、历史追踪、冲突解决、审计日志
-- ============================================================

-- ============================================
-- 第一部分：DDL 建表语句
-- ============================================

-- 1. 源系统数据表（模拟上游系统推送）
DROP TABLE IF EXISTS src_sales_data CASCADE;
CREATE TABLE src_sales_data (
    src_batch_id        VARCHAR2(50),           -- 批次号
    src_sequence        INTEGER,                -- 批次内序号
    transaction_id      VARCHAR2(50) NOT NULL,  -- 业务主键
    product_code        VARCHAR2(50),
    customer_code       VARCHAR2(50),
    sales_amount        NUMERIC(18,2),
    sales_quantity      INTEGER,
    sales_date          DATE,
    region_code         VARCHAR2(20),
    channel_type        VARCHAR2(20),           -- ONLINE/OFFLINE/AGENCY
    sales_rep_code      VARCHAR2(50),
    discount_rate       NUMERIC(5,2),
    payment_method      VARCHAR2(20),
    delivery_status     VARCHAR2(20),           -- PENDING/SHIPPED/DELIVERED/RETURNED
    data_source         VARCHAR2(20),           -- CRM/ERP/POS/WEB
    record_hash         VARCHAR2(64),           -- MD5哈希，用于快速比对变更
    src_create_time     TIMESTAMP,
    src_update_time     TIMESTAMP,
    is_deleted          INTEGER DEFAULT 0,     -- 软删除标记

    PRIMARY KEY (src_batch_id, src_sequence)
);

-- 2. 目标事实表（数据仓库主表）
DROP TABLE IF EXISTS dw_sales_fact CASCADE;
CREATE TABLE dw_sales_fact (
    sk_sales_id         INTEGER PRIMARY KEY,    -- 代理键
    bk_transaction_id   VARCHAR2(50) NOT NULL,  -- 业务主键

    -- 维度外键
    sk_product          INTEGER,
    sk_customer         INTEGER,
    sk_region           INTEGER,
    sk_sales_rep        INTEGER,
    sk_date             INTEGER,

    -- 度量值
    sales_amount        NUMERIC(18,2),
    sales_quantity      INTEGER,
    unit_price          NUMERIC(18,2),
    discount_amount     NUMERIC(18,2),
    net_amount          NUMERIC(18,2),

    -- 维度属性（冗余存储，避免频繁JOIN）
    region_code         VARCHAR2(20),
    channel_type        VARCHAR2(20),
    payment_method      VARCHAR2(20),
    delivery_status     VARCHAR2(20),

    -- 数据血缘
    data_source         VARCHAR2(20),
    src_batch_id        VARCHAR2(50),
    src_sequence        INTEGER,
    record_hash         VARCHAR2(64),

    -- SCD2 历史追踪字段
    effective_date      DATE,                   -- 生效日期
    expiry_date         DATE,                   -- 失效日期（9999-12-31表示当前有效）
    is_current          INTEGER DEFAULT 1,      -- 1=当前有效，0=历史记录
    version_number      INTEGER DEFAULT 1,      -- 版本号

    -- 审计字段
    dw_create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dw_update_time      TIMESTAMP,
    dw_created_by       VARCHAR2(50) DEFAULT CURRENT_USER,
    dw_updated_by       VARCHAR2(50),

    -- 约束
    CONSTRAINT uq_dw_sales_current UNIQUE (bk_transaction_id, is_current)
);

-- 3. 维度表：产品
DROP TABLE IF EXISTS dim_product CASCADE;
CREATE TABLE dim_product (
    sk_product      INTEGER PRIMARY KEY,
    bk_product_code VARCHAR2(50) NOT NULL,
    product_name    VARCHAR2(200),
    category_code   VARCHAR2(20),
    brand_code      VARCHAR2(20),
    unit_cost       NUMERIC(18,2),
    is_active       INTEGER DEFAULT 1,
    UNIQUE (bk_product_code, is_active)
);

-- 4. 维度表：客户
DROP TABLE IF EXISTS dim_customer CASCADE;
CREATE TABLE dim_customer (
    sk_customer      INTEGER PRIMARY KEY,
    bk_customer_code VARCHAR2(50) NOT NULL,
    customer_name    VARCHAR2(200),
    customer_type    VARCHAR2(20),  -- ENTERPRISE/SMB/INDIVIDUAL
    region_code      VARCHAR2(20),
    credit_level     VARCHAR2(10), -- A/B/C/D
    is_active        INTEGER DEFAULT 1,
    UNIQUE (bk_customer_code, is_active)
);

-- 5. 维度表：地区
DROP TABLE IF EXISTS dim_region CASCADE;
CREATE TABLE dim_region (
    sk_region     INTEGER PRIMARY KEY,
    bk_region_code VARCHAR2(20) NOT NULL,
    region_name   VARCHAR2(100),
    parent_region VARCHAR2(20),
    region_level  INTEGER,
    is_active     INTEGER DEFAULT 1
);

-- 6. 维度表：销售代表
DROP TABLE IF EXISTS dim_sales_rep CASCADE;
CREATE TABLE dim_sales_rep (
    sk_sales_rep      INTEGER PRIMARY KEY,
    bk_sales_rep_code VARCHAR2(50) NOT NULL,
    rep_name          VARCHAR2(100),
    team_code         VARCHAR2(20),
    hire_date         DATE,
    is_active         INTEGER DEFAULT 1,
    UNIQUE (bk_sales_rep_code, is_active)
);

-- 7. 日期维度表
DROP TABLE IF EXISTS dim_date CASCADE;
CREATE TABLE dim_date (
    sk_date        INTEGER PRIMARY KEY,
    full_date      DATE NOT NULL,
    year_number    INTEGER,
    quarter_number INTEGER,
    month_number   INTEGER,
    day_number     INTEGER,
    weekday_name   VARCHAR2(10),
    is_workday     INTEGER DEFAULT 1
);

-- 8. 合并冲突解决日志表
DROP TABLE IF EXISTS merge_conflict_log CASCADE;
CREATE TABLE merge_conflict_log (
    log_id          INTEGER PRIMARY KEY,
    merge_batch_id  VARCHAR2(50),
    transaction_id  VARCHAR2(50),
    conflict_type   VARCHAR2(50),   -- DUPLICATE_KEY/HASH_MISMATCH/SCD_CHANGE/DELETE_REVIVED
    resolution      VARCHAR2(50),   -- UPDATE/INSERT/IGNORE/REJECT
    old_hash        VARCHAR2(64),
    new_hash        VARCHAR2(64),
    old_amount      NUMERIC(18,2),
    new_amount      NUMERIC(18,2),
    resolution_note VARCHAR2(500),
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. 合并审计表
DROP TABLE IF EXISTS merge_audit CASCADE;
CREATE TABLE merge_audit (
    audit_id        INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    procedure_name  VARCHAR2(200),
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    status          VARCHAR2(20),
    src_rows        INTEGER,
    inserted_rows   INTEGER,
    updated_rows    INTEGER,
    deleted_rows    INTEGER,
    unchanged_rows  INTEGER,
    conflict_rows   INTEGER,
    error_message   VARCHAR2(1000)
);

-- 10. 拒绝数据表（不符合规则的数据）
DROP TABLE IF EXISTS reject_data CASCADE;
CREATE TABLE reject_data (
    reject_id       INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    transaction_id  VARCHAR2(50),
    reject_reason   VARCHAR2(200),
    reject_detail   VARCHAR2(1000),
    raw_data        VARCHAR2(4000),
    reject_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 11. 增量变更追踪表（CDC用）
DROP TABLE IF EXISTS cdc_change_log CASCADE;
CREATE TABLE cdc_change_log (
    change_id       INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    transaction_id  VARCHAR2(50),
    change_type     VARCHAR2(10),   -- INSERT/UPDATE/DELETE
    change_time     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    old_hash        VARCHAR2(64),
    new_hash        VARCHAR2(64)
);

-- ============================================
-- 第二部分：序列
-- ============================================

DROP SEQUENCE IF EXISTS seq_dw_sales;
CREATE SEQUENCE seq_dw_sales START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_conflict_log;
CREATE SEQUENCE seq_conflict_log START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_merge_audit;
CREATE SEQUENCE seq_merge_audit START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_reject;
CREATE SEQUENCE seq_reject START WITH 1 INCREMENT BY 1;

DROP SEQUENCE IF EXISTS seq_cdc;
CREATE SEQUENCE seq_cdc START WITH 1 INCREMENT BY 1;

-- ============================================
-- 第三部分：测试数据
-- ============================================

-- 维度数据
INSERT INTO dim_product (sk_product, bk_product_code, product_name, category_code, brand_code, unit_cost) VALUES
(1, 'P001', '企业级服务器A型', 'SERVER', 'BRAND_A', 25000),
(2, 'P002', '云存储解决方案', 'STORAGE', 'BRAND_B', 8000),
(3, 'P003', 'AI推理加速卡', 'AI_CHIP', 'BRAND_C', 45000),
(4, 'P004', '数据库中间件', 'SOFTWARE', 'BRAND_A', 15000),
(5, 'P005', '网络安全网关', 'SECURITY', 'BRAND_D', 12000);

INSERT INTO dim_customer (sk_customer, bk_customer_code, customer_name, customer_type, region_code, credit_level) VALUES
(1, 'C001', '华夏科技集团', 'ENTERPRISE', 'EAST', 'A'),
(2, 'C002', '创新软件公司', 'SMB', 'NORTH', 'B'),
(3, 'C003', '个人用户张三', 'INDIVIDUAL', 'SOUTH', 'C'),
(4, 'C004', '云端数据服务', 'ENTERPRISE', 'EAST', 'A'),
(5, 'C005', '小微工作室', 'SMB', 'WEST', 'D');

INSERT INTO dim_region (sk_region, bk_region_code, region_name, parent_region, region_level) VALUES
(1, 'EAST', '华东地区', 'CHINA', 1),
(2, 'NORTH', '华北地区', 'CHINA', 1),
(3, 'SOUTH', '华南地区', 'CHINA', 1),
(4, 'WEST', '西部地区', 'CHINA', 1);

INSERT INTO dim_sales_rep (sk_sales_rep, bk_sales_rep_code, rep_name, team_code, hire_date) VALUES
(1, 'R001', '李明', 'TEAM_A', '2019-03-15'),
(2, 'R002', '王芳', 'TEAM_B', '2020-06-20'),
(3, 'R003', '张伟', 'TEAM_A', '2018-11-01'),
(4, 'R004', '刘洋', 'TEAM_C', '2021-01-10'),
(5, 'R005', '陈静', 'TEAM_B', '2022-08-15');

INSERT INTO dim_date (sk_date, full_date, year_number, quarter_number, month_number, day_number, weekday_name) VALUES
(20240101, '2024-01-01', 2024, 1, 1, 1, 'Monday'),
(20240115, '2024-01-15', 2024, 1, 1, 15, 'Monday'),
(20240201, '2024-02-01', 2024, 1, 2, 1, 'Thursday'),
(20240220, '2024-02-20', 2024, 1, 2, 20, 'Tuesday'),
(20240310, '2024-03-10', 2024, 1, 3, 10, 'Sunday'),
(20240325, '2024-03-25', 2024, 1, 3, 25, 'Monday'),
(20240405, '2024-04-05', 2024, 2, 4, 5, 'Friday'),
(20240418, '2024-04-18', 2024, 2, 4, 18, 'Thursday'),
(20240501, '2024-05-01', 2024, 2, 5, 1, 'Wednesday'),
(20240512, '2024-05-12', 2024, 2, 5, 12, 'Sunday'),
(20240520, '2024-05-20', 2024, 2, 5, 20, 'Monday'),
(20240601, '2024-06-01', 2024, 2, 6, 1, 'Saturday');

-- 第一批源数据（初始加载）
INSERT INTO src_sales_data (src_batch_id, src_sequence, transaction_id, product_code, customer_code,
    sales_amount, sales_quantity, sales_date, region_code, channel_type, sales_rep_code,
    discount_rate, payment_method, delivery_status, data_source, record_hash, src_create_time) VALUES
('BATCH_202401', 1, 'TXN_001', 'P001', 'C001', 250000, 10, '2024-01-15', 'EAST', 'ONLINE', 'R001', 0.10, 'BANK_TRANSFER', 'DELIVERED', 'CRM', 'hash001', SYSTIMESTAMP),
('BATCH_202401', 2, 'TXN_002', 'P002', 'C002', 80000, 10, '2024-01-15', 'NORTH', 'OFFLINE', 'R002', 0.05, 'CASH', 'SHIPPED', 'POS', 'hash002', SYSTIMESTAMP),
('BATCH_202401', 3, 'TXN_003', 'P003', 'C001', 450000, 10, '2024-02-20', 'EAST', 'ONLINE', 'R001', 0.15, 'BANK_TRANSFER', 'PENDING', 'WEB', 'hash003', SYSTIMESTAMP),
('BATCH_202401', 4, 'TXN_004', 'P004', 'C004', 150000, 10, '2024-03-10', 'EAST', 'AGENCY', 'R003', 0.08, 'CREDIT_CARD', 'DELIVERED', 'ERP', 'hash004', SYSTIMESTAMP),
('BATCH_202401', 5, 'TXN_005', 'P005', 'C003', 12000, 1, '2024-03-25', 'SOUTH', 'ONLINE', 'R004', 0.00, 'ALIPAY', 'SHIPPED', 'WEB', 'hash005', SYSTIMESTAMP);

COMMIT;

-- ============================================
-- 第四部分：复杂 MERGE INTO 存储过程包
-- ============================================

CREATE OR REPLACE PACKAGE pkg_merge_sales AS
    -- 类型定义
    TYPE rec_dimension_keys IS RECORD (
        sk_product      INTEGER,
        sk_customer     INTEGER,
        sk_region       INTEGER,
        sk_sales_rep    INTEGER,
        sk_date         INTEGER
    );

    -- 主过程：执行完整的MERGE INTO ETL流程
    PROCEDURE proc_merge_sales_data(
        p_batch_id          IN  VARCHAR2,       -- 批次号
        p_merge_mode        IN  VARCHAR2,       -- FULL/INCREMENTAL
        p_enable_scd2       IN  INTEGER,        -- 1=启用SCD2历史追踪
        p_max_conflict      IN  INTEGER,        -- 最大允许冲突数
        p_reject_on_error   IN  INTEGER,        -- 1=错误时拒绝，0=跳过并记录
        p_audit_id          OUT INTEGER         -- 返回审计ID
    );

    -- 辅助函数：计算记录哈希
    FUNCTION func_calc_record_hash(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_amount        IN NUMERIC,
        p_quantity      IN INTEGER,
        p_region        IN VARCHAR2,
        p_channel       IN VARCHAR2,
        p_discount      IN NUMERIC
    ) RETURN VARCHAR2;

    -- 辅助函数：查找维度代理键
    FUNCTION func_lookup_dimensions(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_region_code   IN VARCHAR2,
        p_sales_rep     IN VARCHAR2,
        p_sales_date    IN DATE
    ) RETURN rec_dimension_keys;

    -- 辅助过程：处理SCD2历史记录
    PROCEDURE proc_handle_scd2(
        p_transaction_id    IN VARCHAR2,
        p_new_sk_sales      IN INTEGER,
        p_effective_date    IN DATE
    );

    -- 辅助过程：记录冲突
    PROCEDURE proc_log_conflict(
        p_batch_id      IN VARCHAR2,
        p_transaction_id IN VARCHAR2,
        p_conflict_type IN VARCHAR2,
        p_resolution    IN VARCHAR2,
        p_old_hash      IN VARCHAR2,
        p_new_hash      IN VARCHAR2,
        p_old_amount    IN NUMERIC,
        p_new_amount    IN NUMERIC,
        p_note          IN VARCHAR2
    );

    -- 辅助过程：记录拒绝数据
    PROCEDURE proc_reject_record(
        p_batch_id      IN VARCHAR2,
        p_transaction_id IN VARCHAR2,
        p_reason        IN VARCHAR2,
        p_detail        IN VARCHAR2,
        p_raw_data      IN VARCHAR2
    );

END pkg_merge_sales;
/

CREATE OR REPLACE PACKAGE BODY pkg_merge_sales AS

    -- ==========================================
    -- 函数：计算记录哈希（MD5模拟）
    -- ==========================================
    FUNCTION func_calc_record_hash(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_amount        IN NUMERIC,
        p_quantity      IN INTEGER,
        p_region        IN VARCHAR2,
        p_channel       IN VARCHAR2,
        p_discount      IN NUMERIC
    ) RETURN VARCHAR2 IS
    BEGIN
        RETURN MD5(
            p_product_code || '|' || p_customer_code || '|' ||
            TO_CHAR(p_amount) || '|' || TO_CHAR(p_quantity) || '|' ||
            p_region || '|' || p_channel || '|' || TO_CHAR(p_discount)
        );
    END func_calc_record_hash;

    -- ==========================================
    -- 函数：查找维度代理键
    -- ==========================================
    FUNCTION func_lookup_dimensions(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_region_code   IN VARCHAR2,
        p_sales_rep     IN VARCHAR2,
        p_sales_date    IN DATE
    ) RETURN rec_dimension_keys IS
        v_keys rec_dimension_keys;
    BEGIN
        -- 查找产品代理键
        SELECT sk_product INTO v_keys.sk_product
        FROM dim_product
        WHERE bk_product_code = p_product_code AND is_active = 1;

        -- 查找客户代理键
        SELECT sk_customer INTO v_keys.sk_customer
        FROM dim_customer
        WHERE bk_customer_code = p_customer_code AND is_active = 1;

        -- 查找地区代理键
        SELECT sk_region INTO v_keys.sk_region
        FROM dim_region
        WHERE bk_region_code = p_region_code;

        -- 查找销售代表代理键
        SELECT sk_sales_rep INTO v_keys.sk_sales_rep
        FROM dim_sales_rep
        WHERE bk_sales_rep_code = p_sales_rep AND is_active = 1;

        -- 查找日期代理键
        SELECT sk_date INTO v_keys.sk_date
        FROM dim_date
        WHERE full_date = p_sales_date;

        RETURN v_keys;

    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            -- 维度键找不到，返回NULL，后续处理
            RETURN v_keys;
        WHEN OTHERS THEN
            RETURN v_keys;
    END func_lookup_dimensions;

    -- ==========================================
    -- 过程：记录冲突
    -- ==========================================
    PROCEDURE proc_log_conflict(
        p_batch_id      IN VARCHAR2,
        p_transaction_id IN VARCHAR2,
        p_conflict_type IN VARCHAR2,
        p_resolution    IN VARCHAR2,
        p_old_hash      IN VARCHAR2,
        p_new_hash      IN VARCHAR2,
        p_old_amount    IN NUMERIC,
        p_new_amount    IN NUMERIC,
        p_note          IN VARCHAR2
    ) IS
    BEGIN
        INSERT INTO merge_conflict_log (
            log_id, merge_batch_id, transaction_id, conflict_type,
            resolution, old_hash, new_hash, old_amount, new_amount, resolution_note
        ) VALUES (
            seq_conflict_log.NEXTVAL, p_batch_id, p_transaction_id, p_conflict_type,
            p_resolution, p_old_hash, p_new_hash, p_old_amount, p_new_amount, p_note
        );
    END proc_log_conflict;

    -- ==========================================
    -- 过程：记录拒绝数据
    -- ==========================================
    PROCEDURE proc_reject_record(
        p_batch_id      IN VARCHAR2,
        p_transaction_id IN VARCHAR2,
        p_reason        IN VARCHAR2,
        p_detail        IN VARCHAR2,
        p_raw_data      IN VARCHAR2
    ) IS
    BEGIN
        INSERT INTO reject_data (
            reject_id, batch_id, transaction_id, reject_reason, reject_detail, raw_data
        ) VALUES (
            seq_reject.NEXTVAL, p_batch_id, p_transaction_id, p_reason, p_detail, p_raw_data
        );
    END proc_reject_record;

    -- ==========================================
    -- 过程：处理SCD2历史记录
    -- ==========================================
    PROCEDURE proc_handle_scd2(
        p_transaction_id    IN VARCHAR2,
        p_new_sk_sales      IN INTEGER,
        p_effective_date    IN DATE
    ) IS
    BEGIN
        -- 关闭旧记录：设置失效日期和is_current=0
        UPDATE dw_sales_fact
        SET expiry_date = p_effective_date - INTERVAL '1' DAY,
            is_current = 0,
            dw_update_time = SYSTIMESTAMP,
            dw_updated_by = CURRENT_USER
        WHERE bk_transaction_id = p_transaction_id
          AND is_current = 1;

        -- 记录CDC变更
        INSERT INTO cdc_change_log (change_id, batch_id, transaction_id, change_type, change_time)
        VALUES (seq_cdc.NEXTVAL, 'SCD2_' || TO_CHAR(SYSTIMESTAMP, 'YYYYMMDD'),
                p_transaction_id, 'UPDATE', SYSTIMESTAMP);
    END proc_handle_scd2;

    -- ==========================================
    -- 主过程：复杂 MERGE INTO ETL
    -- ==========================================
    PROCEDURE proc_merge_sales_data(
        p_batch_id          IN  VARCHAR2,
        p_merge_mode        IN  VARCHAR2,
        p_enable_scd2       IN  INTEGER,
        p_max_conflict      IN  INTEGER,
        p_reject_on_error   IN  INTEGER,
        p_audit_id          OUT INTEGER
    ) IS
        v_start_time        TIMESTAMP := SYSTIMESTAMP;
        v_src_rows          INTEGER := 0;
        v_inserted_rows     INTEGER := 0;
        v_updated_rows      INTEGER := 0;
        v_deleted_rows      INTEGER := 0;
        v_unchanged_rows    INTEGER := 0;
        v_conflict_rows     INTEGER := 0;
        v_error_msg         VARCHAR2(1000);
        v_conflict_limit    INTEGER := 0;

        -- 维度键查找结果
        v_dim_keys          rec_dimension_keys;

        -- 计算字段
        v_unit_price        NUMERIC(18,2);
        v_discount_amount   NUMERIC(18,2);
        v_net_amount        NUMERIC(18,2);
        v_record_hash       VARCHAR2(64);
        v_sk_date           INTEGER;

        -- 用于MERGE的临时表（高斯MERGE INTO限制处理）
        v_temp_table        VARCHAR2(30) := 'tmp_merge_' || REPLACE(p_batch_id, '_', '');

    BEGIN
        -- 1. 初始化审计记录
        INSERT INTO merge_audit (
            audit_id, batch_id, procedure_name, start_time, status
        ) VALUES (
            seq_merge_audit.NEXTVAL, p_batch_id, 'proc_merge_sales_data', v_start_time, 'RUNNING'
        ) RETURNING audit_id INTO p_audit_id;

        -- 2. 数据质量预检查：将源数据加载到临时表并标记问题
        EXECUTE IMMEDIATE 'DROP TABLE IF EXISTS ' || v_temp_table;

        EXECUTE IMMEDIATE '
        CREATE TEMP TABLE ' || v_temp_table || ' AS
        SELECT
            s.*,
            CASE
                WHEN s.sales_amount <= 0 THEN ''INVALID_AMOUNT''
                WHEN s.sales_quantity <= 0 THEN ''INVALID_QUANTITY''
                WHEN s.sales_date > CURRENT_DATE THEN ''FUTURE_DATE''
                WHEN s.discount_rate < 0 OR s.discount_rate > 1 THEN ''INVALID_DISCOUNT''
                WHEN NOT EXISTS (SELECT 1 FROM dim_product p
                                WHERE p.bk_product_code = s.product_code AND p.is_active = 1)
                    THEN ''INVALID_PRODUCT''
                WHEN NOT EXISTS (SELECT 1 FROM dim_customer c
                                WHERE c.bk_customer_code = s.customer_code AND c.is_active = 1)
                    THEN ''INVALID_CUSTOMER''
                ELSE NULL
            END AS reject_reason
        FROM src_sales_data s
        WHERE s.src_batch_id = :1
          AND s.is_deleted = 0'
        USING p_batch_id;

        -- 3. 记录被拒绝的数据
        FOR v_reject IN (
            SELECT transaction_id, reject_reason,
                   product_code || '|' || customer_code || '|' || sales_amount AS raw_data
            FROM ' || v_temp_table || '
            WHERE reject_reason IS NOT NULL
        )
        LOOP
            proc_reject_record(p_batch_id, v_reject.transaction_id,
                              v_reject.reject_reason, 'Pre-merge validation failed',
                              v_reject.raw_data);
            v_src_rows := v_src_rows + 1;
        END LOOP;

        -- 4. 获取有效源数据行数
        EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM ' || v_temp_table || ' WHERE reject_reason IS NULL'
        INTO v_src_rows;

        -- 5. 如果是全量模式，先标记删除（软删除目标表中不在源表的数据）
        IF p_merge_mode = 'FULL' THEN
            UPDATE dw_sales_fact
            SET is_current = 0,
                expiry_date = CURRENT_DATE - INTERVAL '1' DAY,
                dw_update_time = SYSTIMESTAMP,
                dw_updated_by = CURRENT_USER
            WHERE is_current = 1
              AND bk_transaction_id NOT IN (
                  SELECT transaction_id FROM ' || v_temp_table || ' WHERE reject_reason IS NULL
              );

            v_deleted_rows := SQL%ROWCOUNT;

            -- 记录CDC删除
            FOR v_del IN (
                SELECT bk_transaction_id
                FROM dw_sales_fact
                WHERE is_current = 0
                  AND expiry_date = CURRENT_DATE - INTERVAL '1' DAY
                  AND dw_update_time >= v_start_time
            )
            LOOP
                INSERT INTO cdc_change_log (change_id, batch_id, transaction_id, change_type)
                VALUES (seq_cdc.NEXTVAL, p_batch_id, v_del.bk_transaction_id, 'DELETE');
            END LOOP;
        END IF;

        -- 6. ==========================================
        -- 核心：复杂 MERGE INTO 语句
        -- ==========================================

        -- 先为有效源数据计算派生字段和哈希
        EXECUTE IMMEDIATE '
        UPDATE ' || v_temp_table || '
        SET
            unit_price = CASE WHEN sales_quantity > 0
                              THEN sales_amount / sales_quantity
                              ELSE 0 END,
            discount_amount = sales_amount * discount_rate,
            net_amount = sales_amount * (1 - discount_rate),
            record_hash = MD5(product_code || ''|'' || customer_code || ''|'' ||
                             TO_CHAR(sales_amount) || ''|'' || TO_CHAR(sales_quantity))
        WHERE reject_reason IS NULL';

        -- 执行 MERGE INTO（使用临时表作为源）
        -- 高斯 MERGE INTO 语法：MERGE INTO target USING source ON condition
        -- WHEN MATCHED THEN UPDATE ...
        -- WHEN NOT MATCHED THEN INSERT ...

        MERGE INTO dw_sales_fact tgt
        USING (
            SELECT
                t.transaction_id,
                t.product_code,
                t.customer_code,
                t.sales_amount,
                t.sales_quantity,
                t.sales_date,
                t.region_code,
                t.channel_type,
                t.sales_rep_code,
                t.discount_rate,
                t.payment_method,
                t.delivery_status,
                t.data_source,
                t.src_batch_id,
                t.src_sequence,
                t.record_hash,
                t.unit_price,
                t.discount_amount,
                t.net_amount,
                -- 查找维度代理键
                p.sk_product,
                c.sk_customer,
                r.sk_region,
                sr.sk_sales_rep,
                d.sk_date
            FROM ' || v_temp_table || ' t
            LEFT JOIN dim_product p ON t.product_code = p.bk_product_code AND p.is_active = 1
            LEFT JOIN dim_customer c ON t.customer_code = c.bk_customer_code AND c.is_active = 1
            LEFT JOIN dim_region r ON t.region_code = r.bk_region_code
            LEFT JOIN dim_sales_rep sr ON t.sales_rep_code = sr.bk_sales_rep_code AND sr.is_active = 1
            LEFT JOIN dim_date d ON t.sales_date = d.full_date
            WHERE t.reject_reason IS NULL
        ) src
        ON (tgt.bk_transaction_id = src.transaction_id AND tgt.is_current = 1)

        -- MATCHED：记录已存在，检查是否需要更新
        WHEN MATCHED THEN
            UPDATE SET
                -- 只有当哈希值变化时才更新（避免无意义更新）
                sales_amount = CASE
                    WHEN tgt.record_hash != src.record_hash THEN src.sales_amount
                    ELSE tgt.sales_amount
                END,
                sales_quantity = CASE WHEN tgt.record_hash != src.record_hash THEN src.sales_quantity ELSE tgt.sales_quantity END,
                unit_price = CASE WHEN tgt.record_hash != src.record_hash THEN src.unit_price ELSE tgt.unit_price END,
                discount_amount = CASE WHEN tgt.record_hash != src.record_hash THEN src.discount_amount ELSE tgt.discount_amount END,
                net_amount = CASE WHEN tgt.record_hash != src.record_hash THEN src.net_amount ELSE tgt.net_amount END,
                discount_rate = CASE WHEN tgt.record_hash != src.record_hash THEN src.discount_rate ELSE tgt.discount_rate END,
                delivery_status = src.delivery_status,  -- 状态总是更新
                dw_update_time = SYSTIMESTAMP,
                dw_updated_by = CURRENT_USER,
                -- 如果SCD2启用且关键字段变化，触发历史记录
                is_current = CASE
                    WHEN p_enable_scd2 = 1 AND tgt.record_hash != src.record_hash THEN 0
                    ELSE tgt.is_current
                END,
                expiry_date = CASE
                    WHEN p_enable_scd2 = 1 AND tgt.record_hash != src.record_hash THEN src.sales_date - INTERVAL '1' DAY
                    ELSE tgt.expiry_date
                END
            WHERE tgt.record_hash != src.record_hash  -- 只有变化了才执行UPDATE
               OR tgt.delivery_status != src.delivery_status  -- 或状态变化

        -- NOT MATCHED：新记录，直接插入
        WHEN NOT MATCHED THEN
            INSERT (
                sk_sales_id, bk_transaction_id,
                sk_product, sk_customer, sk_region, sk_sales_rep, sk_date,
                sales_amount, sales_quantity, unit_price, discount_amount, net_amount,
                region_code, channel_type, payment_method, delivery_status,
                data_source, src_batch_id, src_sequence, record_hash,
                effective_date, expiry_date, is_current, version_number,
                dw_create_time, dw_created_by
            )
            VALUES (
                seq_dw_sales.NEXTVAL, src.transaction_id,
                src.sk_product, src.sk_customer, src.sk_region, src.sk_sales_rep, src.sk_date,
                src.sales_amount, src.sales_quantity, src.unit_price, src.discount_amount, src.net_amount,
                src.region_code, src.channel_type, src.payment_method, src.delivery_status,
                src.data_source, src.src_batch_id, src.src_sequence, src.record_hash,
                src.sales_date, DATE '9999-12-31', 1, 1,
                SYSTIMESTAMP, CURRENT_USER
            );

        -- 获取MERGE结果统计
        v_inserted_rows := SQL%ROWCOUNT;  -- 这个在高斯中需要特殊处理，实际应分别统计

        -- 7. 处理SCD2：为被关闭的旧记录插入新版本
        IF p_enable_scd2 = 1 THEN
            FOR v_scd IN (
                SELECT
                    src.transaction_id,
                    src.sk_product, src.sk_customer, src.sk_region,
                    src.sk_sales_rep, src.sk_date,
                    src.sales_amount, src.sales_quantity, src.unit_price,
                    src.discount_amount, src.net_amount,
                    src.region_code, src.channel_type, src.payment_method,
                    src.delivery_status, src.data_source,
                    src.src_batch_id, src.src_sequence, src.record_hash,
                    src.sales_date
                FROM ' || v_temp_table || ' src
                JOIN dw_sales_fact tgt ON src.transaction_id = tgt.bk_transaction_id
                WHERE src.reject_reason IS NULL
                  AND tgt.is_current = 0  -- 被MERGE关闭的旧记录
                  AND tgt.dw_update_time >= v_start_time  -- 本次MERGE更新的
            )
            LOOP
                -- 插入新版本
                INSERT INTO dw_sales_fact (
                    sk_sales_id, bk_transaction_id,
                    sk_product, sk_customer, sk_region, sk_sales_rep, sk_date,
                    sales_amount, sales_quantity, unit_price, discount_amount, net_amount,
                    region_code, channel_type, payment_method, delivery_status,
                    data_source, src_batch_id, src_sequence, record_hash,
                    effective_date, expiry_date, is_current, version_number,
                    dw_create_time, dw_created_by
                ) VALUES (
                    seq_dw_sales.NEXTVAL, v_scd.transaction_id,
                    v_scd.sk_product, v_scd.sk_customer, v_scd.sk_region,
                    v_scd.sk_sales_rep, v_scd.sk_date,
                    v_scd.sales_amount, v_scd.sales_quantity, v_scd.unit_price,
                    v_scd.discount_amount, v_scd.net_amount,
                    v_scd.region_code, v_scd.channel_type, v_scd.payment_method,
                    v_scd.delivery_status, v_scd.data_source,
                    v_scd.src_batch_id, v_scd.src_sequence, v_scd.record_hash,
                    v_scd.sales_date, DATE '9999-12-31', 1,
                    (SELECT NVL(MAX(version_number), 0) + 1
                     FROM dw_sales_fact
                     WHERE bk_transaction_id = v_scd.transaction_id),
                    SYSTIMESTAMP, CURRENT_USER
                );

                v_updated_rows := v_updated_rows + 1;
            END LOOP;
        END IF;

        -- 8. 统计各类操作的真实行数（高斯MERGE INTO的SQL%ROWCOUNT行为）
        -- 分别统计INSERT和UPDATE
        SELECT COUNT(*) INTO v_inserted_rows
        FROM dw_sales_fact
        WHERE src_batch_id = p_batch_id
          AND dw_create_time >= v_start_time
          AND is_current = 1;

        SELECT COUNT(*) INTO v_updated_rows
        FROM dw_sales_fact
        WHERE dw_update_time >= v_start_time
          AND is_current = 0;  -- 被更新的旧记录

        SELECT COUNT(*) INTO v_unchanged_rows
        FROM ' || v_temp_table || ' src
        JOIN dw_sales_fact tgt ON src.transaction_id = tgt.bk_transaction_id
        WHERE src.reject_reason IS NULL
          AND tgt.is_current = 1
          AND tgt.record_hash = src.record_hash
          AND tgt.delivery_status = src.delivery_status;

        -- 9. 冲突检查：同一批次内重复transaction_id
        FOR v_dup IN (
            SELECT transaction_id, COUNT(*) AS cnt
            FROM ' || v_temp_table || '
            WHERE reject_reason IS NULL
            GROUP BY transaction_id
            HAVING COUNT(*) > 1
        )
        LOOP
            v_conflict_rows := v_conflict_rows + 1;
            proc_log_conflict(p_batch_id, v_dup.transaction_id, 'DUPLICATE_KEY', 'REJECT',
                             NULL, NULL, NULL, NULL,
                             'Duplicate in source: ' || v_dup.cnt || ' occurrences');

            IF v_conflict_rows > p_max_conflict THEN
                RAISE_APPLICATION_ERROR(-20001, 'Conflict limit exceeded: ' || v_conflict_rows);
            END IF;
        END LOOP;

        -- 10. 记录CDC变更日志
        FOR v_cdc IN (
            SELECT transaction_id, record_hash
            FROM ' || v_temp_table || '
            WHERE reject_reason IS NULL
        )
        LOOP
            INSERT INTO cdc_change_log (change_id, batch_id, transaction_id, change_type, new_hash)
            VALUES (seq_cdc.NEXTVAL, p_batch_id, v_cdc.transaction_id,
                    CASE WHEN EXISTS (SELECT 1 FROM dw_sales_fact
                                     WHERE bk_transaction_id = v_cdc.transaction_id
                                       AND dw_create_time >= v_start_time)
                         THEN 'INSERT' ELSE 'UPDATE' END,
                    v_cdc.record_hash);
        END LOOP;

        -- 11. 更新审计记录
        UPDATE merge_audit
        SET end_time = SYSTIMESTAMP,
            status = 'SUCCESS',
            src_rows = v_src_rows,
            inserted_rows = v_inserted_rows,
            updated_rows = v_updated_rows,
            deleted_rows = v_deleted_rows,
            unchanged_rows = v_unchanged_rows,
            conflict_rows = v_conflict_rows
        WHERE audit_id = p_audit_id;

        COMMIT;

        -- 12. 清理临时表
        EXECUTE IMMEDIATE 'DROP TABLE IF EXISTS ' || v_temp_table;

    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            v_error_msg := SQLERRM;

            UPDATE merge_audit
            SET end_time = SYSTIMESTAMP,
                status = 'FAILED',
                error_message = v_error_msg
            WHERE audit_id = p_audit_id;

            COMMIT;

            -- 清理临时表
            EXECUTE IMMEDIATE 'DROP TABLE IF EXISTS ' || v_temp_table;

            RAISE;
    END proc_merge_sales_data;

END pkg_merge_sales;
/

-- ============================================
-- 第五部分：测试调用
-- ============================================

-- 1. 初始全量加载
DECLARE
    v_audit_id INTEGER;
BEGIN
    pkg_merge_sales.proc_merge_sales_data(
        p_batch_id        => 'BATCH_202401',
        p_merge_mode      => 'FULL',
        p_enable_scd2     => 1,
        p_max_conflict    => 100,
        p_reject_on_error => 0,
        p_audit_id        => v_audit_id
    );
    DBE_OUTPUT.PRINT_LINE('Initial load audit_id: ' || v_audit_id);
END;
/

-- 2. 准备增量数据（模拟变更）
INSERT INTO src_sales_data (src_batch_id, src_sequence, transaction_id, product_code, customer_code,
    sales_amount, sales_quantity, sales_date, region_code, channel_type, sales_rep_code,
    discount_rate, payment_method, delivery_status, data_source, record_hash, src_create_time) VALUES
-- 新增记录
('BATCH_202402', 1, 'TXN_006', 'P001', 'C005', 50000, 2, '2024-04-05', 'WEST', 'ONLINE', 'R005', 0.12, 'WECHAT_PAY', 'PENDING', 'WEB', 'hash006', SYSTIMESTAMP),
('BATCH_202402', 2, 'TXN_007', 'P003', 'C002', 90000, 2, '2024-05-01', 'NORTH', 'OFFLINE', 'R002', 0.08, 'BANK_TRANSFER', 'SHIPPED', 'POS', 'hash007', SYSTIMESTAMP),
-- 变更记录（TXN_001金额变化，触发SCD2）
('BATCH_202402', 3, 'TXN_001', 'P001', 'C001', 275000, 10, '2024-01-15', 'EAST', 'ONLINE', 'R001', 0.10, 'BANK_TRANSFER', 'DELIVERED', 'CRM', 'hash001_new', SYSTIMESTAMP),
-- 状态变更（TXN_003从PENDING变为DELIVERED）
('BATCH_202402', 4, 'TXN_003', 'P003', 'C001', 450000, 10, '2024-02-20', 'EAST', 'ONLINE', 'R001', 0.15, 'BANK_TRANSFER', 'DELIVERED', 'WEB', 'hash003', SYSTIMESTAMP);

COMMIT;

-- 3. 增量加载
DECLARE
    v_audit_id INTEGER;
BEGIN
    pkg_merge_sales.proc_merge_sales_data(
        p_batch_id        => 'BATCH_202402',
        p_merge_mode      => 'INCREMENTAL',
        p_enable_scd2     => 1,
        p_max_conflict    => 100,
        p_reject_on_error => 0,
        p_audit_id        => v_audit_id
    );
    DBE_OUTPUT.PRINT_LINE('Incremental load audit_id: ' || v_audit_id);
END;
/

-- 4. 验证结果
SELECT
    'Total records' AS metric, COUNT(*) AS cnt FROM dw_sales_fact
UNION ALL
SELECT 'Current records', COUNT(*) FROM dw_sales_fact WHERE is_current = 1
UNION ALL
SELECT 'Historical records', COUNT(*) FROM dw_sales_fact WHERE is_current = 0
UNION ALL
SELECT 'SCD2 versions (TXN_001)', COUNT(*) FROM dw_sales_fact WHERE bk_transaction_id = 'TXN_001'
UNION ALL
SELECT 'Rejected records', COUNT(*) FROM reject_data
UNION ALL
SELECT 'Conflict logs', COUNT(*) FROM merge_conflict_log
UNION ALL
SELECT 'CDC changes', COUNT(*) FROM cdc_change_log;

-- 5. 查看TXN_001的SCD2历史
SELECT
    sk_sales_id,
    bk_transaction_id,
    sales_amount,
    net_amount,
    delivery_status,
    effective_date,
    expiry_date,
    is_current,
    version_number,
    dw_create_time
FROM dw_sales_fact
WHERE bk_transaction_id = 'TXN_001'
ORDER BY version_number;

-- 6. 查看审计日志
SELECT * FROM merge_audit ORDER BY start_time DESC;
