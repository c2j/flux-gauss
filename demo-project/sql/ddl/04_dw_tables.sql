-- ============================================================
-- Data warehouse dimension/fact tables (MERGE INTO demo)
-- Auto-consolidated from demo-project/sql/*.sql
-- Each table includes ALL column variants from ALL source files
-- ============================================================

-- Sources: pkg_merge_fix1.sql
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
-- Sources: pkg_merge_fix1.sql
DROP TABLE IF EXISTS dim_region CASCADE;
CREATE TABLE dim_region (
    sk_region      INTEGER PRIMARY KEY,
    bk_region_code VARCHAR2(20) NOT NULL,
    region_name    VARCHAR2(100),
    parent_region  VARCHAR2(20),
    region_level   INTEGER,
    is_active      INTEGER DEFAULT 1
);
-- Sources: pkg_merge_fix1.sql
DROP TABLE IF EXISTS dim_sales_rep CASCADE;
CREATE TABLE dim_sales_rep (
    sk_sales_rep      INTEGER PRIMARY KEY,
    bk_sales_rep_code VARCHAR2(50) NOT NULL,
    rep_name          VARCHAR2(100),
    team_code         VARCHAR2(20),
    hire_date         DATE,
    is_active         INTEGER DEFAULT 1
);
-- Sources: pkg_merge_fix1.sql
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
    dw_updated_by       VARCHAR2(50)
);
-- Sources: pkg_merge_fix1.sql
DROP TABLE IF EXISTS dim_product CASCADE;
CREATE TABLE dim_product (
    sk_product      INTEGER PRIMARY KEY,
    bk_product_code VARCHAR2(50) NOT NULL,
    product_name    VARCHAR2(200),
    category_code   VARCHAR2(20),
    brand_code      VARCHAR2(20),
    unit_cost       NUMERIC(18,2),
    is_active       INTEGER DEFAULT 1
);
-- Sources: pkg_merge_fix1.sql
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
-- Sources: pkg_merge_fix1.sql
DROP TABLE IF EXISTS dim_customer CASCADE;
CREATE TABLE dim_customer (
    sk_customer      INTEGER PRIMARY KEY,
    bk_customer_code VARCHAR2(50) NOT NULL,
    customer_name    VARCHAR2(200),
    customer_type    VARCHAR2(20),
    region_code      VARCHAR2(20),
    credit_level     VARCHAR2(10),
    is_active        INTEGER DEFAULT 1
);
-- Sources: pkg_merge_fix1.sql
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
    is_deleted          INTEGER DEFAULT 0
);
-- Sources: pkg_merge_fix1.sql
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
-- Sources: pkg_merge_fix1.sql
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
-- Sources: pkg_merge_fix1.sql
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
