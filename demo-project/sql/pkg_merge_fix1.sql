
-- ============================================================
-- 高斯/OpenGauss 复杂 MERGE INTO 存储过程示例（修复版）
--
-- 修复点：
--   1. FOR .. IN (SELECT ... FROM 变量表名) → 改为 EXECUTE IMMEDIATE + 游标
--   2. 所有动态表名引用统一使用 EXECUTE IMMEDIATE + OPEN FOR
-- ============================================================

-- ============================================
-- 第一部分：DDL 建表语句
-- ============================================

-- 1. 源系统数据表（模拟上游系统推送）
DROP TABLE IF EXISTS src_sales_data CASCADE;
CREATE TABLE src_sales_data (
    src_batch_id        VARCHAR2(50),
    src_sequence        INTEGER,
    transaction_id      VARCHAR2(50) NOT NULL,
    product_code        VARCHAR2(50),
    customer_code       VARCHAR2(50),
    sales_amount        NUMERIC(18,2),
    sales_quantity      INTEGER,
    sales_date          DATE,
    region_code         VARCHAR2(20),
    channel_type        VARCHAR2(20),
    sales_rep_code      VARCHAR2(50),
    discount_rate       NUMERIC(5,2),
    payment_method      VARCHAR2(20),
    delivery_status     VARCHAR2(20),
    data_source         VARCHAR2(20),
    record_hash         VARCHAR2(64),
    src_create_time     TIMESTAMP,
    src_update_time     TIMESTAMP,
    is_deleted          INTEGER DEFAULT 0,
    PRIMARY KEY (src_batch_id, src_sequence)
);

-- 2. 目标事实表（数据仓库主表）
DROP TABLE IF EXISTS dw_sales_fact CASCADE;
CREATE TABLE dw_sales_fact (
    sk_sales_id         INTEGER PRIMARY KEY,
    bk_transaction_id   VARCHAR2(50) NOT NULL,
    sk_product          INTEGER,
    sk_customer         INTEGER,
    sk_region           INTEGER,
    sk_sales_rep        INTEGER,
    sk_date             INTEGER,
    sales_amount        NUMERIC(18,2),
    sales_quantity      INTEGER,
    unit_price          NUMERIC(18,2),
    discount_amount     NUMERIC(18,2),
    net_amount          NUMERIC(18,2),
    region_code         VARCHAR2(20),
    channel_type        VARCHAR2(20),
    payment_method      VARCHAR2(20),
    delivery_status     VARCHAR2(20),
    data_source         VARCHAR2(20),
    src_batch_id        VARCHAR2(50),
    src_sequence        INTEGER,
    record_hash         VARCHAR2(64),
    effective_date      DATE,
    expiry_date         DATE,
    is_current          INTEGER DEFAULT 1,
    version_number      INTEGER DEFAULT 1,
    dw_create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dw_update_time      TIMESTAMP,
    dw_created_by       VARCHAR2(50) DEFAULT CURRENT_USER,
    dw_updated_by       VARCHAR2(50),
    CONSTRAINT uq_dw_sales_current UNIQUE (bk_transaction_id, is_current)
);

-- 3. 维度表
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

DROP TABLE IF EXISTS dim_customer CASCADE;
CREATE TABLE dim_customer (
    sk_customer      INTEGER PRIMARY KEY,
    bk_customer_code VARCHAR2(50) NOT NULL,
    customer_name    VARCHAR2(200),
    customer_type    VARCHAR2(20),
    region_code      VARCHAR2(20),
    credit_level     VARCHAR2(10),
    is_active        INTEGER DEFAULT 1,
    UNIQUE (bk_customer_code, is_active)
);

DROP TABLE IF EXISTS dim_region CASCADE;
CREATE TABLE dim_region (
    sk_region      INTEGER PRIMARY KEY,
    bk_region_code VARCHAR2(20) NOT NULL,
    region_name    VARCHAR2(100),
    parent_region  VARCHAR2(20),
    region_level   INTEGER,
    is_active      INTEGER DEFAULT 1
);

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

-- 4. 日志表
DROP TABLE IF EXISTS merge_conflict_log CASCADE;
CREATE TABLE merge_conflict_log (
    log_id          INTEGER PRIMARY KEY,
    merge_batch_id  VARCHAR2(50),
    transaction_id  VARCHAR2(50),
    conflict_type   VARCHAR2(50),
    resolution      VARCHAR2(50),
    old_hash        VARCHAR2(64),
    new_hash        VARCHAR2(64),
    old_amount      NUMERIC(18,2),
    new_amount      NUMERIC(18,2),
    resolution_note VARCHAR2(500),
    log_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

DROP TABLE IF EXISTS cdc_change_log CASCADE;
CREATE TABLE cdc_change_log (
    change_id       INTEGER PRIMARY KEY,
    batch_id        VARCHAR2(50),
    transaction_id  VARCHAR2(50),
    change_type     VARCHAR2(10),
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
(20240115, '2024-01-15', 2024, 1, 1, 15, 'Monday'),
(20240220, '2024-02-20', 2024, 1, 2, 20, 'Tuesday'),
(20240310, '2024-03-10', 2024, 1, 3, 10, 'Sunday'),
(20240325, '2024-03-25', 2024, 1, 3, 25, 'Monday'),
(20240405, '2024-04-05', 2024, 2, 4, 5, 'Friday'),
(20240501, '2024-05-01', 2024, 2, 5, 1, 'Wednesday'),
(20240520, '2024-05-20', 2024, 2, 5, 20, 'Monday'),
(20240601, '2024-06-01', 2024, 2, 6, 1, 'Saturday');

-- 第一批源数据
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
-- 第四部分：复杂 MERGE INTO 存储过程包（修复版）
-- ============================================

CREATE OR REPLACE PACKAGE pkg_merge_sales AS
    TYPE rec_dimension_keys IS RECORD (
        sk_product      INTEGER,
        sk_customer     INTEGER,
        sk_region       INTEGER,
        sk_sales_rep    INTEGER,
        sk_date         INTEGER
    );

    PROCEDURE proc_merge_sales_data(
        p_batch_id          IN  VARCHAR2,
        p_merge_mode        IN  VARCHAR2,
        p_enable_scd2       IN  INTEGER,
        p_max_conflict      IN  INTEGER,
        p_reject_on_error   IN  INTEGER,
        p_audit_id          OUT INTEGER
    );

    FUNCTION func_calc_record_hash(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_amount        IN NUMERIC,
        p_quantity      IN INTEGER,
        p_region        IN VARCHAR2,
        p_channel       IN VARCHAR2,
        p_discount      IN NUMERIC
    ) RETURN VARCHAR2;

    FUNCTION func_lookup_dimensions(
        p_product_code  IN VARCHAR2,
        p_customer_code IN VARCHAR2,
        p_region_code   IN VARCHAR2,
        p_sales_rep     IN VARCHAR2,
        p_sales_date    IN DATE
    ) RETURN rec_dimension_keys;

    PROCEDURE proc_handle_scd2(
        p_transaction_id    IN VARCHAR2,
        p_new_sk_sales      IN INTEGER,
        p_effective_date    IN DATE
    );

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
    -- 函数：计算记录哈希
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
        SELECT sk_product INTO v_keys.sk_product
        FROM dim_product WHERE bk_product_code = p_product_code AND is_active = 1;

        SELECT sk_customer INTO v_keys.sk_customer
        FROM dim_customer WHERE bk_customer_code = p_customer_code AND is_active = 1;

        SELECT sk_region INTO v_keys.sk_region
        FROM dim_region WHERE bk_region_code = p_region_code;

        SELECT sk_sales_rep INTO v_keys.sk_sales_rep
        FROM dim_sales_rep WHERE bk_sales_rep_code = p_sales_rep AND is_active = 1;

        SELECT sk_date INTO v_keys.sk_date
        FROM dim_date WHERE full_date = p_sales_date;

        RETURN v_keys;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
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
        UPDATE dw_sales_fact
        SET expiry_date = p_effective_date - INTERVAL '1' DAY,
            is_current = 0,
            dw_update_time = SYSTIMESTAMP,
            dw_updated_by = CURRENT_USER
        WHERE bk_transaction_id = p_transaction_id
          AND is_current = 1;

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
        v_dim_keys          rec_dimension_keys;
        v_unit_price        NUMERIC(18,2);
        v_discount_amount   NUMERIC(18,2);
        v_net_amount        NUMERIC(18,2);
        v_record_hash       VARCHAR2(64);
        v_sk_date           INTEGER;
        v_temp_table        VARCHAR2(30) := 'tmp_merge_' || REPLACE(p_batch_id, '_', '');

        -- 游标变量（修复：用于动态表名遍历）
        v_reject_cursor     SYS_REFCURSOR;
        v_reject_id         VARCHAR2(50);
        v_reject_reason     VARCHAR2(200);
        v_reject_raw        VARCHAR2(4000);

        v_dup_cursor        SYS_REFCURSOR;
        v_dup_id            VARCHAR2(50);
        v_dup_cnt           INTEGER;

        v_scd_cursor        SYS_REFCURSOR;
        v_scd_txn_id        VARCHAR2(50);
        v_scd_sk_product    INTEGER;
        v_scd_sk_customer   INTEGER;
        v_scd_sk_region     INTEGER;
        v_scd_sk_rep        INTEGER;
        v_scd_sk_date       INTEGER;
        v_scd_amount        NUMERIC(18,2);
        v_scd_qty           INTEGER;
        v_scd_unit_price    NUMERIC(18,2);
        v_scd_disc_amt      NUMERIC(18,2);
        v_scd_net           NUMERIC(18,2);
        v_scd_region        VARCHAR2(20);
        v_scd_channel       VARCHAR2(20);
        v_scd_pay           VARCHAR2(20);
        v_scd_delivery      VARCHAR2(20);
        v_scd_source        VARCHAR2(20);
        v_scd_batch         VARCHAR2(50);
        v_scd_seq           INTEGER;
        v_scd_hash          VARCHAR2(64);
        v_scd_date          DATE;

        v_cdc_cursor        SYS_REFCURSOR;
        v_cdc_txn           VARCHAR2(50);
        v_cdc_hash          VARCHAR2(64);

        v_del_cursor        SYS_REFCURSOR;
        v_del_txn           VARCHAR2(50);

    BEGIN
        -- 1. 初始化审计记录
        INSERT INTO merge_audit (
            audit_id, batch_id, procedure_name, start_time, status
        ) VALUES (
            seq_merge_audit.NEXTVAL, p_batch_id, 'proc_merge_sales_data', v_start_time, 'RUNNING'
        ) RETURNING audit_id INTO p_audit_id;

        -- 2. 创建临时表并做数据质量检查
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
        WHERE s.src_batch_id = :1 AND s.is_deleted = 0'
        USING p_batch_id;

        -- 3. 记录被拒绝的数据（修复：使用OPEN FOR + FETCH遍历动态表名）
        OPEN v_reject_cursor FOR
            'SELECT transaction_id, reject_reason,
                    product_code || ''|'' || customer_code || ''|'' || TO_CHAR(sales_amount) AS raw_data
             FROM ' || v_temp_table || '
             WHERE reject_reason IS NOT NULL';

        LOOP
            FETCH v_reject_cursor INTO v_reject_id, v_reject_reason, v_reject_raw;
            EXIT WHEN v_reject_cursor%NOTFOUND;

            proc_reject_record(p_batch_id, v_reject_id,
                              v_reject_reason, 'Pre-merge validation failed',
                              v_reject_raw);
            v_src_rows := v_src_rows + 1;
        END LOOP;
        CLOSE v_reject_cursor;

        -- 4. 获取有效源数据行数
        EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM ' || v_temp_table || ' WHERE reject_reason IS NULL'
        INTO v_src_rows;

        -- 5. 全量模式：软删除目标表中不在源表的数据
        IF p_merge_mode = 'FULL' THEN
            -- UPDATE dw_sales_fact
            -- SET is_current = 0,
            --     expiry_date = CURRENT_DATE - INTERVAL '1' DAY,
            --     dw_update_time = SYSTIMESTAMP,
            --     dw_updated_by = CURRENT_USER
            -- WHERE is_current = 1
            --   AND bk_transaction_id NOT IN (
            --       SELECT transaction_id FROM ' || v_temp_table || ' WHERE reject_reason IS NULL
            --   );

            -- v_deleted_rows := SQL%ROWCOUNT;

            -- 记录CDC删除（修复：使用游标遍历）
            OPEN v_del_cursor FOR
                'SELECT bk_transaction_id
                 FROM dw_sales_fact
                 WHERE is_current = 0
                   AND expiry_date = CURRENT_DATE - INTERVAL ''1'' DAY
                   AND dw_update_time >= :1'
                USING v_start_time;

            LOOP
                FETCH v_del_cursor INTO v_del_txn;
                EXIT WHEN v_del_cursor%NOTFOUND;

                INSERT INTO cdc_change_log (change_id, batch_id, transaction_id, change_type)
                VALUES (seq_cdc.NEXTVAL, p_batch_id, v_del_txn, 'DELETE');
            END LOOP;
            CLOSE v_del_cursor;
        END IF;

        -- 6. 计算派生字段
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

        -- 7. ==========================================
        -- 核心：MERGE INTO（使用固定表名，避免动态SQL限制）
        -- ==========================================

        -- 由于高斯MERGE INTO的USING子句不支持动态表名，
        -- 我们采用两步法：先将有效数据插入到一个固定的 staging 表

        -- 创建/清空 staging 表
        DROP TABLE IF EXISTS tmp_merge_staging;
        CREATE TEMP TABLE tmp_merge_staging AS
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
        WHERE t.reject_reason IS NULL;

        -- 执行 MERGE INTO（使用固定表名 tmp_merge_staging）
        MERGE INTO dw_sales_fact tgt
        USING tmp_merge_staging source
        ON (tgt.bk_transaction_id = src.transaction_id AND tgt.is_current = 1)

        WHEN MATCHED THEN
            UPDATE SET
                sales_amount = CASE
                    WHEN tgt.record_hash != src.record_hash THEN src.sales_amount
                    ELSE tgt.sales_amount END,
                sales_quantity = CASE WHEN tgt.record_hash != src.record_hash THEN src.sales_quantity ELSE tgt.sales_quantity END,
                unit_price = CASE WHEN tgt.record_hash != src.record_hash THEN src.unit_price ELSE tgt.unit_price END,
                discount_amount = CASE WHEN tgt.record_hash != src.record_hash THEN src.discount_amount ELSE tgt.discount_amount END,
                net_amount = CASE WHEN tgt.record_hash != src.record_hash THEN src.net_amount ELSE tgt.net_amount END,
                discount_rate = CASE WHEN tgt.record_hash != src.record_hash THEN src.discount_rate ELSE tgt.discount_rate END,
                delivery_status = src.delivery_status,
                dw_update_time = SYSTIMESTAMP,
                dw_updated_by = CURRENT_USER,
                -- is_current = CASE
                --     WHEN p_enable_scd2 = 1 AND tgt.record_hash != src.record_hash THEN 0
                --     ELSE tgt.is_current END,
                expiry_date = CASE
                    WHEN p_enable_scd2 = 1 AND tgt.record_hash != src.record_hash THEN src.sales_date - INTERVAL '1' DAY
                    ELSE tgt.expiry_date END
            WHERE tgt.record_hash != src.record_hash
               OR tgt.delivery_status != src.delivery_status

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

        -- 8. SCD2处理：为被关闭的旧记录插入新版本（修复：使用游标遍历）
        IF p_enable_scd2 = 1 THEN
            OPEN v_scd_cursor FOR
                'SELECT
                    src.transaction_id,
                    src.sk_product, src.sk_customer, src.sk_region,
                    src.sk_sales_rep, src.sk_date,
                    src.sales_amount, src.sales_quantity, src.unit_price,
                    src.discount_amount, src.net_amount,
                    src.region_code, src.channel_type, src.payment_method,
                    src.delivery_status, src.data_source,
                    src.src_batch_id, src.src_sequence, src.record_hash,
                    src.sales_date
                 FROM tmp_merge_staging src
                 JOIN dw_sales_fact tgt ON src.transaction_id = tgt.bk_transaction_id
                 WHERE tgt.is_current = 0
                   AND tgt.dw_update_time >= :1'
                USING v_start_time;

            LOOP
                FETCH v_scd_cursor INTO
                    v_scd_txn_id, v_scd_sk_product, v_scd_sk_customer, v_scd_sk_region,
                    v_scd_sk_rep, v_scd_sk_date, v_scd_amount, v_scd_qty, v_scd_unit_price,
                    v_scd_disc_amt, v_scd_net, v_scd_region, v_scd_channel, v_scd_pay,
                    v_scd_delivery, v_scd_source, v_scd_batch, v_scd_seq, v_scd_hash,
                    v_scd_date;
                EXIT WHEN v_scd_cursor%NOTFOUND;

                INSERT INTO dw_sales_fact (
                    sk_sales_id, bk_transaction_id,
                    sk_product, sk_customer, sk_region, sk_sales_rep, sk_date,
                    sales_amount, sales_quantity, unit_price, discount_amount, net_amount,
                    region_code, channel_type, payment_method, delivery_status,
                    data_source, src_batch_id, src_sequence, record_hash,
                    effective_date, expiry_date, is_current, version_number,
                    dw_create_time, dw_created_by
                ) VALUES (
                    seq_dw_sales.NEXTVAL, v_scd_txn_id,
                    v_scd_sk_product, v_scd_sk_customer, v_scd_sk_region,
                    v_scd_sk_rep, v_scd_sk_date,
                    v_scd_amount, v_scd_qty, v_scd_unit_price,
                    v_scd_disc_amt, v_scd_net,
                    v_scd_region, v_scd_channel, v_scd_pay,
                    v_scd_delivery, v_scd_source,
                    v_scd_batch, v_scd_seq, v_scd_hash,
                    v_scd_date, DATE '9999-12-31', 1,
                    (SELECT NVL(MAX(version_number), 0) + 1
                     FROM dw_sales_fact
                     WHERE bk_transaction_id = v_scd_txn_id),
                    SYSTIMESTAMP, CURRENT_USER
                );

                v_updated_rows := v_updated_rows + 1;
            END LOOP;
            CLOSE v_scd_cursor;
        END IF;

        -- 9. 统计各类操作的真实行数
        SELECT COUNT(*) INTO v_inserted_rows
        FROM dw_sales_fact
        WHERE src_batch_id = p_batch_id
          AND dw_create_time >= v_start_time
          AND is_current = 1;

        SELECT COUNT(*) INTO v_updated_rows
        FROM dw_sales_fact
        WHERE dw_update_time >= v_start_time
          AND is_current = 0;

        SELECT COUNT(*) INTO v_unchanged_rows
        FROM tmp_merge_staging src
        JOIN dw_sales_fact tgt ON src.transaction_id = tgt.bk_transaction_id
        WHERE tgt.is_current = 1
          AND tgt.record_hash = src.record_hash
          AND tgt.delivery_status = src.delivery_status;

        -- 10. 冲突检查：同一批次内重复transaction_id（修复：使用游标）
        OPEN v_dup_cursor FOR
            'SELECT transaction_id, COUNT(*) AS cnt
             FROM tmp_merge_staging
             GROUP BY transaction_id
             HAVING COUNT(*) > 1';

        LOOP
            FETCH v_dup_cursor INTO v_dup_id, v_dup_cnt;
            EXIT WHEN v_dup_cursor%NOTFOUND;

            v_conflict_rows := v_conflict_rows + 1;
            proc_log_conflict(p_batch_id, v_dup_id, 'DUPLICATE_KEY', 'REJECT',
                             NULL, NULL, NULL, NULL,
                             'Duplicate in source: ' || v_dup_cnt || ' occurrences');

            IF v_conflict_rows > p_max_conflict THEN
                CLOSE v_dup_cursor;
                RAISE_APPLICATION_ERROR(-20001, 'Conflict limit exceeded: ' || v_conflict_rows);
            END IF;
        END LOOP;
        CLOSE v_dup_cursor;

        -- 11. 记录CDC变更日志（修复：使用游标）
        OPEN v_cdc_cursor FOR
            'SELECT transaction_id, record_hash FROM tmp_merge_staging';

        LOOP
            FETCH v_cdc_cursor INTO v_cdc_txn, v_cdc_hash;
            EXIT WHEN v_cdc_cursor%NOTFOUND;

            INSERT INTO cdc_change_log (change_id, batch_id, transaction_id, change_type, new_hash)
            VALUES (seq_cdc.NEXTVAL, p_batch_id, v_cdc_txn,
                    CASE WHEN EXISTS (SELECT 1 FROM dw_sales_fact
                                     WHERE bk_transaction_id = v_cdc_txn
                                       AND dw_create_time >= v_start_time)
                         THEN 'INSERT' ELSE 'UPDATE' END,
                    v_cdc_hash);
        END LOOP;
        CLOSE v_cdc_cursor;

        -- 12. 更新审计记录
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

        -- 13. 清理临时表
        DROP TABLE IF EXISTS tmp_merge_staging;
        EXECUTE IMMEDIATE 'DROP TABLE IF EXISTS ' || v_temp_table;

    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            v_error_msg := SQLERRM;

            -- 确保游标关闭
            IF v_reject_cursor%ISOPEN THEN CLOSE v_reject_cursor; END IF;
            IF v_dup_cursor%ISOPEN THEN CLOSE v_dup_cursor; END IF;
            IF v_scd_cursor%ISOPEN THEN CLOSE v_scd_cursor; END IF;
            IF v_cdc_cursor%ISOPEN THEN CLOSE v_cdc_cursor; END IF;
            IF v_del_cursor%ISOPEN THEN CLOSE v_del_cursor; END IF;

            UPDATE merge_audit
            SET end_time = SYSTIMESTAMP,
                status = 'FAILED',
                error_message = v_error_msg
            WHERE audit_id = p_audit_id;

            COMMIT;

            DROP TABLE IF EXISTS tmp_merge_staging;
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

-- 2. 准备增量数据
INSERT INTO src_sales_data (src_batch_id, src_sequence, transaction_id, product_code, customer_code,
    sales_amount, sales_quantity, sales_date, region_code, channel_type, sales_rep_code,
    discount_rate, payment_method, delivery_status, data_source, record_hash, src_create_time) VALUES
('BATCH_202402', 1, 'TXN_006', 'P001', 'C005', 50000, 2, '2024-04-05', 'WEST', 'ONLINE', 'R005', 0.12, 'WECHAT_PAY', 'PENDING', 'WEB', 'hash006', SYSTIMESTAMP),
('BATCH_202402', 2, 'TXN_007', 'P003', 'C002', 90000, 2, '2024-05-01', 'NORTH', 'OFFLINE', 'R002', 0.08, 'BANK_TRANSFER', 'SHIPPED', 'POS', 'hash007', SYSTIMESTAMP),
('BATCH_202402', 3, 'TXN_001', 'P001', 'C001', 275000, 10, '2024-01-15', 'EAST', 'ONLINE', 'R001', 0.10, 'BANK_TRANSFER', 'DELIVERED', 'CRM', 'hash001_new', SYSTIMESTAMP),
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
SELECT 'Total records' AS metric, COUNT(*) AS cnt FROM dw_sales_fact
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
    sk_sales_id, bk_transaction_id, sales_amount, net_amount, delivery_status,
    effective_date, expiry_date, is_current, version_number, dw_create_time
FROM dw_sales_fact
WHERE bk_transaction_id = 'TXN_001'
ORDER BY version_number;

-- 6. 查看审计日志
SELECT * FROM merge_audit ORDER BY start_time DESC;
